from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import replace
from datetime import datetime
from pathlib import PurePath

import requests

from .config import RatingsConfig
from .kap import (
    KAP_DETAIL_URL,
    KAP_FILE_URL,
    attachment_fingerprint,
    extract_attachments,
    get_bytes_with_retry,
    get_with_retry,
    pdf_attachments,
    pdf_text,
)
from .models import Disclosure, RatingInfo
from .normalize import normalize
from .state import State

LOGGER = logging.getLogger(__name__)

RATING_SUBJECT_TERMS = (
    "kredi derecelendirmesi",
    "kredi derecelendirme notu",
    "varlik veya ihracci derecelendirme notu",
    "derecelendirme notu",
)

# Uzun/kısa vadeli notlar. "TR D", "J5 (tr)", "CCC (tr)", "D (temerrüt)" gibi
# biçimleri yakalar. Uzun alternatifler önce denenir.
RATING_TOKEN = (
    r"(?:TR\s+)?"
    r"(?:AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|D|J[1-5])"
    r"(?:\s*\([^)\n]{1,24}\))?"
    r"(?![A-Za-z0-9])"
)

CURRENT_PATTERNS = (
    rf"Uzun\s+Vadeli\s+Ulusal(?:\s+Kurum\s+Kredi)?(?:\s+(?:Derecelendirme|Rating))?\s+Notu\s*(?:[:\-]\s*)+({RATING_TOKEN})",
    rf"Uzun\s+Vadeli\s+(?:Ulusal\s+)?(?:Derecelendirme|Rating)\s+Notu\s*(?:[:\-]\s*)+({RATING_TOKEN})",
    rf"Uzun\s+Vadeli\s+Ulusal\s+(?:Derecelendirme|Rating)\s+Notu\s*(?:[:\-]\s*)+({RATING_TOKEN})",
    rf"Kredi\s+(?:Derecelendirme|Rating)\s+Notu\s*[:\-]?\s*({RATING_TOKEN})",
    rf"derecelendirme\s+notu\s+[\"“'‘’]?({RATING_TOKEN})[\"”'‘’]?\s*(?:seviyesine|olarak|seviyesinde)",
    rf"notu\s+[\"“'‘’]?({RATING_TOKEN})[\"”'‘’]?\s*(?:seviyesine|olarak|seviyesinde)",
)

PREVIOUS_PATTERNS = (
    rf"({RATING_TOKEN})\s*[\"“'‘’]?\s*seviyesinden\s*[\"“'‘’]?({RATING_TOKEN})\s*[\"”'’]?\s*seviyesine",
    rf"önceki\s+(?:not[u]?|derecelendirme\s+notu)\s*[:\-]?\s*({RATING_TOKEN})",
)

AGENCY_PATTERNS = (
    (r"\bjcr\b", "JCR Avrasya"),
    (r"\bsaha\b", "Saha Rating"),
    (r"turkrating|istanbul uluslararasi derecelendirme", "Turkrating"),
    (r"\bfitch\b", "Fitch"),
    (r"moody", "Moody's"),
    (r"standard\s*&?\s*poor|\bs&p\b", "S&P"),
)

RED_FLAG_TERMS = (
    "temerrut",
    "gecikmis alacak",
    "odeme yapilamadi",
    "odeme gerceklestirilemedi",
    "kupon odemesi",
    "yukumluluklerin yerine getirilemem",
    "vefat",
    "belirsizlik",
    "nakit akisi",
    "bozul",
    "yapilandirma",
    "likidite",
    "iflas",
    "konkordato",
    "borca batik",
    "sermaye kaybi",
    "ceza",
    "sorusturma",
    "dava",
    "faaliyetlerin durdurulmasi",
    "uretime ara",
    "mucbir sebep",
    "tedarik",
    "is kazasi",
    "yangin",
    "deprem",
    "el konulmasi",
    "kayyum",
)

_SCALE = (
    "D",
    "C",
    "CC",
    "CCC-",
    "CCC",
    "CCC+",
    "B-",
    "B",
    "B+",
    "BB-",
    "BB",
    "BB+",
    "BBB-",
    "BBB",
    "BBB+",
    "A-",
    "A",
    "A+",
    "AA-",
    "AA",
    "AA+",
    "AAA",
)
_SCALE_RANK = {value: index for index, value in enumerate(_SCALE)}

