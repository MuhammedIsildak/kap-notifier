from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from .models import Disclosure

LOGGER = logging.getLogger(__name__)

KAP_LIGHT_URL = "https://www.kap.org.tr/tr/api/disclosure/list/light"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Referer": "https://www.kap.org.tr/tr/bildirim-sorgu",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


class FetchError(RuntimeError):
    pass


def _parse_payload(payload: object) -> list[Disclosure]:
    if not isinstance(payload, list):
        raise FetchError(
            f"KAP beklenmeyen yanıt döndü (liste değil): {type(payload).__name__}"
        )

    by_index: dict[str, Disclosure] = {}
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        try:
            disclosure = Disclosure.from_api(raw)
        except ValueError:
            LOGGER.warning("Geçersiz KAP kaydı atlandı: %r", raw)
            continue
        by_index[disclosure.index] = disclosure
    return list(by_index.values())


def fetch_disclosures(
    *,
    timeout: int = 30,
    retries: int = 3,
    session: requests.Session | None = None,
) -> list[Disclosure]:
    """Günün tüm KAP bildirimlerini tek istekte çeker.

    Bu uç nokta KAP'ın kendi arayüzünün kullandığı, resmî olarak
    dokümante edilmemiş bir uç noktadır. Nazik kullanım için çağrı
    sayısı düşük tutulmalıdır.
    """
    client = session or requests.Session()
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = client.get(KAP_LIGHT_URL, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            LOGGER.warning("KAP isteği başarısız (deneme %s/%s): %s", attempt, retries, exc)
        else:
            if response.status_code in RETRYABLE_STATUS:
                last_error = FetchError(f"HTTP {response.status_code}")
                LOGGER.warning(
                    "KAP geçici hata verdi (deneme %s/%s): HTTP %s",
                    attempt,
                    retries,
                    response.status_code,
                )
            elif response.status_code >= 400:
                snippet = response.text[:200].replace("\n", " ")
                raise FetchError(
                    f"KAP isteği reddedildi: HTTP {response.status_code} — {snippet}"
                )
            else:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise FetchError("KAP yanıtı JSON değil") from exc
                disclosures = _parse_payload(payload)
                LOGGER.info("KAP'tan %s bildirim alındı", len(disclosures))
                return disclosures

        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 8))

    raise FetchError(f"KAP'a ulaşılamadı: {last_error}")


def fetch_from_file(path: str | Path) -> list[Disclosure]:
    file_path = Path(path).expanduser()
    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    disclosures = _parse_payload(payload)
    LOGGER.info("%s dosyasından %s bildirim okundu", file_path, len(disclosures))
    return disclosures