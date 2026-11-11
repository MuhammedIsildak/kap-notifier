from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

KAP_BASE_URL = "https://www.kap.org.tr/tr/Bildirim"
DATE_FORMAT = "%d.%m.%Y %H:%M:%S"


@dataclass(frozen=True)
class Disclosure:
    """KAP'ın `disclosure/list/light` uç noktasından gelen tek bir bildirim."""

    index: str
    title: str
    subject: str
    summary: str
    publish_date: str

    @classmethod
    def from_api(cls, raw: dict) -> "Disclosure":
        index = str(raw.get("disclosureIndex") or "").strip()
        if not index:
            raise ValueError("disclosureIndex eksik")
        return cls(
            index=index,
            title=str(raw.get("title") or "").strip(),
            subject=str(raw.get("subject") or "").strip(),
            summary=str(raw.get("summary") or "").strip(),
            publish_date=str(raw.get("publishDate") or "").strip(),
        )

    @property
    def url(self) -> str:
        return f"{KAP_BASE_URL}/{self.index}"

    @property
    def published_at(self) -> datetime | None:
        try:
            return datetime.strptime(self.publish_date, DATE_FORMAT)
        except (ValueError, TypeError):
            return None

    @property
    def time_label(self) -> str:
        dt = self.published_at
        return dt.strftime("%H:%M") if dt else self.publish_date

    def content_hash(self) -> str:
        """Aynı index'in içeriği güncellendi mi anlamak için."""
        payload = "\x1f".join(
            (self.title, self.subject, self.summary, self.publish_date)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]