_ABBREVIATIONS = (
    ("T.A.Ş.", "T§A§Ş§"),
    ("A.Ş.", "A§Ş§"),
    ("T.C.", "T§C§"),
    ("Prof.", "Prof§"),
    ("Dr.", "Dr§"),
    ("vd.", "vd§"),
    ("vb.", "vb§"),
    ("ör.", "ör§"),
)


def _split_sentences(text: str) -> list[str]:
    """Kısaltmalardaki noktaları cümle sonu saymadan metni böler."""
    prepared = text
    for original, placeholder in _ABBREVIATIONS:
        prepared = prepared.replace(original, placeholder)
    parts = re.split(r"(?<=[.!?])\s+|(?<=;)\s+|•", prepared)
    return [part.replace("§", ".").strip() for part in parts if part.strip()]


def is_rating_disclosure(disclosure: Disclosure) -> bool:
    haystack = normalize(
        f"{disclosure.subject} {disclosure.summary} {disclosure.title}"
    )
    return any(term in haystack for term in RATING_SUBJECT_TERMS)


def _clean(text: str) -> str:
    text = (text or "").replace("\u00a0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _base_rating(value: str) -> str:
    cleaned = (value or "").upper().replace("(TR)", " ").replace("(TEMERRÜT)", " ")
    cleaned = cleaned.replace("TEMERRÜT", " ").replace("TR", " ").strip()
    return cleaned.split()[0] if cleaned else ""


def _scale_rank(value: str) -> int | None:
    base = _base_rating(value).strip("() ")
    return _SCALE_RANK.get(base)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = normalize(value)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _first_match(text: str, patterns: tuple[str, ...]) -> re.Match | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match
    return None


def _extract_entity(text: str, disclosure: Disclosure, source: str) -> str:
    match = re.search(
        r"Derecelendirilen\s+Kuruluş\s*[:\-]?\s*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if match:
        entity = _clean(match.group(1)).strip(" :;,")
        if entity:
            return entity[:200]

    match = re.search(r"DUYURU\s*\n\s*([^\n]+)", text, re.IGNORECASE)
    if match:
        entity = _clean(match.group(1)).strip(" :;,")
        if entity:
            return entity[:200]

    if source:
        stem = PurePath(source).stem.strip()
        if stem and normalize(stem) not in {"kredi derecelendirmesi", "duyuru"}:
            return stem[:200]

    title = disclosure.title.strip()
    if title and "derecelendirme" not in normalize(title):
        return title[:200]
    return ""


def _extract_agency(text: str, disclosure: Disclosure) -> str:
    haystack = normalize(f"{disclosure.title}\n{text}")
    for pattern, label in AGENCY_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return label
    title = disclosure.title.strip()
    if title and "derecelendirme" in normalize(title):
        return title[:80]
    return ""


def _extract_ratings(text: str) -> tuple[str, str]:
    """(önceki, güncel) not çiftini döndürür."""
    previous = ""
    current = ""

    match = _first_match(text, PREVIOUS_PATTERNS)
    if match:
        previous = _clean(match.group(1))
        current = _clean(match.group(2))

    match = _first_match(text, CURRENT_PATTERNS)
    if match:
        current = _clean(match.group(1))

    return previous, current


def _extract_outlook(text: str, current: str) -> str:
    low = normalize(text)
    is_default = _base_rating(current) == "D"
    if is_default:
        if "askida" in low:
            return "Temerrüt (Askıda)"
        if "izlemeye alin" in low:
            return "Temerrüt (İzleme)"
        return "Temerrüt"
    if "askida" in low:
        return "Askıda"
    if "izlemeye alin" in low or "negatif izleme" in low:
        return "İzleme"
    if "negatif" in low:
        return "Negatif"
    if "pozitif" in low:
        return "Pozitif"
    if "stabil" in low or "duragan" in low:
        return "Stabil"
    return "—"


def _extract_action(text: str, current: str, previous: str) -> str:
    low = normalize(text)
    if (
        "temerrute alin" in low
        or "temerrut olarak" in low
        or "temerrut hali" in low
        or _base_rating(current) == "D"
    ):
        return "Temerrüt"

    if current and previous:
        current_rank = _scale_rank(current)
        previous_rank = _scale_rank(previous)
        if current_rank is not None and previous_rank is not None:
            if current_rank < previous_rank:
                return "Not indirimi"
            if current_rank > previous_rank:
                return "Not artırımı"
            return "Teyit"

    if "not indir" in low or "dusur" in low or "notu dusur" in low:
        return "Not indirimi"
    if "not art" in low or "yukselt" in low:
        return "Not artırımı"
    if "teyit" in low:
        return "Teyit"
    if "izlemeye alin" in low:
        return "İzlemeye alındı"
    if "askiya alin" in low:
        return "Askıya alındı"
    if "ilk kez" in low or "ilk defa" in low:
        return "İlk derecelendirme"
    if "revize" in low or "guncellen" in low:
        return "Güncelleme"
    return "—"


_HEADER_NOISE = (
    "telif hakki",
    "izinsiz cogaltilmasina",
    "tum haklari saklidir",
    "burada yer alan bilgiler",
    "kap turkiye sermaye piyasalari",
)

# PDF tabloları tek satıra düzleşince "Notu: X Notu: Y" gibi döküntüler
# oluşuyor. Bunları risk cümlesi saymıyoruz.
_TABLE_NOISE = (
    "derecelendirilen kurulus",
    "derecelendirme notunun verilis tarihi",
)


def _clean_sentence(sentence: str) -> str:
    text = " ".join(sentence.replace("•", " ").split())
    text = text.strip(" -–—,;:")
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"[.,;:]+$", "", text)
    if not text:
        return ""
    if text[0].islower():
        text = text[0].upper() + text[1:]
    return text + "."


def _is_noise_sentence(sentence: str) -> bool:
    low = normalize(sentence)
    if len(low) < 25:
        return True
    if low.count("notu") >= 2:
        return True
    if low.count(":") >= 3:
        return True
    if any(term in low for term in _HEADER_NOISE):
        return True
    if any(term in low for term in _TABLE_NOISE):
        return True
    # "İstanbul, 11.11.2026 Kredi Derecelendirme..." gibi başlık satırları.
    if re.match(r"^[a-zçğıöşü]+,?\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}", low):
        return True
    return False


def _drop_noise_lines(text: str) -> str:
    lines: list[str] = []
    for line in _clean(text).split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        low = normalize(stripped)
        if any(term in low for term in _HEADER_NOISE):
            continue
        if any(term in low for term in _TABLE_NOISE):
            continue
        if "notu:" in low or low.endswith(" notu"):
            continue
        if re.match(r"^[a-zçğıöşü]+,?\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}", low):
            continue
        lines.append(stripped)
    return " ".join(lines)


def _extract_red_flags(text: str) -> tuple[str, ...]:
    # PDF satır sonları cümle sonu değildir; önce tablo/başlık satırlarını at,
    # kalan satırları birleştir, madde işaretlerini ve nokta/virgül sonlarını
    # cümle sınırı say.
    flattened = _drop_noise_lines(text)
    sentences = _split_sentences(flattened)
    flags: list[str] = []
    for term in RED_FLAG_TERMS:
        for sentence in sentences:
            if term not in normalize(sentence):
                continue
            if _is_noise_sentence(sentence):
                continue
            cleaned = _clean_sentence(sentence)
            if not cleaned:
                continue
            flags.append(cleaned[:240])
            break
        if len(flags) >= 4:
            break
    return tuple(_dedupe(flags))


def _extract_details(text: str) -> tuple[str, ...]:
    details: list[str] = []
    patterns = (
        (
            r"Uzun\s+Vadeli\s+Uluslararas[ıi]\s+Yerel\s+Para\s+(?:Kurum\s+Kredi\s+)?(?:Derecelendirme|Rating)?\s*Notu\s*(?:[:\-]\s*)+([^\n]+)",
            "Uzun vadeli uluslararası",
        ),
        (
            r"K[ıi]sa\s+Vadeli\s+(?:Ulusal\s+)?(?:Kurum\s+Kredi\s+)?(?:Derecelendirme|Rating)?\s*Notu\s*(?:[:\-]\s*)+([^\n]+)",
            "Kısa vadeli ulusal",
        ),
    )
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = " ".join(match.group(1).split()).strip(" :;,")
        if value:
            details.append(f"{label}: {value[:100]}")
    return tuple(details[:3])


def parse_rating(
    text: str, disclosure: Disclosure, source: str = ""
) -> RatingInfo:
    """Bir derecelendirme metninden firma, not ve aksiyon bilgisini çıkarır."""
    text = _clean(text)
    entity = _extract_entity(text, disclosure, source)
    agency = _extract_agency(text, disclosure)
    previous, current = _extract_ratings(text)
    outlook = _extract_outlook(text, current)
    action = _extract_action(text, current, previous)
    red_flags = _extract_red_flags(text)
    details = _extract_details(text)

    return RatingInfo(
        agency=agency,
        entity=entity,
        current=current,
        previous=previous,
        outlook=outlook,
        action=action,
        details=details,
        red_flags=red_flags,
        source=source,
    )


def html_to_text(page_html: str) -> str:
    text = re.sub(
        r"<script.*?</script>", " ", page_html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(
        r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</(?:p|div|li|tr|h[1-6]|td|th)>", "\n", text, flags=re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean(html_lib.unescape(text))


class RatingExtractor:
    def __init__(
        self,
        config: RatingsConfig,
        state: State,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.state = state
        self.session = session or requests.Session()
        # Sayaç örnek seviyesinde: limit "tek çalışmada" geçerli olsun,
        # e-posta parçaları arasında yeniden sıfırlanmasın.
        self._attempted = 0
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    def extract(
        self, disclosures: list[Disclosure], now: datetime
    ) -> dict[str, RatingInfo]:
        results: dict[str, RatingInfo] = {}
        if not self.config.enabled:
            return results

        for disclosure in disclosures:
            if not is_rating_disclosure(disclosure):
                continue

            if self._attempted >= self.config.max_items_per_run:
                LOGGER.info(
                    "Kredi notu çıkarma limiti (%s) doldu; kalanlar sonraki çalışmada",
                    self.config.max_items_per_run,
                )
                break
            self._attempted += 1

            try:
                page_html = self._fetch(KAP_DETAIL_URL.format(index=disclosure.index))
                attachments = extract_attachments(page_html)
                fingerprint = attachment_fingerprint(attachments)

                # Ek dosya değişmediyse önbellekteki kayıt geçerlidir; PDF
                # yeniden indirilmez.
                cached = self.state.get_rating(
                    disclosure.index, disclosure.content_hash()
                )
                if cached is not None and cached.attachments == fingerprint:
                    results[disclosure.index] = cached
                    continue

                info = self._extract_one(disclosure, page_html, attachments)
            except Exception as exc:  # noqa: BLE001 - tek bildirim tüm işi durdurmasın
                LOGGER.warning(
                    "Kredi notu çıkarılamadı (%s): %s", disclosure.index, exc
                )
                continue

            if info is None:
                continue
            results[disclosure.index] = info
            self.state.set_rating(
                disclosure.index,
                info,
                content_hash=disclosure.content_hash(),
                now=now,
            )

        return results

    def _extract_one(
        self,
        disclosure: Disclosure,
        page_html: str,
        attachments: list[dict],
    ) -> RatingInfo | None:
        pdfs = pdf_attachments(attachments, self.config.max_attachments)

        text_parts: list[str] = []
        source = ""
        for attachment in pdfs:
            oid = str(attachment.get("objId") or "")
            if not oid:
                continue
            try:
                payload = self._fetch_bytes(KAP_FILE_URL.format(oid=oid))
                extracted = pdf_text(payload)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Ek PDF okunamadı (%s): %s", attachment.get("fileName"), exc
                )
                continue
            if extracted.strip():
                text_parts.append(extracted)
                source = source or str(attachment.get("fileName") or "")

        # Ek yoksa veya metin çıkmadıysa bildirim gövdesini kullan.
        if not text_parts:
            text_parts.append(html_to_text(page_html))

        info = parse_rating("\n".join(text_parts), disclosure, source=source)
        info = replace(info, attachments=attachment_fingerprint(attachments))
        # Notu okunamayan kayıtlar tabloyu kirletmesin; AI özeti KAP
        # metniyle devam eder.
        if not (info.current or info.previous):
            return None
        if not (info.entity or info.agency):
            return None
        return info

    def _fetch(self, url: str) -> str:
        return get_with_retry(
            self.session, url, timeout=self.config.timeout_seconds
        ).text

    def _fetch_bytes(self, url: str) -> bytes:
        return get_bytes_with_retry(
            self.session,
            url,
            timeout=self.config.timeout_seconds,
            max_bytes=self.config.max_pdf_bytes,
        )