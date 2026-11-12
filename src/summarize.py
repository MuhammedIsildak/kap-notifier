from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import requests

from .config import LLMConfig
from .models import (
    MATERIALITY_ORDER,
    Disclosure,
    RatingInfo,
    Summary,
)
from .state import State

LOGGER = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
SOUL_PATH = Path(__file__).resolve().parents[1] / "soul.md"

DEFAULT_SOUL = """Sen kıdemli bir kurumsal finans ve kredi analistisin.
KAP bildirimlerini bir mali analistin karar sürecinde işine yarayacak şekilde
okur, rutin işlemleri ayırır, maddi etkisi olan gelişmeleri öne çıkarırsın.
Türkçe yazarsın; sayıları ve tarihleri korursun; spekülasyon yapmaz ve yatırım
tavsiyesi vermezsin."""

PROMPT = """Aşağıdaki KAP bildirimlerini, kıdemli bir mali analistin gözüyle
Türkçe değerlendir.

Her bildirim için şu alanları üret:
- materiality: "critical" | "high" | "normal" | "low"
  critical: temerrüt, iflas, konkordato, not indirimi, sermaye kaybı, faaliyet durması
  high: yatırım kararı, M&A, büyük sözleşme/ihale, temettü, sermaye artırımı, önemli finansman
  normal: rutin dışı ama doğrudan kritik olmayan gelişme
  low: rutin idari işlem, sıradan yönetici ataması, olağan geri alım
- category: kısa etiket (örn. "Kredi notu", "Temerrüt", "İflas/hukuki",
  "TMSF/devir", "Yatırım", "M&A", "Temettü", "Sözleşme", "Finansal rapor",
  "Finansman", "Yönetim değişikliği", "Diğer")
- summary: 2-4 cümle. Ne oldu, hangi büyüklükte, analist için neden önemli?
  Sayıları, tarihleri ve şirket unvanlarını koru. Dış bilgi ekleme.
- red_flags: en fazla 3 somut risk sinyali; yoksa boş liste.
- analysis_points: en fazla 3 madde. Her madde bir NETİCE söylemeli:
  "<somut gözlem veya rakam> → <bunun sonucu: ne olur / ne izlenir>".
  Örnek: "Sözleşme 450 mn TL, 2025 cirosunun %18'i → 2027 FAVÖK'üne
  yıllık ~%6 katkı, nakit girişi 2027'de başlar".
  Örnek: "Net borç/FAVÖK 4.1x → 2027'de 3.5x altına inmezse kredi
  koşulları yeniden fiyatlandır".
  "İncelenmelidir", "takip edilmelidir", "dikkat edilmelidir", "önemlidir"
  gibi tek başına havada kalan ifadeler YASAK. Her maddede sayı, tarih,
  eşik veya doğrudan sonuç olmalı. Bilgi yoksa "tutar açıklanmadı → etki
  hesaplanamıyor, KAP ekine bakılmalı" gibi açık yaz.

- has_document: belge metni ("belge_metni") sağlanıyorsa True, yoksa False.
  Sadece verilen bilgilere dayan; tahmin ve yorum katma.

Belge metni ("belge_metni") verilmişse:
- Özet ve analiz noktalarını KAP'ın kısa özeti yerine belgedeki tutar, oran,
  tarih ve koşullara dayandır.
- Belge ile KAP özeti çelişirse belge metnini esas al.
- Belgede olmayan sayıyı uydurma; yoksa "belgede tutar belirtilmemiş" yaz.

Kurallar:
- Sadece verilen bilgilere dayan; tahmin ve yorum katma.
- "Kredi notu" verisi varsa onu mutlaka kullan: firma, ajans, önceki/yeni not,
  aksiyon ve görünüm.
- Yatırım tavsiyesi verme.
- Yanıtı SADECE aşağıdaki JSON şemasında döndür:
{"items": [{"id": "bildirim-index", "materiality": "high",
  "category": "Kredi notu", "summary": "...", "red_flags": ["..."],
  "analysis_points": ["..."]}]}Bildirimler:
{payload}
"""


