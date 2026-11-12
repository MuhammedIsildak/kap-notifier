"""KAP erişim katmanının hata yolları."""

import pytest
import requests

from src.fetch import FetchError, fetch_disclosures
from src.kap import KapError, get_bytes_with_retry, get_with_retry


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode()

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self  # type: ignore[arg-type]
            )


class _FakeSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_retryable_status_is_retried_then_raises(monkeypatch):
    monkeypatch.setattr("src.kap.time.sleep", lambda _seconds: None)
    session = _FakeSession([_FakeResponse(503), _FakeResponse(503), _FakeResponse(503)])

    with pytest.raises(KapError):
        get_with_retry(session, "https://kap.example/x", timeout=5, retries=3)

    assert session.calls == 3


def test_client_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("src.kap.time.sleep", lambda _seconds: None)
    session = _FakeSession([_FakeResponse(404)])

    with pytest.raises(requests.HTTPError):
        get_with_retry(session, "https://kap.example/x", timeout=5, retries=3)

    assert session.calls == 1


def test_oversized_attachment_is_rejected():
    session = _FakeSession([_FakeResponse(200, "x" * 100)])

    with pytest.raises(KapError):
        get_bytes_with_retry(
            session, "https://kap.example/f", timeout=5, max_bytes=10
        )


def test_fetch_maps_client_error_to_fetch_error():
    session = _FakeSession([_FakeResponse(403, "reddedildi")])

    with pytest.raises(FetchError) as excinfo:
        fetch_disclosures(session=session)

    assert "403" in str(excinfo.value)


def test_fetch_maps_unreachable_to_fetch_error(monkeypatch):
    monkeypatch.setattr("src.kap.time.sleep", lambda _seconds: None)
    session = _FakeSession([_FakeResponse(500), _FakeResponse(500), _FakeResponse(500)])

    with pytest.raises(FetchError):
        fetch_disclosures(session=session)


def test_fetch_rejects_non_json_payload():
    session = _FakeSession([_FakeResponse(200, "html degil")])

    with pytest.raises(FetchError) as excinfo:
        fetch_disclosures(session=session)

    assert "JSON" in str(excinfo.value)
