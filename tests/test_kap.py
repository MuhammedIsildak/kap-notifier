"""KAP ortak katmanı ve boşluk tespiti için testler."""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import RatingsConfig
from src.filter import Candidate
from src.kap import attachment_fingerprint, pdf_attachments
from src.models import Disclosure
from src.ratings import RatingExtractor
from src.state import State
from main import warn_if_gap

NOW = datetime.now(ZoneInfo("Europe/Istanbul"))


def disclosure(index: str = "1") -> Disclosure:
    return Disclosure(
        index=index,
        title="ÖRNEK A.Ş.",
        subject="Kredi Derecelendirmesi",
        summary="Kredi derecelendirme bildirimi",
        publish_date="12.11.2026 10:00:00",
    )


def _page(obj_id: str) -> str:
    attachments = [
        {
            "objId": obj_id,
            "fileName": "Not.pdf",
            "fileExtension": "pdf",
        }
    ]
    escaped = json.dumps(attachments).replace('"', '\\"')
    return (
        '<script>self.__next_f.push([1,"x\\"attachments\\":'
        f'{escaped}"])</script>'
        "<p>Derecelendirilen Kuruluş: ÖRNEK A.Ş.</p>"
        "<p>Kredi Derecelendirme Notu: BBB (tr)</p>"
    )


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.status_code = 200
        self.text = text
        self.content = text.encode()

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, pages: list[str]) -> None:
        self.pages = list(pages)
        self.calls = 0
        self.detail_calls = 0

    def get(self, url: str, *_args, **_kwargs):
        self.calls += 1
        if "/Bildirim/" in url:
            self.detail_calls += 1
            return _FakeResponse(self.pages.pop(0))
        # Ek indirmeleri geçersiz PDF ile yanıtlanır.
        return _FakeResponse("bu bir PDF degil")


def test_attachment_fingerprint_tracks_only_object_ids():
    attachments = [{"objId": "a", "fileName": "x.pdf"}, {"fileName": "y.pdf"}]
    assert attachment_fingerprint(attachments) == ("a",)


def test_pdf_attachments_filters_and_limits():
    attachments = [
        {"objId": "1", "fileName": "a.pdf"},
        {"objId": "2", "fileName": "b.xlsx"},
        {"objId": "3", "fileName": "c.pdf"},
    ]
    assert [item["objId"] for item in pdf_attachments(attachments, 2)] == ["1", "3"]


def test_rating_cache_is_reused_until_attachment_changes(tmp_path):
    state = State(tmp_path)
    session = _FakeSession([_page("obj-1"), _page("obj-1"), _page("obj-2")])
    extractor = RatingExtractor(RatingsConfig(), state, session=session)

    first = extractor.extract([disclosure()], now=NOW)
    assert first["1"].current == "BBB (tr)"
    assert first["1"].attachments == ("obj-1",)

    # Aynı ek: önbellek kullanılır, ek PDF indirilmez.
    second = extractor.extract([disclosure()], now=NOW)
    assert second["1"].attachments == ("obj-1",)

    # Ek değişti: kayıt yeniden çıkarılır.
    third = extractor.extract([disclosure()], now=NOW)
    assert third["1"].attachments == ("obj-2",)
    assert session.detail_calls == 3
    # 3 detay sayfası + 2 ek indirmesi (önbellekli turda indirme yok).
    assert session.calls == 5


def test_gap_is_reported_when_newer_notifications_are_missing(tmp_path, caplog):
    state = State(tmp_path)
    newer = Disclosure(
        index="9",
        title="YENİ A.Ş.",
        subject="Özel Durum Açıklaması",
        summary="Yeni bildirim",
        publish_date="12.11.2026 12:00:00",
    )
    state.mark_sent(
        [Candidate(disclosure=newer, score=1, priority=0)], now=NOW
    )

    older = [
        Disclosure(
            index="1",
            title="ESKİ A.Ş.",
            subject="Özel Durum Açıklaması",
            summary="Eski bildirim",
            publish_date="11.11.2026 09:00:00",
        )
    ]
    with caplog.at_level(logging.WARNING):
        warn_if_gap(older, state)

    assert "Olası boşluk" in caplog.text


def test_no_gap_warning_when_notifications_are_current(tmp_path, caplog):
    state = State(tmp_path)
    newer = Disclosure(
        index="9",
        title="ESKİ A.Ş.",
        subject="Özel Durum Açıklaması",
        summary="Bildirim",
        publish_date="11.11.2026 09:00:00",
    )
    state.mark_sent(
        [Candidate(disclosure=newer, score=1, priority=0)], now=NOW
    )

    current = [
        Disclosure(
            index="1",
            title="YENİ A.Ş.",
            subject="Özel Durum Açıklaması",
            summary="Yeni bildirim",
            publish_date="12.11.2026 12:00:00",
        )
    ]
    with caplog.at_level(logging.WARNING):
        warn_if_gap(current, state)

    assert "Olası boşluk" not in caplog.text
