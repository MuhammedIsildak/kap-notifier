from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import FilterConfig
from .models import Disclosure
from .normalize import matching_terms, normalize, subject_rank

LOGGER = logging.getLogger(__name__)


@dataclass
class Candidate:
    disclosure: Disclosure
    score: int
    priority: int
    reasons: list[str] = field(default_factory=list)
    status: str = "new"  # new | updated

    @property
    def sort_key(self) -> tuple[int, float]:
        published = self.disclosure.published_at
        timestamp = published.timestamp() if published else 0.0
        return (self.priority, -timestamp)


def evaluate(
    disclosure: Disclosure,
    filters: FilterConfig,
    companies: tuple[str, ...] = (),
) -> Candidate | None:
    """Bir bildirimi kurallara göre değerlendirir.

    None dönerse bildirim gönderilmez.
    """
    title = normalize(disclosure.title)
    subject = normalize(disclosure.subject)
    full_text = f"{disclosure.title} {disclosure.subject} {disclosure.summary}"

    # 1) Şirket filtresi (doluysa sert filtre)
    company_match = False
    if companies:
        company_match = any(normalize(company) in title for company in companies)
        if not company_match:
            return None

    # 2) Gürültü / istenmeyen türler
    for term in filters.subject_deny:
        if normalize(term) in subject:
            return None
    if matching_terms(full_text, filters.exclude_keywords):
        return None

    # 3) Tür allowlist'i
    subject_match = False
    if filters.subject_allow:
        subject_match = any(normalize(term) in subject for term in filters.subject_allow)
        if not subject_match:
            return None

    # 4) Anahtar kelimeler
    keyword_hits = matching_terms(full_text, filters.include_keywords)
    if filters.require_keyword and not keyword_hits:
        return None

    score = 0
    reasons: list[str] = []
    if subject_match:
        score += 2
        reasons.append("tür")
    if company_match:
        score += 2
        reasons.append("şirket")
    if keyword_hits:
        score += min(len(keyword_hits), 3)
        reasons.append("kelime:" + ",".join(keyword_hits[:3]))

    if score < filters.min_score:
        return None

    return Candidate(
        disclosure=disclosure,
        score=score,
        priority=subject_rank(disclosure.subject, filters.priority_subjects),
        reasons=reasons,
    )


def select_candidates(
    disclosures: list[Disclosure],
    filters: FilterConfig,
    companies: tuple[str, ...] = (),
) -> list[Candidate]:
    """Filtreler, aynı index için en yüksek skoru tutar ve sıralar."""
    by_index: dict[str, Candidate] = {}
    for disclosure in disclosures:
        candidate = evaluate(disclosure, filters, companies)
        if candidate is None:
            continue
        current = by_index.get(disclosure.index)
        if current is None or candidate.score > current.score:
            by_index[disclosure.index] = candidate

    return sorted(by_index.values(), key=lambda item: item.sort_key)