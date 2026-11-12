from __future__ import annotations

import html as html_lib
import logging
import re

import requests

from .config import DocumentsConfig
from .kap import (
    KAP_DETAIL_URL,
    KAP_FILE_URL,
    extract_attachments,
    get_bytes_with_retry,
    get_with_retry,
    pdf_attachments,
    pdf_text,
)
from .models import Disclosure
from .normalize import normalize

LOGGER = logging.getLogger(__name__)

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

# Finansal tabloda yüzlerce satır olur; bütçeye sığan anlamlı satırlar
# toplamlar, net kâr ve özkaynak gibi ana kalemlerdir.
AGGREGATE_KEYWORDS = (
    "toplam",
    "net donem",
    "net kar",
    "ozkaynak",
    "net borc",
    "faaliyet brut kar",
    "faaliyet geliri",
    "hasilat",
    "kaynaklar toplami",
    "varliklar toplami",
    "yuksumlulukler toplami",
    "kar veya zarar",
)


def _decode_json_escapes(text: str) -> str:
    """KAP sayfasındaki JSON kaçışlarını (\u003e, \n, \") gerçer metne çevirir."""
    def replace(match: re.Match) -> str:
        return chr(int(match.group(1), 16))

    text = re.sub(r"\\u([0-9a-fA-F]{4})", replace, text)
    text = text.replace("\\n", "\n").replace("\\r", " ").replace("\\t", " ")
    text = text.replace('\\"', '"').replace("\\\\", "\\")
    return text


def _strip_tags(fragment: str) -> str:
    text = _decode_json_escapes(fragment)
    text = html_lib.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    # KAP'ın birleştirme işaretleri gövde metninin önündedir.
    marker = text.rfind("[CONSOLIDATION_METHOD")
    if marker != -1:
        closing = text.find("]", marker)
        if closing != -1:
            text = text[closing + 1 :]
    return " ".join(text.split())


def extract_table_rows(page_html: str) -> list[str]:
    """Finansal rapor tablolarındaki ana satırları çıkarır.

    KAP tablosunda yüzlerce satır ve onlarca sütun vardır; hepsi modele
    gönderilemez. Bu yüzden yalnızca toplamlar, net kâr ve özkaynak gibi
    analistin ihtiyaç duyduğu kalemler, "Etiket: v1 | v2 | v3" biçiminde
    döndürülür. Dönem başlıkları ayrı satırda verilir.
    """
    flat = page_html.replace('\\"', '"')

    # Dönem başlıkları (Cari Dönem 30.09.2026, Önceki Dönem 31.12.2025 ...)
    headers: list[str] = []
    for match in re.finditer(
        r'<td[^>]*class="[^"]*taxonomy-dimensional-header-cell[^"]*"[^>]*>(.*?)</td>',
        flat,
        re.DOTALL,
    ):
        text = _strip_tags(match.group(1))
        if text and text not in headers:
            headers.append(text)

    # Tablo satırları: her satır bir <tr class="...-row-N ..."> bloğudur.
    starts = [
        (match.start(), match.group(1))
        for match in re.finditer(r'<tr class="([^"]*-row-\d+[^"]*)"[^>]*>', flat)
    ]

    rows: list[tuple[str, str]] = []
    for index, (start, _cls) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(flat)
        chunk = flat[start:end]

        title_at = chunk.find("taxonomy-field-title")
        if title_at == -1:
            continue
        # Etiket: başlık hücresindeki Türkçe içerik.
        label_at = chunk.find("content-tr", title_at)
        if label_at == -1:
            continue
        label_end = chunk.find("</div>", label_at)
        label_open = chunk.find(">", label_at)
        if label_open == -1:
            continue
        label = _strip_tags(
            chunk[label_open + 1 : label_end if label_end != -1 else len(chunk)]
        )
        if not label:
            continue

        values: list[str] = []
        position = 0
        while True:
            cell_at = chunk.find("taxonomy-context-value", position)
            if cell_at == -1:
                break
            cell_open = chunk.find(">", cell_at)
            next_at = chunk.find("<td", cell_at + 10)
            cell = chunk[cell_open + 1 : next_at if next_at != -1 else len(chunk)]
            value = _strip_tags(cell)
            if value:
                values.append(value)
            position = cell_at + 10
        if values:
            rows.append((label, " | ".join(values)))

    # Konsolide / konsolide olmayan tablolar aynı etiketi tekrarlar.
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, values in rows:
        key = (label, values)
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)

    picked = [
        (label, values)
        for label, values in unique
        if any(keyword in normalize(label) for keyword in AGGREGATE_KEYWORDS)
    ]
    if len(picked) < 3:
        # Anahtar eşleşmezse ilk satırları ver.
        picked = unique[:12]

    lines: list[str] = []

    # Para birimi ve tablo niteliği ("1.000.000 TL", "Konsolide").
    meta: list[str] = []
    for match in re.finditer(
        r'<table class="financial-header-table"[^>]*>(.*?)</table>', flat, re.DOTALL
    ):
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.DOTALL):
            cells = [
                _strip_tags(cell)
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            ]
            cells = [cell for cell in cells if cell]
            if len(cells) >= 2:
                entry = f"{cells[0]}: {cells[1]}"
                if entry not in meta:
                    meta.append(entry)
    if meta:
        lines.append("Tablo bilgisi: " + " | ".join(meta[:4]))

    if headers:
        lines.append("Dönem sütunları: " + " | ".join(headers[:6]))
    lines.extend(f"{label}: {values}" for label, values in picked)
    return lines


