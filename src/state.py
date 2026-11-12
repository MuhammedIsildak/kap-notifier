from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from .filter import Candidate
from .models import DATE_FORMAT, Disclosure, RatingInfo, Summary

LOGGER = logging.getLogger(__name__)

SEEN_RETENTION_DAYS = 7
SUMMARY_RETENTION_DAYS = 14
RATING_RETENTION_DAYS = 14


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        backup = path.with_suffix(path.suffix + f".corrupt-{int(datetime.now().timestamp())}")
        LOGGER.warning("State dosyası okunamadı (%s); yedekleniyor: %s", exc, backup)
        try:
            path.replace(backup)
        except OSError:
            pass
        return {}
    return data if isinstance(data, dict) else {}


class State:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser()
        self.seen_path = self.directory / "seen.json"
        self.summaries_path = self.directory / "summaries.json"
        self.ratings_path = self.directory / "ratings.json"
        self.seen: dict[str, dict] = {}
        self.summaries: dict[str, dict] = {}
        self.ratings: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        seen_data = _load_json(self.seen_path)
        raw_seen = seen_data.get("seen")
        if isinstance(raw_seen, dict):
            self.seen = {
                str(index): entry
                for index, entry in raw_seen.items()
                if isinstance(entry, dict)
            }

        summaries_data = _load_json(self.summaries_path)
        raw_summaries = summaries_data.get("items")
        if isinstance(raw_summaries, dict):
            self.summaries = {
                str(index): entry
                for index, entry in raw_summaries.items()
                if isinstance(entry, dict)
            }

        ratings_data = _load_json(self.ratings_path)
        raw_ratings = ratings_data.get("items")
        if isinstance(raw_ratings, dict):
            self.ratings = {
                str(index): entry
                for index, entry in raw_ratings.items()
                if isinstance(entry, dict)
            }

    def status(self, disclosure: Disclosure) -> str | None:
        """new, updated veya daha önce aynen gönderildiyse None."""
        entry = self.seen.get(disclosure.index)
        if not entry:
            return "new"
        if entry.get("hash") != disclosure.content_hash():
            return "updated"
        return None

    def mark_sent(self, candidates: list[Candidate], now: datetime) -> None:
        sent_at = now.isoformat()
        for candidate in candidates:
            disclosure = candidate.disclosure
            self.seen[disclosure.index] = {
                "published": disclosure.publish_date,
                "hash": disclosure.content_hash(),
                "sent_at": sent_at,
            }

    def get_summary(
        self, index: str, content_hash: str | None = None
    ) -> Summary | None:
        """Önbellekteki özeti döndürür.

        content_hash verilirse, bildirim içeriği değiştiğinde eski özet
        kullanılmaz; çağıran taraf yeniden özetlemek için None alır.
        """
        entry = self.summaries.get(index) or {}
        summary = entry.get("summary")
        if not summary:
            return None
        if content_hash and entry.get("hash") != content_hash:
            return None
        materiality = str(entry.get("materiality") or "normal").strip().lower()
        return Summary(
            text=str(summary),
            materiality=materiality,
            category=str(entry.get("category") or ""),
            red_flags=tuple(str(item) for item in entry.get("red_flags") or ()),
            analysis_points=tuple(
                str(item) for item in entry.get("analysis_points") or ()
            ),
            has_document=bool(entry.get("has_document")),
        )

    def set_summary(
        self,
        index: str,
        summary: Summary,
        model: str,
        now: datetime,
        content_hash: str | None = None,
    ) -> None:
        payload = summary.to_dict()
        payload["model"] = model
        payload["ts"] = now.isoformat()
        if content_hash:
            payload["hash"] = content_hash
        self.summaries[index] = payload

    def get_rating(
        self, index: str, content_hash: str | None = None
    ) -> RatingInfo | None:
        entry = self.ratings.get(index) or {}
        if content_hash and entry.get("hash") != content_hash:
            return None
        payload = entry.get("info")
        if not isinstance(payload, dict):
            return None
        return RatingInfo.from_dict(payload)

    def set_rating(
        self,
        index: str,
        rating: RatingInfo,
        content_hash: str,
        now: datetime,
    ) -> None:
        self.ratings[index] = {
            "hash": content_hash,
            "info": rating.to_dict(),
            "ts": now.isoformat(),
        }

    def _prune(self, now: datetime) -> None:
        seen_cutoff = now - timedelta(days=SEEN_RETENTION_DAYS)
        for index, entry in list(self.seen.items()):
            published = entry.get("published")
            sent_at = entry.get("sent_at")
            reference: datetime | None = None
            if published:
                try:
                    reference = datetime.strptime(str(published), DATE_FORMAT).replace(
                        tzinfo=now.tzinfo
                    )
                except ValueError:
                    reference = None
            if reference is None and sent_at:
                try:
                    reference = datetime.fromisoformat(str(sent_at))
                except ValueError:
                    reference = None
            if reference is not None and reference < seen_cutoff:
                self.seen.pop(index, None)

        summary_cutoff = now - timedelta(days=SUMMARY_RETENTION_DAYS)
        for index, entry in list(self.summaries.items()):
            ts = entry.get("ts")
            reference: datetime | None = None
            if ts:
                try:
                    reference = datetime.fromisoformat(str(ts))
                except ValueError:
                    reference = None
            if reference is not None and reference < summary_cutoff:
                self.summaries.pop(index, None)

        rating_cutoff = now - timedelta(days=RATING_RETENTION_DAYS)
        for index, entry in list(self.ratings.items()):
            ts = entry.get("ts")
            reference: datetime | None = None
            if ts:
                try:
                    reference = datetime.fromisoformat(str(ts))
                except ValueError:
                    reference = None
            if reference is not None and reference < rating_cutoff:
                self.ratings.pop(index, None)

    def save(self, now: datetime) -> None:
        self._prune(now)
        updated_at = now.isoformat()
        _atomic_write(
            self.seen_path,
            {"version": 1, "updated_at": updated_at, "seen": self.seen},
        )
        self.save_summaries(now)
        self.save_ratings(now)

    def save_summaries(self, now: datetime) -> None:
        self._prune(now)
        _atomic_write(
            self.summaries_path,
            {
                "version": 1,
                "updated_at": now.isoformat(),
                "items": self.summaries,
            },
        )

    def save_ratings(self, now: datetime) -> None:
        self._prune(now)
        _atomic_write(
            self.ratings_path,
            {
                "version": 1,
                "updated_at": now.isoformat(),
                "items": self.ratings,
            },
        )