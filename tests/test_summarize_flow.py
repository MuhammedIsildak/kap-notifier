from unittest.mock import MagicMock, patch

from src.config import LLMConfig
from src.models import Disclosure
from src.state import State
from src.summarize import Summarizer


def disclosure() -> Disclosure:
    return Disclosure(
        index="1",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması",
        summary="KAP özeti",
        publish_date="11.11.2026 10:00:00",
    )


def test_summarizer_calls_gemini_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    state = State(tmp_path)

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"items":[{"id":"1","summary":"AI özeti"}]}'}
                    ]
                }
            }
        ]
    }

    with patch("src.summarize.requests.post", return_value=response) as post:
        summarizer = Summarizer(
            LLMConfig(enabled=True, model="test-model", batch_size=8), state
        )
        result = summarizer.summarize([disclosure()])

    assert result == {"1": "AI özeti"}
    assert state.get_summary("1") == "AI özeti"
    post.assert_called_once()


def test_summarizer_without_key_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    state = State(tmp_path)
    summarizer = Summarizer(LLMConfig(enabled=True), state)
    assert summarizer.summarize([disclosure()]) == {}