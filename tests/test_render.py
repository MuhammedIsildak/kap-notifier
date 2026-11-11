from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import load_config
from src.filter import Candidate
from src.models import Disclosure, RatingInfo, Summary
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


def test_rating_table_and_critical_badge():
    config = load_config(ROOT / "config.yaml")
    disclosure = Disclosure(
        index="44",
        title="JCR AVRASYA DERECELENDİRME A.Ş.",
        subject="Kredi Derecelendirmesi",
        summary="KREDİ DERECELENDİRME BİLDİRİMİ",
        publish_date="11.11.2026 20:00:00",
    )
    candidate = Candidate(
        disclosure=disclosure,
        score=4,
        priority=0,
        materiality="critical",
    )
    rating = RatingInfo(
        agency="JCR Avrasya",
        entity="ÖRNEK FON",
        current="CCC (tr)",
        previous="A (tr)",
        outlook="—",
        action="Not indirimi",
        red_flags=("Nakit akışı bozuldu",),
    )
    summary = Summary(
        text="Not iki kademe indirildi.",
        materiality="critical",
        category="Kredi notu",
        analysis_points=("Nakit akışını izle",),
    )
    rendered = render_email(
        [candidate], {"44": summary}, config, NOW, ratings={"44": rating}
    )

    assert "Kredi Notu Bildirimleri" in rendered.html
    assert "ÖRNEK FON" in rendered.html
    assert "CCC (tr)" in rendered.html
    assert "Not indirimi" in rendered.html
    assert "KRİTİK" in rendered.html
    assert "1 kritik" in rendered.subject
    assert "Kredi Notu Bildirimleri" in rendered.text


def test_rating_default_escalates_materiality():
    config = load_config(ROOT / "config.yaml")
    disclosure = Disclosure(
        index="46",
        title="İSTANBUL ULUSLARARASI DERECELENDİRME HİZMETLERİ A.Ş.",
        subject="Kredi Derecelendirmesi",
        summary="Kredi Derecelendirme Not Güncellemesi",
        publish_date="11.11.2026 20:00:00",
    )
    candidate = Candidate(
        disclosure=disclosure, score=2, priority=10_000, materiality="normal"
    )
    rating = RatingInfo(
        agency="Turkrating",
        entity="Dünya Varlık Yönetim A.Ş.",
        current="TR D",
        outlook="Temerrüt (Askıda)",
        action="Temerrüt",
    )
    rendered = render_email(
        [candidate],
        {"46": Summary(text="Rutin güncelleme", materiality="normal")},
        config,
        NOW,
        ratings={"46": rating},
    )
    assert "KRİTİK" in rendered.html
    assert "1 kritik" in rendered.subject
    assert "Analiz Gerektirenler" in rendered.text


def test_critical_filter_materiality_wins_over_ai_normal():
    config = load_config(ROOT / "config.yaml")
    disclosure = Disclosure(
        index="45",
        title="ŞİRKET",
        subject="Özel Durum Açıklaması",
        summary="Temerrüt açıklaması",
        publish_date="11.11.2026 20:00:00",
    )
    candidate = Candidate(
        disclosure=disclosure, score=4, priority=0, materiality="critical"
    )
    rendered = render_email(
        [candidate], {"45": Summary(text="Rutin görünüyor", materiality="normal")}, config, NOW
    )
    assert "KRİTİK" in rendered.html