def load_soul(path: Path = SOUL_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SOUL
    return text or DEFAULT_SOUL


def redact(text: str, secret: str) -> str:
    """Hata mesajlarından API anahtarını siler.

    Anahtar istek adresinde (query parametresi) taşındığı için ağ hataları
    URL'yi düz metin olarak içerir; loglara sızmasın diye burada maskelenir.
    """
    return text.replace(secret, "***") if secret else text


def _rating_materiality(rating: RatingInfo) -> str:
    return rating.materiality


def _fallback_summary(rating: RatingInfo) -> Summary:
    return Summary(
        text=rating.fallback_summary(),
        materiality=_rating_materiality(rating),
        category="Kredi notu",
        red_flags=rating.red_flags,
    )


class Summarizer:
    def __init__(self, config: LLMConfig, state: State):
        self.config = config
        self.state = state
        self.soul = load_soul()
        # Limitler ve süre bütçesi çalışma başına geçerli olsun; e-posta
        # parçaları arasında yeniden sıfırlanmasın.
        self._sent_to_llm = 0
        self._deadline = time.monotonic() + config.max_run_seconds

    def summarize(
        self,
        disclosures: list[Disclosure],
        ratings: dict[str, RatingInfo] | None = None,
        documents: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Summary]:
        """Önbellekte olanları döndürür, eksikleri LLM ile özetler."""
        ratings = ratings or {}
        documents = documents or {}
        results: dict[str, Summary] = {}
        missing: list[Disclosure] = []

        for disclosure in disclosures:
            cached = self.state.get_summary(
                disclosure.index, disclosure.content_hash()
            )
            if cached is not None:
                results[disclosure.index] = cached
                continue

            rating = ratings.get(disclosure.index)
            if rating is not None:
                # LLM çalışmazsa bile kredi notu tablosu anlamlı kalsın.
                results[disclosure.index] = _fallback_summary(rating)
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

        budget = max(0, self.config.max_items_per_run - self._sent_to_llm)
        limited = missing[:budget]
        self._sent_to_llm += len(limited)
        if len(missing) > len(limited):
            LOGGER.info(
                "%s bildirim LLM limiti (%s) nedeniyle bu çalışmada özetlenmeyecek",
                len(missing) - len(limited),
                self.config.max_items_per_run,
            )

        # Süre bütçesi: GitHub Actions işi 20 dakikada kesiyor. Bütçe
        # dolarsa yeni parti başlatılmaz; bildirimler KAP özetiyle gider.
        stamp = now or _now_like()

        for start in range(0, len(limited), self.config.batch_size):
            if time.monotonic() >= self._deadline:
                LOGGER.warning(
                    "LLM süre bütçesi doldu (%ss); %s bildirim KAP özetiyle "
                    "gönderilecek",
                    self.config.max_run_seconds,
                    len(limited) - start,
                )
                break
            batch = limited[start : start + self.config.batch_size]
            try:
                summarized = self._call_gemini(batch, api_key, ratings, documents)
            except Exception as exc:  # noqa: BLE001 - tek parti hatası tüm işi durdurmasın
                LOGGER.warning("LLM partisi özetlenemedi: %s", exc)
                continue

            for disclosure in batch:
                summary = summarized.get(disclosure.index)
                if summary is None:
                    continue
                results[disclosure.index] = summary
                self.state.set_summary(
                    disclosure.index,
                    summary,
                    model=self.config.model,
                    now=stamp,
                    content_hash=disclosure.content_hash(),
                )

        return results

    def _call_gemini(
        self,
        batch: list[Disclosure],
        api_key: str,
        ratings: dict[str, RatingInfo],
        documents: dict[str, str],
    ) -> dict[str, Summary]:
        payload = []
        for item in batch:
            rating = ratings.get(item.index)
            payload.append(
                {
                    "id": item.index,
                    "sirket": item.title,
                    "bildirim": item.subject,
                    "tarih": item.publish_date,
                    "kap_ozeti": item.summary,
                    "kredi_notu": rating.to_dict() if rating else None,
                    "belge_metni": documents.get(item.index) or None,
                }
            )
        prompt = PROMPT.replace(
            "{payload}", json.dumps(payload, ensure_ascii=False)
        )
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        if self.soul:
            body["systemInstruction"] = {"parts": [{"text": self.soul}]}

        valid_ids = {item.index for item in batch}
        models = [self.config.model]
        fallback = self.config.fallback_model.strip()
        if fallback and fallback not in models:
            models.append(fallback)

        last_error: Exception | None = None
        for model in models:
            try:
                text = self._request(model, api_key, body)
                parsed = self._parse_response(text, valid_ids)
                for index, summary in list(parsed.items()):
                    if documents.get(index):
                        parsed[index] = replace(summary, has_document=True)
                if documents:
                    LOGGER.info(
                        "%s bildirim için PDF içeriği özete eklendi",
                        sum(1 for item in batch if documents.get(item.index)),
                    )
                return parsed
            except Exception as exc:  # noqa: BLE001 - model yedeğine geç
                last_error = exc
                LOGGER.warning("LLM modeli başarısız (%s): %s", model, exc)
                continue

        raise RuntimeError(f"Tüm LLM modelleri başarısız: {last_error}")

    def _request(self, model: str, api_key: str, body: dict) -> str:
        url = GEMINI_URL.format(model=model)
        response: requests.Response | None = None
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    url,
                    params={"key": api_key},
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
            except requests.RequestException as exc:
                LOGGER.warning(
                    "Gemini isteği başarısız (deneme %s/4, %s): %s",
                    attempt,
                    model,
                    redact(str(exc), api_key),
                )
                time.sleep(min(2 ** attempt, 10))
                continue
            if response.status_code in RETRYABLE_STATUS:
                LOGGER.warning(
                    "Gemini geçici hata verdi (deneme %s/4, %s): HTTP %s",
                    attempt,
                    model,
                    response.status_code,
                )
                time.sleep(min(2 ** attempt, 10))
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
        return text

    @staticmethod
    def _parse_response(text: str, valid_ids: set[str]) -> dict[str, Summary]:
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

        results: dict[str, Summary] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            index = str(item.get("id") or item.get("index") or "").strip()
            summary = " ".join(str(item.get("summary") or "").split())
            if index not in valid_ids or not summary:
                continue

            materiality = str(item.get("materiality") or "normal").strip().lower()
            if materiality not in MATERIALITY_ORDER:
                materiality = "normal"

            red_flags = _string_list(item.get("red_flags"), limit=4, length=240)
            analysis_points = _string_list(
                item.get("analysis_points"), limit=4, length=280
            )
            has_document = bool(item.get("has_document"))
            results[index] = Summary(
                text=summary[:800],
                materiality=materiality,
                category=" ".join(
                    str(item.get("category") or "").split()
                )[:60],
                red_flags=red_flags,
                analysis_points=analysis_points,
                has_document=has_document,
            )
        return results


def _string_list(value: object, *, limit: int, length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    output: list[str] = []
    for item in value:
        text = " ".join(str(item or "").split())
        if text:
            output.append(text.rstrip(".,;:")[:length])
        if len(output) >= limit:
            break
    return tuple(output)


def _now_like():
    """State ile aynı saat dilimini kullanmak için tembel import."""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Europe/Istanbul"))