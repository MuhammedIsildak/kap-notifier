from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import load_config
from src.filter import Candidate
from src.models import Disclosure
from src.render import render_email

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 11, 11, 20, 5, tzinfo=TZ)


def test_render_escapes_html_and_contains_link():
    config = load_config(ROOT / "config.yaml")
    disclosure = Disclosure(
        index="42",
        title="<script>alert(1)</script> ŞİRKET",
        subject="Özel Durum Açıklaması",
        summary="Özet <b>kalın</b>",
        publish_date="11.11.2026 20:00:00",
    )
    candidate = Candidate(disclosure=disclosure, score=2, priority=0)
    rendered = render_email([candidate], {"42": "AI <özet>"}, config, NOW)

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "https://www.kap.org.tr/tr/Bildirim/42" in rendered.html
    assert "1 yeni" in rendered.subject
    assert "AI özeti" in rendered.text


def test_updated_badge():
    config = load_config(ROOT / "config.yaml")
    disclosure = Disclosure(
        index="43",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması",
        summary="Özet",
        publish_date="11.11.2026 20:00:00",
    )
    candidate = Candidate(
        disclosure=disclosure, score=2, priority=0, status="updated"
    )
    rendered = render_email([candidate], {}, config, NOW)
    assert "GÜNCELLENDİ" in rendered.html
    assert "1 güncelleme" in rendered.subject