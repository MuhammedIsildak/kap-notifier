from pathlib import Path

from src.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_empty_smtp_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("KAP_SMTP_HOST", "")
    monkeypatch.setenv("KAP_SMTP_PORT", "")
    monkeypatch.setenv("KAP_EMAIL_FROM", "")
    monkeypatch.setenv("KAP_SMTP_USER", "ornek@gmail.com")
    config = load_config(ROOT / "config.yaml")
    assert config.email.smtp_host == "smtp.gmail.com"
    assert config.email.smtp_port == 465
    assert config.email.effective_from == "ornek@gmail.com"


def test_recipients_are_split(monkeypatch):
    monkeypatch.setenv("KAP_EMAIL_TO", "a@example.com, b@example.com ,")
    config = load_config(ROOT / "config.yaml")
    assert config.email.recipients == ("a@example.com", "b@example.com")