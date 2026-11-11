from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from .config import AppConfig
from .filter import Candidate
from .models import MATERIALITY_ORDER, RatingInfo, Summary


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


MATERIALITY_LABELS = {
    "critical": "KRİTİK",
    "high": "YÜKSEK",
    "normal": "",
    "low": "RUTİN",
}

MATERIALITY_COLORS = {
    "critical": ("#fee2e2", "#991b1b"),
    "high": ("#ffedd5", "#9a3412"),
    "normal": ("#e2e8f0", "#334155"),
    "low": ("#f1f5f9", "#64748b"),
}

ACTION_COLORS = {
    "Temerrüt": ("#991b1b", "#ffffff"),
    "Not indirimi": ("#dc2626", "#ffffff"),
    "Not artırımı": ("#15803d", "#ffffff"),
    "Teyit": ("#e2e8f0", "#334155"),
    "İzlemeye alındı": ("#fef3c7", "#92400e"),
    "Askıya alındı": ("#fef3c7", "#92400e"),
    "Güncelleme": ("#e0e7ff", "#3730a3"),
}


def _as_summary(value: Summary | str | None) -> Summary | None:
    if value is None:
        return None
    if isinstance(value, Summary):
        return value
    return Summary(text=str(value))


def _effective_materiality(
    candidate: Candidate,
    summary: Summary | None,
    rating: RatingInfo | None = None,
) -> str:
    values = [candidate.materiality]
    if summary is not None:
        values.append(summary.materiality)
    if rating is not None:
        values.append(rating.materiality)
    return min(values, key=lambda value: MATERIALITY_ORDER.get(value, 2))


def _badge(text: str, background: str, color: str) -> str:
    return (
        '<span style="display:inline-block;'
        f"background:{background};color:{color};"
        "font-size:11px;font-weight:700;letter-spacing:.02em;"
        'padding:2px 7px;border-radius:999px;margin-left:6px;">'
        f"{html.escape(text)}</span>"
    )


def _rating_action_badge(action: str) -> str:
    if not action or action == "—":
        return "—"
    background, color = ACTION_COLORS.get(action, ("#e2e8f0", "#334155"))
    return (
        '<span style="display:inline-block;'
        f"background:{background};color:{color};"
        "font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;"
        'white-space:nowrap;">'
        f"{html.escape(action)}</span>"
    )


def _rating_table_html(
    rows: list[tuple[Candidate, RatingInfo]]
) -> str:
    if not rows:
        return ""
    body_rows: list[str] = []
    for candidate, rating in rows:
        disclosure = candidate.disclosure
        body_rows.append(
            f"""
            <tr style="border-bottom:1px solid #e2e8f0;">
              <td style="padding:9px 6px;font-weight:600;color:#0f172a;line-height:1.35;">
                {html.escape(rating.entity or disclosure.title or "—")}
              </td>
              <td style="padding:9px 6px;color:#334155;white-space:nowrap;">
                {html.escape(rating.agency or "—")}
              </td>
              <td style="padding:9px 6px;color:#0f172a;font-weight:700;white-space:nowrap;">
                {html.escape(rating.display_note)}
              </td>
              <td style="padding:9px 6px;color:#334155;">{html.escape(rating.outlook or "—")}</td>
              <td style="padding:9px 6px;">{_rating_action_badge(rating.action)}</td>
              <td style="padding:9px 6px;">
                <a href="{html.escape(disclosure.url)}"
                   style="color:#1d4ed8;text-decoration:none;font-weight:600;">KAP</a>
              </td>
            </tr>
            """
        )
    return f"""
    <div style="margin-top:20px;border:1px solid #e2e8f0;border-radius:12px;
                background:#ffffff;overflow:hidden;">
      <div style="padding:12px 16px;background:#0f172a;color:#ffffff;
                  font-size:12px;font-weight:700;letter-spacing:.06em;
                  text-transform:uppercase;">
        Kredi Notu Bildirimleri ({len(rows)})
      </div>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead>
            <tr style="background:#f1f5f9;color:#475569;text-align:left;">
              <th style="padding:8px 6px;font-weight:700;">Firma</th>
              <th style="padding:8px 6px;font-weight:700;">Ajans</th>
              <th style="padding:8px 6px;font-weight:700;">Not</th>
              <th style="padding:8px 6px;font-weight:700;">Görünüm</th>
              <th style="padding:8px 6px;font-weight:700;">Aksiyon</th>
              <th style="padding:8px 6px;font-weight:700;">Link</th>
            </tr>
          </thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </div>
    </div>
    """


