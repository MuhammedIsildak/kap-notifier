from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .config import LLMConfig
from .models import Disclosure
from .state import State

LOGGER = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

PROMPT = """Aşağıdaki KAP bildirimlerini yatırımcı için Türkçe özetle.

Kurallar:
- Her özet en fazla 3 cümle olsun.
- Sadece verilen bilgilere dayan; tahmin, yorum veya dış bilgi ekleme.
- Sayıları, tarihleri ve şirket unvanlarını koru.
- Yatırım tavsiyesi verme.
- Yanıtı SADECE aşağıdaki JSON şemasında döndür:
  {"items": [{"id": "bildirim-index", "summary": "..."}]}

Bildirimler:
{payload}
"""


class Summarizer:
    def __init__(self, config: LLMConfig, state: State):
        self.config = config
        self.state = state

    def summarize(self, disclosures: list[Disclosure]) -> dict[str, str]:
        """Önbellekte olanları döndürür, eksikleri LLM ile özetler."""
        results: dict[str, str] = {}
        missing: list[Disclosure] = []

        for disclosure in disclosures:
            cached = self.state.get_summary(disclosure.index)
            if cached:
                results[disclosure.index] = cached
            else:
                missing.append(disclosure)

        if not self.config.enabled or not missing:
            return results

        api_key = os.getenv(self.config.api_key_env, "").strip()
        if not api_key:
            LOGGER.info(
                "%s tanımlı değil; LLM özeti atlanıyor, KAP özeti kullanılacak",
                self.config.api_key_env,
            )
            return results

        if self.config.provider != "gemini":
            LOGGER.warning(
                "Desteklenmeyen LLM sağlayıcısı: %s; LLM özeti atlanıyor",
                self.config.provider,
            )
            return results

        if self.config.max_items_per_run <= 0:
            return results

        limited = missing[: self.config.max_items_per_run]
        if len(missing) > len(limited):
            LOGGER.info(
                "%s bildirim LLM limiti (%s) nedeniyle bu çalışmada özetlenmeyecek",
                len(missing) - len(limited),
                self.config.max_items_per_run,
            )

        for start in range(0, len(limited), self.config.batch_size):
            batch = limited[start : start + self.config.batch_size]
            try:
                summarized = self._call_gemini(batch, api_key)
            except Exception as exc:  # noqa: BLE001 - tek parti hatası tüm işi durdurmasın
                LOGGER.warning("LLM partisi özetlenemedi: %s", exc)
                continue

            for disclosure in batch:
                text = summarized.get(disclosure.index)
                if not text:
                    continue
                results[disclosure.index] = text
                self.state.set_summary(
                    disclosure.index,
                    text,
                    model=self.config.model,
                    now=_now_like(),
                )

        return results

    def _call_gemini(self, batch: list[Disclosure], api_key: str) -> dict[str, str]:
        payload = [
            {
                "id": item.index,
                "sirket": item.title,
                "bildirim": item.subject,
                "tarih": item.publish_date,
                "kap_ozeti": item.summary,
            }
            for item in batch
        ]
        prompt = PROMPT.replace(
            "{payload}", json.dumps(payload, ensure_ascii=False)
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        }
        url = GEMINI_URL.format(model=self.config.model)

        response: requests.Response | None = None
        for attempt in range(1, 4):
            response = requests.post(
                url,
                params={"key": api_key},
                json=body,
                timeout=self.config.timeout_seconds,
            )
            if response.status_code in RETRYABLE_STATUS:
                LOGGER.warning(
                    "Gemini geçici hata verdi (deneme %s/3): HTTP %s",
                    attempt,
                    response.status_code,
                )
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            break

        if response is None:
            raise RuntimeError("Gemini yanıtı alınamadı")
        if response.status_code >= 400:
            snippet = response.text[:300].replace("\n", " ")
            raise RuntimeError(f"Gemini HTTP {response.status_code}: {snippet}")

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise RuntimeError(f"Gemini boş yanıt döndü: {feedback}")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(str(part.get("text") or "") for part in parts).strip()
        if not text:
            raise RuntimeError("Gemini metin döndürmedi")

        return self._parse_response(text, {item.index for item in batch})

    @staticmethod
    def _parse_response(text: str, valid_ids: set[str]) -> dict[str, str]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            parsed = json.loads(cleaned)
        except ValueError as exc:
            raise RuntimeError(f"Gemini JSON döndürmedi: {cleaned[:160]}") from exc

        items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            raise RuntimeError("Gemini yanıtında items listesi yok")

        results: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            index = str(item.get("id") or item.get("index") or "").strip()
            summary = " ".join(str(item.get("summary") or "").split())
            if index in valid_ids and summary:
                results[index] = summary[:600]
        return results


def _now_like():
    """State ile aynı saat dilimini kullanmak için tembel import."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Istanbul"))