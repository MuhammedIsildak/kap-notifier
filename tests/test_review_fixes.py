"""Kod incelemesinde bulunan hataların regresyon testleri."""

import logging
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from src.config import ConfigError, DocumentsConfig, LLMConfig, RatingsConfig, load_config
from src.documents import DocumentFetcher
from src.models import Disclosure
from src.ratings import RatingExtractor
from src.state import State
from src.summarize import Summarizer, redact

NOW = datetime.now(ZoneInfo("Europe/Istanbul"))


def disclosure(index: str = "1", subject: str = "Kredi Derecelendirmesi") -> Disclosure:
    return Disclosure(
        index=index,
        title="ŞİRKET",
        subject=subject,
        summary="KAP özeti",
        publish_date="11.11.2026 10:00:00",
    )


def test_api_key_is_masked_in_error_messages():
    assert redact("url?key=SECRET", "SECRET") == "url?key=***"
    assert redact("url?key=SECRET", "") == "url?key=SECRET"


def test_gemini_failure_does_not_log_api_key(tmp_path, monkeypatch, caplog):
    """Anahtar istek adresinde taşındığı için ağ hatasında loga sızabilirdi."""
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-KEY")
    monkeypatch.setattr("src.summarize.time.sleep", lambda _seconds: None)
    state = State(tmp_path)
    summarizer = Summarizer(LLMConfig(enabled=True, model="m"), state)

    failure = requests.ConnectionError(
        "Max retries exceeded with url: /v1beta/models/m:generateContent"
        "?key=SECRET-KEY"
    )
    with caplog.at_level(logging.WARNING):
        with patch("src.summarize.requests.post", side_effect=failure):
            summarizer.summarize([disclosure()])

    assert "SECRET-KEY" not in caplog.text
    assert "***" in caplog.text


def test_invalid_request_delay_raises_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("documents:\n  request_delay_seconds: hizli\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_rating_limit_applies_to_the_whole_run(tmp_path):
    """Limit e-posta parçaları arasında yeniden sayılmamalı."""
    state = State(tmp_path)
    extractor = RatingExtractor(
        RatingsConfig(enabled=True, max_items_per_run=1), state
    )

    with patch.object(RatingExtractor, "_extract_one", return_value=None):
        first = extractor.extract([disclosure("1"), disclosure("2")], now=NOW)
        second = extractor.extract([disclosure("3")], now=NOW)

    assert first == {}
    assert second == {}
    assert extractor._attempted == 1


def test_document_limit_applies_to_the_whole_run(tmp_path):
    state = State(tmp_path)
    fetcher = DocumentFetcher(DocumentsConfig(enabled=True, max_items_per_run=1), state)

    with patch.object(DocumentFetcher, "_fetch_one", return_value="metin"):
        first = fetcher.fetch([disclosure("1"), disclosure("2")])
        second = fetcher.fetch([disclosure("3")])

    assert list(first) == ["1"]
    assert second == {}
    assert fetcher._attempted == 1


def test_llm_limit_applies_to_the_whole_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    state = State(tmp_path)
    summarizer = Summarizer(
        LLMConfig(enabled=True, model="m", batch_size=1, max_items_per_run=1), state
    )

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '{"items":[]}'}]}}]
    }
    with patch("src.summarize.requests.post", return_value=response) as post:
        summarizer.summarize([disclosure("1")])
        summarizer.summarize([disclosure("2")])

    # İkinci bildirim için yeni istek atılmamalı: bütçe çalışmada doldu.
    assert post.call_count == 1


def test_llm_deadline_stops_new_batches(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    state = State(tmp_path)
    summarizer = Summarizer(
        LLMConfig(
            enabled=True,
            model="m",
            batch_size=1,
            max_items_per_run=10,
            max_run_seconds=30,
        ),
        state,
    )
    summarizer._deadline = 0.0  # bütçe dolmuş gibi

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '{"items":[]}'}]}}]
    }
    with patch("src.summarize.requests.post", return_value=response) as post:
        result = summarizer.summarize([disclosure("1")])

    assert result == {}
    post.assert_not_called()