def _rating_table_text(rows: list[tuple[Candidate, RatingInfo]]) -> str:
    if not rows:
        return ""
    lines = [f"Kredi Notu Bildirimleri ({len(rows)})", "-" * 60]
    for candidate, rating in rows:
        disclosure = candidate.disclosure
        lines.append(f"Firma   : {rating.entity or disclosure.title or '—'}")
        lines.append(
            f"Ajans   : {rating.agency or '—'} | Not: {rating.display_note} | "
            f"Görünüm: {rating.outlook or '—'} | Aksiyon: {rating.action or '—'}"
        )
        lines.append(f"Link    : {disclosure.url}")
        lines.append("-" * 60)
    return "\n".join(lines)


def _summary_html(summary: Summary) -> str:
    detail_parts: list[str] = []
    if summary.red_flags:
        items = "".join(
            f"<li style='margin:2px 0;'>{html.escape(flag)}</li>"
            for flag in summary.red_flags
        )
        detail_parts.append(
            "<div style='margin-top:6px;'><strong>Riskler:</strong>"
            f"<ul style='margin:2px 0 0 18px;padding:0;'>{items}</ul></div>"
        )
    if summary.analysis_points:
        items = "".join(
            f"<li style='margin:2px 0;'>{html.escape(point)}</li>"
            for point in summary.analysis_points
        )
        detail_parts.append(
            "<div style='margin-top:6px;'><strong>Analiz noktaları:</strong>"
            f"<ul style='margin:2px 0 0 18px;padding:0;'>{items}</ul></div>"
        )

    category = ""
    if summary.category:
        category = (
            '<span style="display:inline-block;background:#dbeafe;color:#1e40af;'
            "font-size:11px;font-weight:700;padding:1px 7px;border-radius:999px;"
            f'margin-left:6px;">{html.escape(summary.category)}</span>'
        )

    return (
        '<div style="margin-top:10px;padding:10px 12px;background:#eff6ff;'
        'border-left:3px solid #3b82f6;border-radius:0 8px 8px 0;'
        'font-size:14px;line-height:1.55;color:#1e3a8a;">'
        f"<strong>AI analizi:</strong>{category}"
        f"<div style='margin-top:4px;'>{html.escape(summary.text)}</div>"
        f"{''.join(detail_parts)}</div>"
    )


