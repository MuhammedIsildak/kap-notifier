from dataclasses import replace
from unittest.mock import MagicMock, patch

from src.config import LLMConfig
from src.models import Disclosure, RatingInfo
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

    with patch("src.llm.requests.post", return_value=response) as post:
        summarizer = Summarizer(
            LLMConfig(enabled=True, model="test-model", batch_size=8), state
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "AI özeti"
    assert state.get_summary("1") is not None
    assert state.get_summary("1").text == "AI özeti"
    post.assert_called_once()

    body = post.call_args.kwargs["json"]
    assert "systemInstruction" in body
    assert "kıdemli" in body["systemInstruction"]["parts"][0]["text"].lower()


def test_summarizer_resummarizes_when_content_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    state = State(tmp_path)

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"items":[{"id":"1","summary":"Yeni özet"}]}'}]}}
        ]
    }

    with patch("src.llm.requests.post", return_value=response) as post:
        summarizer = Summarizer(
            LLMConfig(enabled=True, model="test-model", batch_size=8), state
        )
        summarizer.summarize([disclosure()])
        changed = replace(disclosure(), summary="Güncellenmiş KAP özeti")
        result = summarizer.summarize([changed])

    assert result["1"].text == "Yeni özet"
    assert post.call_count == 2


def test_summarizer_without_key_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    state = State(tmp_path)
    summarizer = Summarizer(LLMConfig(enabled=True), state)
    assert summarizer.summarize([disclosure()]) == {}


def test_rating_fallback_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    state = State(tmp_path)
    rating = RatingInfo(
        agency="JCR Avrasya",
        entity="ÖRNEK FON",
        current="CCC (tr)",
        previous="A (tr)",
        outlook="—",
        action="Not indirimi",
        red_flags=("Nakit akışı bozuldu",),
    )
    summarizer = Summarizer(LLMConfig(enabled=True), state)
    result = summarizer.summarize(
        [disclosure()], ratings={"1": rating}
    )
    summary = result["1"]
    assert "ÖRNEK FON" in summary.text
    assert "A (tr)" in summary.text
    assert summary.category == "Kredi notu"
    assert summary.materiality == "high"
    assert summary.red_flags == ("Nakit akışı bozuldu",)


def test_falls_back_to_second_model_on_503(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.time.sleep", lambda _seconds: None)
    state = State(tmp_path)

    unavailable = MagicMock()
    unavailable.status_code = 503
    unavailable.text = "high demand"
    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"items":[{"id":"1","summary":"Yedek"}]}'}]}}
        ]
    }

    with patch(
        "src.llm.requests.post", side_effect=[unavailable] * 4 + [ok]
    ) as post:
        summarizer = Summarizer(
            LLMConfig(
                enabled=True,
                model="primary",
                fallback_model="backup",
                batch_size=1,
            ),
            state,
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "Yedek"
    assert post.call_count == 5
    assert "primary" in post.call_args_list[0].args[0]
    assert "backup" in post.call_args_list[-1].args[0]