def extract_body_text(page_html: str) -> str:
    """KAP detay sayfasındaki bildirim gövdesini (açıklama metnini) çıkarır.

    Ek dosyası olmayan bildirimlerde sözleşme tutarı, tarih ve koşullar
    yalnızca gövdede yazılıdır; bu metin okunmazsa AI "tutar açıklanmadı"
    der ve önemli bilgi kaybolur.
    """
    flat = page_html.replace('\\"', '"')

    # Türkçe gövde hücreleri ayrıştırılır; İngilizce kopya atlanır.
    cell_pattern = re.compile(
        r'taxonomy-context-value-summernote[^"]*multi-language-content\s+'
        r'content-(tr|en)"[^>]*>(.*?)</td>',
        re.DOTALL,
    )
    turkish: list[str] = []
    for match in cell_pattern.finditer(flat):
        if match.group(1) != "tr":
            continue
        chunk = match.group(2)
        found = False
        for block in re.findall(
            r'<div class="text-block-value"[^>]*>(.*?)</div>', chunk, re.DOTALL
        ):
            text = _strip_tags(block)
            if text:
                turkish.append(text)
                found = True
        if not found:
            # Gövde doğrudan hücre içindeyse.
            text = _strip_tags(chunk)
            if text:
                turkish.append(text)

    # Aynı metin hem TR hem EN hücresinde geçebilir; tekrarları at.
    seen: set[str] = set()
    unique: list[str] = []
    for text in turkish:
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)

    # Finansal raporlarda tablolar gövdeden ayrı durur.
    unique.extend(extract_table_rows(page_html))
    return "\n".join(unique)


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
    # Sayfa kabuğu / betik kalıntıları.
    if any(
        marker in line
        for marker in ("self.__next_f", "notification-body-scale", "$RS(", "[CONSOLIDATION")
    ):
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
        # Sayaç örnek seviyesinde: limit "tek çalışmada" geçerli olsun,
        # e-posta parçaları arasında yeniden sıfırlanmasın.
        self._attempted = 0
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    def fetch(
        self, disclosures: list[Disclosure], skip: set[str] | None = None
    ) -> dict[str, str]:
        if not self.config.enabled or self.config.max_items_per_run <= 0:
            return {}

        skip = skip or set()
        results: dict[str, str] = {}
        for disclosure in disclosures:
            if disclosure.index in skip:
                continue
            cached = self._memo.get(disclosure.index)
            if cached:
                results[disclosure.index] = cached
                continue
            if self._attempted >= self.config.max_items_per_run:
                LOGGER.info(
                    "Belge okuma limiti (%s) doldu; kalanlar sonraki çalışmada",
                    self.config.max_items_per_run,
                )
                break
            self._attempted += 1
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
        page_html = get_with_retry(
            self.session,
            KAP_DETAIL_URL.format(index=disclosure.index),
            timeout=self.config.timeout_seconds,
            delay=self.config.request_delay_seconds,
        ).text
        attachments = extract_attachments(page_html)
        pdfs = pdf_attachments(attachments, self.config.max_attachments)

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
                payload = get_bytes_with_retry(
                    self.session,
                    KAP_FILE_URL.format(oid=oid),
                    timeout=self.config.timeout_seconds,
                    max_bytes=self.config.max_pdf_bytes,
                    delay=self.config.request_delay_seconds,
                )
                raw = pdf_text(payload)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Ek PDF okunamadı (%s): %s", attachment.get("fileName"), exc
                )
                continue
            cleaned = clean_document_text(raw, remaining)
            if cleaned:
                parts.append(cleaned)

        if parts:
            return "\n\n".join(parts).strip()

        # Ek yoksa bildirim gövdesi okunur. Sözleşme tutarı, tarih ve
        # koşullar genellikle yalnızca burada yazılıdır.
        body = clean_document_text(extract_body_text(page_html), budget)
        if body:
            LOGGER.info(
                "Bildirim gövdesi okundu (%s): %s karakter",
                disclosure.index,
                len(body),
            )
        return body