def _item_html(
    candidate: Candidate,
    summary: Summary | None,
    rating: RatingInfo | None,
) -> str:
    disclosure = candidate.disclosure
    materiality = _effective_materiality(candidate, summary, rating)
    badges = ""
    if candidate.status == "updated":
        badges += _badge("GÜNCELLENDİ", "#fef3c7", "#92400e")
    label = MATERIALITY_LABELS.get(materiality, "")
    if label:
        background, color = MATERIALITY_COLORS.get(materiality, ("#e2e8f0", "#334155"))
        badges += _badge(label, background, color)

    rating_block = ""
    if rating is not None and (rating.current or rating.previous):
        rating_block = (
            '<div style="margin-top:10px;padding:8px 10px;background:#f8fafc;'
            'border:1px solid #e2e8f0;border-radius:8px;font-size:13px;'
            'line-height:1.5;color:#334155;">'
            "<strong>Kredi notu:</strong> "
            f"{html.escape(rating.entity or disclosure.title)} · "
            f"{html.escape(rating.agency or '—')} · "
            f"<strong>{html.escape(rating.display_note)}</strong> · "
            f"{html.escape(rating.action or '—')} · "
            f"Görünüm: {html.escape(rating.outlook or '—')}"
            "</div>"
        )

    ai_block = _summary_html(summary) if summary is not None else ""

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0;background:#ffffff;">
      <div style="font-size:12px;color:#64748b;margin-bottom:4px;">
        {html.escape(disclosure.time_label)} &middot; {html.escape(disclosure.subject)}
      </div>
      <div style="font-size:16px;font-weight:700;color:#0f172a;line-height:1.35;margin-bottom:8px;">
        {html.escape(disclosure.title)}{badges}
      </div>
      <div style="font-size:14px;line-height:1.55;color:#334155;">
        <strong>KAP özeti:</strong> {html.escape(disclosure.summary or "Özet bulunmuyor.")}
      </div>
      {rating_block}
      {ai_block}
      <div style="margin-top:12px;">
        <a href="{html.escape(disclosure.url)}"
           style="display:inline-block;background:#0f172a;color:#ffffff;text-decoration:none;
                  font-size:13px;font-weight:600;padding:8px 12px;border-radius:8px;">
          KAP'ta aç &rarr;
        </a>
      </div>
    </div>
    """


def _summary_text(summary: Summary | None) -> list[str]:
    if summary is None:
        return []
    lines = [f"AI özeti: {summary.text}"]
    if summary.category:
        lines.append(f"Kategori: {summary.category}")
    if summary.red_flags:
        lines.append("Riskler: " + "; ".join(summary.red_flags))
    if summary.analysis_points:
        lines.append("Analiz: " + "; ".join(summary.analysis_points))
    return lines


def _split_groups(
    candidates: list[Candidate],
    summaries: dict[str, Summary],
    ratings: dict[str, RatingInfo],
) -> list[tuple[str, list[Candidate]]]:
    def is_hot(item: Candidate) -> bool:
        summary = summaries.get(item.disclosure.index)
        rating = ratings.get(item.disclosure.index)
        materiality = _effective_materiality(item, summary, rating)
        return materiality in ("critical", "high") or item.priority < 10_000

    hot = [item for item in candidates if is_hot(item)]
    hot_ids = {id(item) for item in hot}
    others = [item for item in candidates if id(item) not in hot_ids]
    groups: list[tuple[str, list[Candidate]]] = []
    if hot:
        groups.append(("Analiz Gerektirenler", hot))
    if others:
        groups.append(("Diğer Bildirimler", others))
    return groups


def render_email(
    candidates: list[Candidate],
    summaries: dict[str, Summary | str],
    config: AppConfig,
    now: datetime,
    ratings: dict[str, RatingInfo] | None = None,
) -> RenderedEmail:
    ratings = ratings or {}
    parsed_summaries = {
        index: summary
        for index, value in summaries.items()
        if (summary := _as_summary(value)) is not None
    }

    def sort_key(item: Candidate) -> tuple[int, int, float]:
        summary = parsed_summaries.get(item.disclosure.index)
        rating = ratings.get(item.disclosure.index)
        materiality = _effective_materiality(item, summary, rating)
        published = item.disclosure.published_at
        timestamp = published.timestamp() if published else 0.0
        return (
            MATERIALITY_ORDER.get(materiality, 2),
            item.priority,
            -timestamp,
        )

    candidates = sorted(candidates, key=sort_key)

    new_count = sum(1 for item in candidates if item.status == "new")
    updated_count = sum(1 for item in candidates if item.status == "updated")
    parts: list[str] = []
    if new_count:
        parts.append(f"{new_count} yeni")
    if updated_count:
        parts.append(f"{updated_count} güncelleme")
    count_label = ", ".join(parts) or f"{len(candidates)} bildirim"

    materialities = [
        _effective_materiality(
            item,
            parsed_summaries.get(item.disclosure.index),
            ratings.get(item.disclosure.index),
        )
        for item in candidates
    ]
    critical_count = materialities.count("critical")
    high_count = materialities.count("high")
    priority_label = ""
    if critical_count:
        priority_label = f" | {critical_count} kritik"
    elif high_count:
        priority_label = f" | {high_count} önemli"

    stamp = now.strftime("%d.%m.%Y %H:%M")
    subject = (
        f"{config.email.subject_prefix} | {stamp} | {count_label}{priority_label}"
    )

    rating_rows = [
        (item, ratings[item.disclosure.index])
        for item in candidates
        if item.disclosure.index in ratings
        and (
            ratings[item.disclosure.index].current
            or ratings[item.disclosure.index].previous
        )
    ]
    rating_section_html = _rating_table_html(rating_rows)
    rating_section_text = _rating_table_text(rating_rows)

    groups = _split_groups(candidates, parsed_summaries, ratings)
    sections_html: list[str] = []
    sections_text: list[str] = []

    for title, items in groups:
        item_blocks = "".join(
            _item_html(
                item,
                parsed_summaries.get(item.disclosure.index),
                ratings.get(item.disclosure.index),
            )
            for item in items
        )
        sections_html.append(
            f"""
            <div style="margin-top:22px;">
              <div style="font-size:12px;font-weight:700;letter-spacing:.06em;
                          text-transform:uppercase;color:#64748b;margin-bottom:6px;">
                {html.escape(title)} ({len(items)})
              </div>
              {item_blocks}
            </div>
            """
        )

        text_lines = [f"{title} ({len(items)})", "=" * 60]
        for item in items:
            disclosure = item.disclosure
            summary = parsed_summaries.get(disclosure.index)
            rating = ratings.get(disclosure.index)
            materiality = _effective_materiality(item, summary, rating)
            materiality_label = MATERIALITY_LABELS.get(materiality, "")
            labels = []
            if item.status == "updated":
                labels.append("GÜNCELLENDİ")
            if materiality_label:
                labels.append(materiality_label)
            suffix = f" [{' | '.join(labels)}]" if labels else ""
            text_lines.append(
                f"[{disclosure.time_label}] {disclosure.title} — "
                f"{disclosure.subject}{suffix}"
            )
            if rating is not None and (rating.current or rating.previous):
                text_lines.append(
                    "Kredi notu: "
                    f"{rating.entity or disclosure.title} | {rating.agency or '—'} | "
                    f"{rating.display_note} | {rating.action or '—'} | "
                    f"Görünüm: {rating.outlook or '—'}"
                )
            text_lines.append(f"KAP: {disclosure.summary or 'Özet bulunmuyor.'}")
            text_lines.extend(_summary_text(summary))
            text_lines.append(f"Link: {disclosure.url}")
            text_lines.append("-" * 60)
        sections_text.append("\n".join(text_lines))

    text_parts = [
        subject,
        f"Kaynak: KAP | Oluşturma: {stamp}",
    ]
    if rating_section_text:
        text_parts.append(rating_section_text)
    text_parts.extend(sections_text)
    text_parts.append(
        "KAP özeti orijinal metindir. AI özeti bilgilendirme amaçlıdır; "
        "yatırım tavsiyesi değildir."
    )
    text_body = "\n\n".join(text_parts)

    html_body = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#1f2937;">
  <div style="max-width:760px;margin:0 auto;padding:24px 12px;">
    <div style="background:#0f172a;border-radius:16px 16px 0 0;padding:24px;">
      <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#93c5fd;font-weight:700;">
        KAP Bildirim Özeti
      </div>
      <div style="font-size:24px;font-weight:700;color:#ffffff;margin-top:6px;">
        {html.escape(stamp)}
      </div>
      <div style="font-size:14px;color:#cbd5e1;margin-top:4px;">
        {html.escape(count_label)}{html.escape(priority_label)}
      </div>
    </div>
    <div style="background:#f8fafc;border-radius:0 0 16px 16px;padding:8px 20px 24px;">
      {rating_section_html}
      {''.join(sections_html)}
      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;
                  font-size:12px;line-height:1.6;color:#64748b;">
        KAP özeti orijinal bildirim metninden alınmıştır. AI analizi bilgilendirme
        amaçlıdır; yatırım tavsiyesi değildir. Güncel ve tam metin için KAP linkini
        kullanın.
      </div>
    </div>
    <div style="text-align:center;font-size:11px;color:#94a3b8;padding:18px 8px;">
      Bu e-posta kap-notifier tarafından otomatik oluşturuldu.
    </div>
  </div>
</body>
</html>"""

    return RenderedEmail(subject=subject, html=html_body, text=text_body)