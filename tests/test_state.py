from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from src.filter import Candidate
from src.models import Disclosure, RatingInfo, Summary
from src.state import State

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 11, 11, 20, 0, tzinfo=TZ)


def make_disclosure(summary: str = "İlk özet") -> Disclosure:
    return Disclosure(
        index="123",
        title="ÖRNEK A.Ş.",
        subject="Özel Durum Açıklaması",
        summary=summary,
        publish_date="11.11.2026 19:30:00",
    )


def test_new_then_same_then_updated(tmp_path):
    state = State(tmp_path)
    disclosure = make_disclosure()
    assert state.status(disclosure) == "new"

    candidate = Candidate(disclosure=disclosure, score=2, priority=0)
    state.mark_sent([candidate], NOW)
    assert state.status(disclosure) is None

    changed = replace(disclosure, summary="Güncellenmiş özet")
    assert state.status(changed) == "updated"


def test_save_and_reload(tmp_path):
    state = State(tmp_path)
    disclosure = make_disclosure()
    state.mark_sent(
        [Candidate(disclosure=disclosure, score=2, priority=0)], NOW
    )
    state.set_summary(
        disclosure.index,
        Summary(text="AI özeti", materiality="high", category="Kredi notu"),
        "test-model",
        NOW,
    )
    state.save(NOW)

    reloaded = State(tmp_path)
    assert reloaded.status(disclosure) is None
    summary = reloaded.get_summary(disclosure.index)
    assert summary is not None
    assert summary.text == "AI özeti"
    assert summary.materiality == "high"
    assert summary.category == "Kredi notu"


def test_rating_cache_and_content_hash(tmp_path):
    state = State(tmp_path)
    rating = RatingInfo(
        agency="JCR Avrasya",
        entity="ÖRNEK FON",
        current="CCC (tr)",
        previous="A (tr)",
        action="Not indirimi",
    )
    state.set_rating("123", rating, content_hash="hash-1", now=NOW)

    assert state.get_rating("123", "hash-1") == rating
    assert state.get_rating("123", "hash-2") is None


def test_old_entries_are_pruned(tmp_path):
    state = State(tmp_path)
    state.seen["old"] = {
        "published": "01.11.2026 10:00:00",
        "hash": "x",
        "sent_at": "2026-11-01T10:00:00+03:00",
    }
    state.save(NOW)

    reloaded = State(tmp_path)
    assert "old" not in reloaded.seen


def test_corrupt_state_is_backed_up(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{bozuk json", encoding="utf-8")
    state = State(tmp_path)
    assert state.seen == {}
    assert any(p.name.startswith("seen.json.corrupt-") for p in tmp_path.iterdir())