"""KAP'a erişim için ortak katman.

Detay sayfası, ek dosya indirme ve HTTP yeniden deneme mantığı burada
toplanır; `ratings` ve `documents` modülleri aynı yardımcıları kendi
kopyalarını üretmez.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time

import requests
from pypdf import PdfReader

LOGGER = logging.getLogger(__name__)

KAP_DETAIL_URL = "https://www.kap.org.tr/tr/Bildirim/{index}"
KAP_FILE_URL = "https://www.kap.org.tr/tr/api/file/download/{oid}"

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Referer": "https://www.kap.org.tr/tr/bildirim-sorgu",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


class KapError(RuntimeError):
    """KAP'a ulaşılamadı veya geçici hata verdi."""


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    retries: int = 3,
    delay: float = 0.0,
    headers: dict | None = None,
) -> requests.Response:
    """GET isteğini geçici hatalarda yeniden dener.

    Kalıcı hatalarda (4xx) `requests.HTTPError` yükseltilir; geçici hatalar
    tükenirse `KapError` yükseltilir.
    """
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url, headers=headers or HEADERS, timeout=timeout
            )
        except requests.RequestException as exc:
            last_error = exc
            LOGGER.warning(
                "KAP isteği başarısız (deneme %s/%s): %s", attempt, retries, exc
            )
        else:
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                if delay:
                    time.sleep(delay)
                return response
            last_error = KapError(f"HTTP {response.status_code}")
            LOGGER.warning(
                "KAP geçici hata verdi (deneme %s/%s): HTTP %s",
                attempt,
                retries,
                response.status_code,
            )
        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 6))
    raise KapError(f"KAP'a ulaşılamadı: {last_error}") from last_error


def get_bytes_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    max_bytes: int,
    retries: int = 3,
    delay: float = 0.0,
) -> bytes:
    """Ek dosyasını indirir; boyut sınırı aşılırsa indirmeyi reddeder."""
    response = get_with_retry(
        session, url, timeout=timeout, retries=retries, delay=delay
    )
    if len(response.content) > max_bytes:
        raise KapError("Ek dosya boyut sınırını aşıyor")
    return response.content


def extract_attachments(page_html: str) -> list[dict]:
    """KAP detay sayfasındaki ek dosya listesini çıkarır."""
    flat = page_html.replace('\\"', '"')
    match = re.search(r'"attachments"\s*:\s*(\[.*?\])', flat, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            attachments = [
                item
                for item in parsed
                if isinstance(item, dict) and item.get("objId")
            ]
            if attachments:
                return attachments

    # Yedek: doğrudan indirme bağlantıları.
    fallback: list[dict] = []
    for match in re.finditer(
        r'href="[^"]*?/api/file/download/([a-f0-9]{16,64})"[^>]*>([^<]*)<',
        page_html,
        re.IGNORECASE,
    ):
        fallback.append(
            {"objId": match.group(1), "fileName": match.group(2).strip()}
        )
    return fallback


def pdf_attachments(attachments: list[dict], limit: int) -> list[dict]:
    """Ek listesinden PDF olanları seçer."""
    pdfs = [
        item
        for item in attachments
        if str(item.get("fileExtension") or "").lower() == "pdf"
        or str(item.get("fileName") or "").lower().endswith(".pdf")
    ]
    return pdfs[:limit]


def attachment_fingerprint(attachments: list[dict]) -> tuple[str, ...]:
    """Ek dosyaların kimliği; içerik değiştiğinde önbellek geçersizleşir."""
    return tuple(
        str(item.get("objId") or "") for item in attachments if item.get("objId")
    )


def pdf_text(payload: bytes) -> str:
    """KAP bazen PDF'i Java serileştirme başlığıyla sarar; %PDF'e hizala."""
    start = payload.find(b"%PDF")
    if start > 0:
        payload = payload[start:]
    reader = PdfReader(io.BytesIO(payload), strict=False)
    return "\n".join((page.extract_text() or "") for page in reader.pages)
