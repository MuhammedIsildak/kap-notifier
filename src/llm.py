"""LLM sağlayıcı katmanı.

Her sağlayıcı, kendi istek/yanıt biçimini bilen bir istemci olarak tanımlanır.
Özetleme katmanı (src/summarize.py) sağlayıcıdan bağımsızdır; sıralı bir
istemci zinciri üzerinde çalışır ve ilk başarılı yanıtı alır.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import requests

LOGGER = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Gemini'nin ücretsiz katmanı yoğun saatte 503 döner; sınırlı deneme yapıp
# diğer modellere geçiyoruz. NVIDIA tarafında her deneme kredi harcar, orada
# deneme sayısı düşük tutulur.
GEMINI_ATTEMPTS = 4
NVIDIA_ATTEMPTS = 3


class LLMError(RuntimeError):
    """Bir sağlayıcıdan geçerli yanıt alınamadı."""


def redact(text: str, secret: str) -> str:
    """Hata mesajlarından API anahtarını siler."""
    return text.replace(secret, "***") if secret else text


@dataclass(frozen=True)
class Client:
    """Bir (sağlayıcı, model) çifti ve onun istek/yanıt biçimi."""

    provider: str
    model: str
    api_key: str
    timeout_seconds: int = 60
    attempts: int = 3

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"


class LLMClient(Protocol):
    """Ortak arayüz: metin istemek, ham metin yanıt almak."""

    provider: str
    model: str

    def complete(self, prompt: str, system: str | None = None) -> str: ...


def _post(
    client: Client,
    url: str,
    *,
    json_body: dict,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> requests.Response:
    """Yeniden denemeli POST.

    Geçici hatalarda (429/5xx) kademeli bekleyerek yeniden dener; kalıcı
    hatada son yanıtı döndürür, çağıran durum koduna bakar.
    """
    response: requests.Response | None = None
    for attempt in range(1, client.attempts + 1):
        try:
            response = requests.post(
                url,
                json=json_body,
                headers=headers,
                params=params,
                timeout=client.timeout_seconds,
            )
        except requests.RequestException as exc:
            LOGGER.warning(
                "%s isteği başarısız (deneme %s/%s): %s",
                client.label,
                attempt,
                client.attempts,
                redact(str(exc), client.api_key),
            )
            if attempt < client.attempts:
                time.sleep(min(2**attempt, 10))
            continue
        if response.status_code in RETRYABLE_STATUS:
            LOGGER.warning(
                "%s geçici hata verdi (deneme %s/%s): HTTP %s",
                client.label,
                attempt,
                client.attempts,
                response.status_code,
            )
            if attempt < client.attempts:
                time.sleep(min(2**attempt, 10))
            continue
        break
    if response is None:
        raise LLMError(f"{client.label} yanıtı alınamadı")
    return response


def _raise_for_status(client: Client, response: requests.Response) -> None:
    if response.status_code < 400:
        return
    snippet = response.text[:300].replace("\n", " ")
    raise LLMError(
        f"{client.label} HTTP {response.status_code}: "
        f"{redact(snippet, client.api_key)}"
    )


class GeminiClient:
    """Google Gemini (generateContent)."""

    provider = "gemini"

    def __init__(self, client: Client):
        self.client = client
        self.model = client.model

    def complete(self, prompt: str, system: str | None = None) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = GEMINI_URL.format(model=self.client.model)
        response = _post(
            self.client,
            url,
            json_body=body,
            params={"key": self.client.api_key},
        )
        _raise_for_status(self.client, response)

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise LLMError(f"{self.client.label} boş yanıt döndü: {feedback}")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(str(part.get("text") or "") for part in parts).strip()
        if not text:
            raise LLMError(f"{self.client.label} metin döndürmedi")
        return text


class NvidiaClient:
    """NVIDIA NIM / build.nvidia.com (OpenAI uyumlu chat/completions).

    Ücretsiz krediyle çalışır; anahtar Authorization başlığında taşınır, bu
    yüzden adrese gömülmez.
    """

    provider = "nvidia"

    def __init__(self, client: Client):
        self.client = client
        self.model = client.model

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.client.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8192,
            "stream": False,
            # Çoğu model JSON modunu destekler; desteklemeyenler 400 döner ve
            # aşağıdaki denemede şema olmadan tekrar istek yapılır.
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.client.api_key}",
            "Accept": "application/json",
        }
        response = _post(
            self.client,
            NVIDIA_URL,
            json_body=body,
            headers=headers,
        )
        if response.status_code == 400:
            body.pop("response_format", None)
            response = _post(
                self.client,
                NVIDIA_URL,
                json_body=body,
                headers=headers,
            )
        _raise_for_status(self.client, response)

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(
                f"{self.client.label} boş yanıt döndü: {data.get('detail') or ''}"
            )
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "").strip()
        if not text:
            # Bazı akıl yürütme (reasoning) modelleri içeriği reasoning_content
            # alanına yazar; bu durumda yanıt yok demektir.
            reasoning = str(message.get("reasoning_content") or "").strip()
            raise LLMError(
                f"{self.client.label} metin döndürmedi"
                + (" (yalnızca reasoning içeriği var)" if reasoning else "")
            )
        return text


_CLIENTS = {
    "gemini": GeminiClient,
    "nvidia": NvidiaClient,
}


def build_client(
    provider: str,
    model: str,
    api_key: str,
    *,
    timeout_seconds: int = 60,
) -> LLMClient:
    """Sağlayıcı adından istemci kurar; bilinmeyen sağlayıcı hata verir."""
    key = provider.strip().lower()
    factory = _CLIENTS.get(key)
    if factory is None:
        raise LLMError(f"Desteklenmeyen LLM sağlayıcısı: {provider}")
    attempts = GEMINI_ATTEMPTS if key == "gemini" else NVIDIA_ATTEMPTS
    client = Client(
        provider=key,
        model=model,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        attempts=attempts,
    )
    return factory(client)
