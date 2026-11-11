#!/usr/bin/env python3
"""Gemini API anahtarını test eder ve isteğe bağlı olarak .env dosyasına kaydeder.

Kullanım:
    .venv/bin/python tools/setup_ai.py
    .venv/bin/python tools/setup_ai.py --model gemini-3.8-flash

Anahtar terminalde görünmez (getpass) ve hata mesajlarında maskelenir.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def read_env_value(key: str) -> str:
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def write_env_value(key: str, value: str) -> None:
    lines = (
        ENV_PATH.read_text(encoding="utf-8").splitlines()
        if ENV_PATH.exists()
        else []
    )
    output: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)
    if not found:
        output.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def test_key(key: str, model: str) -> bool:
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Sadece OK yaz."}]}],
        "generationConfig": {"maxOutputTokens": 5, "temperature": 0},
    }
    try:
        response = requests.post(
            url, params={"key": key}, json=body, timeout=30
        )
    except requests.RequestException as exc:
        print(f"Bağlantı hatası: {redact(str(exc), key)}")
        return False

    if response.status_code == 200:
        print(f"Anahtar çalışıyor (model: {model}).")
        return True

    try:
        error = response.json().get("error", {})
        message = error.get("message") or response.text[:400]
    except ValueError:
        message = response.text[:400]
    print(
        f"Anahtar/model reddedildi: HTTP {response.status_code} — "
        f"{redact(str(message), key)}"
    )

    if response.status_code in (400, 403, 404):
        print("\nMevcut Gemini modelleri kontrol ediliyor...")
        list_models(key)
    return False


def list_models(key: str) -> None:
    try:
        response = requests.get(
            f"{GEMINI_BASE}/models", params={"key": key}, timeout=30
        )
    except requests.RequestException as exc:
        print(f"Model listesi alınamadı: {redact(str(exc), key)}")
        return

    if response.status_code != 200:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = response.text[:300]
        print(
            f"Model listesi alınamadı: HTTP {response.status_code} — "
            f"{redact(str(message), key)}"
        )
        return

    names = [
        item.get("name", "").removeprefix("models/")
        for item in response.json().get("models", [])
        if "generateContent" in item.get("supportedGenerationMethods", [])
    ]
    if names:
        print("Kullanılabilir modeller (ilk 10):")
        for name in names[:10]:
            print(f"  - {name}")
        print("\nModel adını config.yaml içindeki llm.model alanına yazabilirsin.")
    else:
        print("generateContent destekleyen model bulunamadı.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini API anahtarını test et")
    parser.add_argument(
        "--model", default="gemini-3.5-flash-lite", help="Test edilecek model"
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Anahtar çalışsa bile .env dosyasına kaydetme",
    )
    args = parser.parse_args()

    existing = read_env_value("GEMINI_API_KEY")
    if existing:
        print(".env içinde mevcut bir anahtar bulundu, test ediliyor...")
        key = existing
    else:
        key = getpass.getpass(
            "Gemini API anahtarını yapıştır (ekranda görünmez): "
        ).strip()

    if not key:
        print("Anahtar boş; işlem iptal edildi.")
        return 2

    if not test_key(key, args.model):
        return 1

    if not args.no_save:
        answer = input(".env dosyasına kaydedilsin mi? [E/h]: ").strip().lower()
        if answer in ("", "e", "evet", "y", "yes"):
            write_env_value("GEMINI_API_KEY", key)
            print(f"Kaydedildi: {ENV_PATH}")
        else:
            print("Kaydedilmedi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())