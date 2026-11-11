from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

KAP_BASE_URL = "https://www.kap.org.tr/tr/Bildirim"
DATE_FORMAT = "%d.%m.%Y %H:%M:%S"

MATERIALITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}
MATERIALITY_VALUES = tuple(MATERIALITY_ORDER)


@dataclass(frozen=True)
class RatingInfo:
    """Bir kredi derecelendirme bildiriminden çıkarılan özet bilgi."""

    agency: str = ""
    entity: str = ""
    current: str = ""
    previous: str = ""
    outlook: str = ""
    action: str = ""
    details: tuple[str, ...] = ()
    red_flags: tuple[str, ...] = ()
    source: str = ""

    @property
    def display_note(self) -> str:
        if not self.current:
            return "—"
        if self.previous and self.previous != self.current:
            return f"{self.current} (önceki {self.previous})"
        return self.current

    @property
    def materiality(self) -> str:
        """Kredi notu aksiyonundan maddiyet seviyesi türetir."""
        current = (self.current or "").upper()
        if self.action == "Temerrüt" or current.startswith("TR D") or current.startswith(
            "D ("
        ):
            return "critical"
        if self.action in {"Not indirimi", "İzlemeye alındı", "Askıya alındı"}:
            return "high"
        return "normal"

    def fallback_summary(self) -> str:
        """LLM yokken kullanılacak deterministik özet."""
        entity = self.entity or "İlgili kuruluş"
        agency = self.agency or "derecelendirme kuruluşu"
        parts: list[str] = []
        if self.previous and self.current and self.previous != self.current:
            parts.append(
                f"kredi notu {self.previous} seviyesinden {self.current} "
                f"seviyesine güncellendi ({self.action or 'revizyon'})"
            )
        elif self.current:
            parts.append(f"kredi notu {self.current} olarak açıklandı")
        else:
            parts.append("kredi notu bildirimi yayımlandı")
        if self.outlook and self.outlook != "—":
            parts.append(f"görünüm/statü: {self.outlook}")
        if self.red_flags:
            risk = self.red_flags[0].rstrip(".,;:")
            if len(risk) > 200:
                risk = risk[:197].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
            parts.append("risk: " + risk)
        summary = f"{entity} için {agency} değerlendirmesi: " + "; ".join(parts)
        summary = re.sub(r"([.!?])\1+$", r"\1", summary.strip())
        if not summary.endswith((".", "!", "?", "…")):
            summary += "."
        return summary

    def to_dict(self) -> dict:
        return {
            "agency": self.agency,
            "entity": self.entity,
            "current": self.current,
            "previous": self.previous,
            "outlook": self.outlook,
            "action": self.action,
            "details": list(self.details),
            "red_flags": list(self.red_flags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RatingInfo":
        return cls(
            agency=str(data.get("agency") or ""),
            entity=str(data.get("entity") or ""),
            current=str(data.get("current") or ""),
            previous=str(data.get("previous") or ""),
            outlook=str(data.get("outlook") or ""),
            action=str(data.get("action") or ""),
            details=tuple(str(item) for item in data.get("details") or ()),
            red_flags=tuple(str(item) for item in data.get("red_flags") or ()),
            source=str(data.get("source") or ""),
        )


@dataclass(frozen=True)
class Summary:
    """LLM'in ürettiği analist odaklı özet."""

    text: str
    materiality: str = "normal"
    category: str = ""
    red_flags: tuple[str, ...] = ()
    analysis_points: tuple[str, ...] = ()
    has_document: bool = False

    def to_dict(self) -> dict:
        return {
            "summary": self.text,
            "materiality": self.materiality,
            "category": self.category,
            "red_flags": list(self.red_flags),
            "analysis_points": list(self.analysis_points),
            "has_document": self.has_document,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Summary":
        materiality = str(data.get("materiality") or "normal").strip().lower()
        if materiality not in MATERIALITY_ORDER:
            materiality = "normal"
        return cls(
            text=str(data.get("summary") or data.get("text") or ""),
            materiality=materiality,
            category=str(data.get("category") or ""),
            red_flags=tuple(str(item) for item in data.get("red_flags") or ()),
            analysis_points=tuple(
                str(item) for item in data.get("analysis_points") or ()
            ),
            has_document=bool(data.get("has_document")),
        )


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