"""NVIDIA (OpenAI uyumlu) sağlayıcının yedek olarak kullanımı."""

import logging
from unittest.mock import MagicMock, patch

from src.config import FallbackProvider, LLMConfig, load_config
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


def nvidia_response(text: str = '{"items":[{"id":"1","summary":"NVIDIA özeti"}]}'):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": text}}]
    }
    return response


def config(**kwargs) -> LLMConfig:
    base = {
        "enabled": True,
        "model": "gemini-test",
        "fallback_model": "gemini-test-lite",
        "batch_size": 8,
    }
    base.update(kwargs)
    return LLMConfig(**base)


def _provider_side_effect(url: str, **kwargs):
    """Gemini adresine 503, NVIDIA adresine geçerli yanıt verir."""
    if "integrate.api.nvidia.com" in url:
        return nvidia_response()
    unavailable = MagicMock()
    unavailable.status_code = 503
    unavailable.text = "high demand"
    return unavailable


def test_nvidia_used_when_gemini_fails(tmp_path, monkeypatch):
    """Gemini hata verirse zincirdeki NVIDIA modeline düşülmeli."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key")
    monkeypatch.setattr("src.llm.time.sleep", lambda _seconds: None)
    state = State(tmp_path)

    with patch(
        "src.llm.requests.post",
        side_effect=_provider_side_effect,
    ) as post:
        summarizer = Summarizer(
            config(
                fallbacks=(
                    FallbackProvider(
                        provider="nvidia",
                        model="nvidia/nemotron-3-ultra-550b-a55b",
                        api_key_env="NVIDIA_API_KEY",
                    ),
                )
            ),
            state,
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "NVIDIA özeti"
    # İki gemini modeli denenir (biri 4 kez), sonra NVIDIA.
    assert post.call_count == 9
    nvidia_call = post.call_args_list[-1]
    assert nvidia_call.args[0].endswith("/v1/chat/completions")
    assert nvidia_call.kwargs["headers"]["Authorization"] == "Bearer nvapi-key"
    assert nvidia_call.kwargs["json"]["model"].startswith("nvidia/")


def test_nvidia_key_is_not_in_url_or_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret")
    monkeypatch.setattr("src.llm.time.sleep", lambda _seconds: None)
    state = State(tmp_path)

    unavailable = MagicMock()
    unavailable.status_code = 503
    unavailable.text = "high demand"

    def side_effect(url: str, **kwargs):
        if "integrate.api.nvidia.com" in url:
            return nvidia_response()
        return unavailable

    with caplog.at_level(logging.WARNING):
        with patch(
            "src.llm.requests.post",
            side_effect=side_effect,
        ) as post:
            summarizer = Summarizer(
                config(
                    fallbacks=(
                        FallbackProvider(
                            provider="nvidia",
                            model="nvidia/nemotron-3-ultra-550b-a55b",
                            api_key_env="NVIDIA_API_KEY",
                        ),
                    )
                ),
                state,
            )
            summarizer.summarize([disclosure()])

    assert "nvapi-secret" not in caplog.text
    for call in post.call_args_list:
        assert "nvapi-secret" not in str(call.args[0])


def test_nvidia_skipped_when_key_missing(tmp_path, monkeypatch):
    """NVIDIA anahtarı yoksa sağlayıcı zincirden çıkarılır."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    state = State(tmp_path)

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"items":[{"id":"1","summary":"G"}]}'}]}}
        ]
    }

    with patch("src.llm.requests.post", return_value=response) as post:
        summarizer = Summarizer(
            config(
                fallbacks=(
                    FallbackProvider(
                        provider="nvidia",
                        model="nvidia/nemotron-3-ultra-550b-a55b",
                        api_key_env="NVIDIA_API_KEY",
                    ),
                )
            ),
            state,
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "G"
    assert post.call_count == 1


def test_nvidia_response_with_surrounding_text_is_parsed(tmp_path, monkeypatch):
    """Bazı modeller JSON'u metin içine sarar; yine de ayrıştırılmalı."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key")
    state = State(tmp_path)

    text = (
        "İşte özet:\n```json\n"
        '{"items":[{"id":"1","summary":"Sarılmış JSON"}]}\n```\n'
        "Başka bir şey."
    )
    with patch(
        "src.llm.requests.post",
        side_effect=[nvidia_response(text)],
    ):
        summarizer = Summarizer(
            config(
                provider="nvidia",
                model="nvidia/nemotron-3-ultra-550b-a55b",
                fallback_model="",
                api_key_env="NVIDIA_API_KEY",
            ),
            state,
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "Sarılmış JSON"


def test_used_model_is_recorded_in_state(tmp_path, monkeypatch):
    """State hangi modelin özet ürettiğini kaydeder."""
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key")
    monkeypatch.setattr("src.llm.time.sleep", lambda _seconds: None)
    state = State(tmp_path)

    with patch(
        "src.llm.requests.post",
        side_effect=_provider_side_effect,
    ):
        summarizer = Summarizer(
            config(
                fallbacks=(
                    FallbackProvider(
                        provider="nvidia",
                        model="nvidia/nemotron-3-ultra-550b-a55b",
                        api_key_env="NVIDIA_API_KEY",
                    ),
                )
            ),
            state,
        )
        summarizer.summarize([disclosure()])

    entry = state.summaries["1"]
    assert entry["model"] == "nvidia/nvidia/nemotron-3-ultra-550b-a55b"


def test_fallbacks_parsed_from_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n"
        "  provider: gemini\n"
        "  model: gemini-x\n"
        "  fallbacks:\n"
        "    - provider: nvidia\n"
        "      model: nvidia/nemotron-3-ultra-550b-a55b\n"
        "      api_key_env: NVIDIA_API_KEY\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert len(loaded.llm.fallbacks) == 1
    assert loaded.llm.fallbacks[0].provider == "nvidia"
    assert loaded.llm.fallbacks[0].model.startswith("nvidia/")
    assert loaded.llm.fallbacks[0].api_key_env == "NVIDIA_API_KEY"


def test_fallbacks_without_model_are_ignored(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n"
        "  fallbacks:\n"
        "    - provider: nvidia\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.llm.fallbacks == ()


def test_nvidia_request_shape(tmp_path, monkeypatch):
    """İstek OpenAI uyumlu biçimde, anahtar başlıkta taşınmalı."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key")
    state = State(tmp_path)

    with patch("src.llm.requests.post", return_value=nvidia_response()) as post:
        summarizer = Summarizer(
            config(
                provider="nvidia",
                model="nvidia/nemotron-3-ultra-550b-a55b",
                fallback_model="",
                api_key_env="NVIDIA_API_KEY",
            ),
            state,
        )
        summarizer.summarize([disclosure()])

    call = post.call_args
    assert call.args[0] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert not (call.kwargs.get("params") or {}).get("key")
    body = call.kwargs["json"]
    assert body["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["response_format"] == {"type": "json_object"}


def test_nvidia_retries_without_json_mode_on_400(tmp_path, monkeypatch):
    """JSON modunu desteklemeyen modelde istek şemasız tekrar denenir."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-key")
    state = State(tmp_path)

    rejected = MagicMock()
    rejected.status_code = 400
    rejected.text = "response_format is not supported"

    with patch(
        "src.llm.requests.post",
        side_effect=[rejected, nvidia_response()],
    ) as post:
        summarizer = Summarizer(
            config(
                provider="nvidia",
                model="nvidia/nemotron-3-ultra-550b-a55b",
                fallback_model="",
                api_key_env="NVIDIA_API_KEY",
            ),
            state,
        )
        result = summarizer.summarize([disclosure()])

    assert result["1"].text == "NVIDIA özeti"
    assert post.call_count == 2
    assert "response_format" not in post.call_args_list[1].kwargs["json"]
