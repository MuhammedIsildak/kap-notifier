from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime

from .config import AppConfig
from .filter import Candidate


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


def _split_groups(candidates: list[Candidate]) -> list[tuple[str, list[Candidate]]]:
    priority = [item for item in candidates if item.priority < 10_000]
    others = [item for item in candidates if item.priority >= 10_000]
    groups: list[tuple[str, list[Candidate]]] = []
    if priority:
        groups.append(("Öncelikli Bildirimler", priority))
    if others:
        groups.append(("Diğer Bildirimler", others))
    return groups


def _item_html(candidate: Candidate, ai_summary: str | None) -> str:
    disclosure = candidate.disclosure
    badge = ""
    if candidate.status == "updated":
        badge = (
            '<span style="display:inline-block;background:#fef3c7;color:#92400e;'
            'font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;'
            'margin-left:6px;">GÜNCELLENDİ</span>'
        )

    ai_block = ""
    if ai_summary:
        ai_block = (
            '<div style="margin-top:10px;padding:10px 12px;background:#eff6ff;'
            'border-left:3px solid #3b82f6;border-radius:0 8px 8px 0;'
            'font-size:14px;line-height:1.55;color:#1e3a8a;">'
            '<strong>AI özeti:</strong> '
            f"{html.escape(ai_summary)}</div>"
        )

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0;background:#ffffff;">
      <div style="font-size:12px;color:#64748b;margin-bottom:4px;">
        {html.escape(disclosure.time_label)} &middot; {html.escape(disclosure.subject)}
      </div>
      <div style="font-size:16px;font-weight:700;color:#0f172a;line-height:1.35;margin-bottom:8px;">
        {html.escape(disclosure.title)}{badge}
      </div>
      <div style="font-size:14px;line-height:1.55;color:#334155;">
        <strong>KAP özeti:</strong> {html.escape(disclosure.summary or "Özet bulunmuyor.")}
      </div>
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


def render_email(
    candidates: list[Candidate],
    summaries: dict[str, str],
    config: AppConfig,
    now: datetime,
) -> RenderedEmail:
    new_count = sum(1 for item in candidates if item.status == "new")
    updated_count = sum(1 for item in candidates if item.status == "updated")
    parts: list[str] = []
    if new_count:
        parts.append(f"{new_count} yeni")
    if updated_count:
        parts.append(f"{updated_count} güncelleme")
    count_label = ", ".join(parts) or f"{len(candidates)} bildirim"

    stamp = now.strftime("%d.%m.%Y %H:%M")
    subject = f"{config.email.subject_prefix} | {stamp} | {count_label}"

    groups = _split_groups(candidates)
    sections_html: list[str] = []
    sections_text: list[str] = []

    for title, items in groups:
        item_blocks = "".join(
            _item_html(item, summaries.get(item.disclosure.index)) for item in items
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
            status = " [GÜNCELLENDİ]" if item.status == "updated" else ""
            text_lines.append(
                f"[{disclosure.time_label}] {disclosure.title} — {disclosure.subject}{status}"
            )
            text_lines.append(f"KAP: {disclosure.summary or 'Özet bulunmuyor.'}")
            ai_summary = summaries.get(disclosure.index)
            if ai_summary:
                text_lines.append(f"AI: {ai_summary}")
            text_lines.append(f"Link: {disclosure.url}")
            text_lines.append("-" * 60)
        sections_text.append("\n".join(text_lines))

    html_body = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#1f2937;">
  <div style="max-width:720px;margin:0 auto;padding:24px 12px;">
    <div style="background:#0f172a;border-radius:16px 16px 0 0;padding:24px;">
      <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#93c5fd;font-weight:700;">
        KAP Bildirim Özeti
      </div>
      <div style="font-size:24px;font-weight:700;color:#ffffff;margin-top:6px;">
        {html.escape(stamp)}
      </div>
      <div style="font-size:14px;color:#cbd5e1;margin-top:4px;">
        {html.escape(count_label)}
      </div>
    </div>
    <div style="background:#f8fafc;border-radius:0 0 16px 16px;padding:8px 20px 24px;">
      {''.join(sections_html)}
      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;
                  font-size:12px;line-height:1.6;color:#64748b;">
        KAP özeti orijinal bildirim metninden alınmıştır. AI özeti bilgilendirme
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

    text_body = "\n\n".join(
        [
            f"{subject}",
            f"Kaynak: KAP | Oluşturma: {stamp}",
            *sections_text,
            "KAP özeti orijinal metindir. AI özeti bilgilendirme amaçlıdır; yatırım tavsiyesi değildir.",
        ]
    )

    return RenderedEmail(subject=subject, html=html_body, text=text_body)