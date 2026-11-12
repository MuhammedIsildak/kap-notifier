from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from .kap import KapError, get_with_retry
from .models import Disclosure

LOGGER = logging.getLogger(__name__)

KAP_LIGHT_URL = "https://www.kap.org.tr/tr/api/disclosure/list/light"
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

    try:
        response = get_with_retry(
            client,
            KAP_LIGHT_URL,
            timeout=timeout,
            retries=retries,
            headers=HEADERS,
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        snippet = exc.response.text[:200].replace("\n", " ") if exc.response else ""
        raise FetchError(f"KAP isteği reddedildi: HTTP {status} — {snippet}") from exc
    except KapError as exc:
        raise FetchError(str(exc)) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError("KAP yanıtı JSON değil") from exc
    disclosures = _parse_payload(payload)
    LOGGER.info("KAP'tan %s bildirim alındı", len(disclosures))
    return disclosures


def fetch_from_file(path: str | Path) -> list[Disclosure]:
    file_path = Path(path).expanduser()
    with file_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    disclosures = _parse_payload(payload)
    LOGGER.info("%s dosyasından %s bildirim okundu", file_path, len(disclosures))
    return disclosures