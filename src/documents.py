from __future__ import annotations

import logging
import re
import time

import requests

from .config import DocumentsConfig
from .models import Disclosure
from .normalize import normalize
from .ratings import (
    HEADERS,
    KAP_DETAIL_URL,
    KAP_FILE_URL,
    extract_attachments,
    pdf_text,
)

LOGGER = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# PDF'lerin her sayfasında tekrar eden KAP başlıkları ve tablo döküntüleri
# modele gitmeden önce atılır.
NOISE_LINES = (
    "telif hakki",
    "izinsiz cogaltilmasina",
    "tum haklari saklidir",
    "burada yer alan bilgiler",
    "kap turkiye sermaye piyasalari",
    "kamuyu aydinlatma platformu",
    "sayfa ",
    "page ",
)


def _is_noise(line: str) -> bool:
    low = normalize(line)
    if len(line) < 4:
        return True
    if any(term in low for term in NOISE_LINES):
        return True
    # Sadece sayfa numarası / tarih / noktalama kalıntısı.
    if re.fullmatch(r"[\d\s.,:/\-–—()]+", line):
        return True
    # Tek kelimelik başlık kalıntıları (tablo sütun adı vs.).
    if " " not in line and not any(char.isdigit() for char in line):
        return True
    return False


def clean_document_text(text: str, limit: int) -> str:
    """PDF metnini LLM'e verilecek sade, sınırlı bir metne çevirir."""
    lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = " ".join(raw.split())
        if not line or _is_noise(line):
            continue
        lines.append(line)

    joined = "\n".join(lines)
    if len(joined) <= limit:
        return joined

    cut = joined[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("\n"))
    if boundary > int(limit * 0.6):
        cut = cut[: boundary + 1]
    return cut.rstrip(" ,;:-") + " …"


class DocumentFetcher:
    """Seçili bildirimlerin PDF eklerini indirip metnini çıkarır."""

    def __init__(
        self,
        config: DocumentsConfig,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self._memo: dict[str, str] = {}
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    def fetch(
        self, disclosures: list[Disclosure], skip: set[str] | None = None
    ) -> dict[str, str]:
        if not self.config.enabled or self.config.max_items_per_run <= 0:
            return {}

        skip = skip or set()
        results: dict[str, str] = {}
        attempted = 0
        for disclosure in disclosures:
            if disclosure.index in skip:
                continue
            cached = self._memo.get(disclosure.index)
            if cached:
                results[disclosure.index] = cached
                continue
            if attempted >= self.config.max_items_per_run:
                LOGGER.info(
                    "Belge okuma limiti (%s) doldu; kalanlar sonraki çalışmada",
                    self.config.max_items_per_run,
                )
                break
            attempted += 1
            try:
                text = self._fetch_one(disclosure)
            except Exception as exc:  # noqa: BLE001 - tek bildirim tüm işi durdurmasın
                LOGGER.warning(
                    "Belge okunamadı (%s): %s", disclosure.index, exc
                )
                continue
            if text:
                self._memo[disclosure.index] = text
                results[disclosure.index] = text
        return results

    def _fetch_one(self, disclosure: Disclosure) -> str:
        page_html = self._get(KAP_DETAIL_URL.format(index=disclosure.index)).text
        attachments = extract_attachments(page_html)
        pdfs = [
            item
            for item in attachments
            if str(item.get("fileExtension") or "").lower() == "pdf"
            or str(item.get("fileName") or "").lower().endswith(".pdf")
        ][: self.config.max_attachments]
        if not pdfs:
            return ""

        budget = self.config.max_chars
        parts: list[str] = []
        for attachment in pdfs:
            remaining = budget - sum(len(part) for part in parts)
            if remaining <= 200:
                break
            oid = str(attachment.get("objId") or "")
            if not oid:
                continue
            try:
                payload = self._get_bytes(KAP_FILE_URL.format(oid=oid))
                raw = pdf_text(payload)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Ek PDF okunamadı (%s): %s", attachment.get("fileName"), exc
                )
                continue
            cleaned = clean_document_text(raw, remaining)
            if cleaned:
                parts.append(cleaned)

        return "\n\n".join(parts).strip()

    def _get(self, url: str) -> requests.Response:
        response = self._request(url)
        if self.config.request_delay_seconds:
            time.sleep(self.config.request_delay_seconds)
        return response

    def _get_bytes(self, url: str) -> bytes:
        response = self._get(url)
        if len(response.content) > self.config.max_pdf_bytes:
            raise ValueError("Ek dosya boyut sınırını aşıyor")
        return response.content

    def _request(self, url: str) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(1, 4):
            response = self.session.get(
                url, headers=HEADERS, timeout=self.config.timeout_seconds
            )
            if response.status_code in RETRYABLE_STATUS:
                LOGGER.info(
                    "KAP geçici hata verdi (deneme %s/3): HTTP %s",
                    attempt,
                    response.status_code,
                )
                time.sleep(min(2 ** (attempt - 1), 6))
                continue
            response.raise_for_status()
            return response
        if response is None:
            raise RuntimeError("KAP yanıtı alınamadı")
        response.raise_for_status()
        return response