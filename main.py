from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from src.config import AppConfig, ConfigError, load_config
from src.documents import DocumentFetcher
from src.fetch import FetchError, fetch_disclosures, fetch_from_file
from src.filter import Candidate, select_candidates
from src.mailer import MailerError, send_email
from src.models import DATE_FORMAT, MATERIALITY_ORDER, Disclosure, Summary
from src.ratings import RatingExtractor
from src.render import RenderedEmail, render_email
from src.state import State
from src.summarize import Summarizer

LOGGER = logging.getLogger("kap-notifier")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KAP bildirimlerini tarar, filtreler ve e-posta gönderir."
    )
    parser.add_argument("--config", default="config.yaml", help="Ayar dosyası yolu")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="E-posta göndermez, state yazmaz; önizleme üretir",
    )
    parser.add_argument(
        "--input",
        help="KAP yerine yerel JSON dosyasından oku (test için)",
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="KAP'ı çalıştırmadan test e-postası gönder",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Sessiz saatleri yok say",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="LLM özetini kapat",
    )
    parser.add_argument(
        "--no-ratings",
        action="store_true",
        help="Kredi notu eklerini indirme/çıkarma",
    )
    parser.add_argument(
        "--no-documents",
        action="store_true",
        help="PDF eklerini indirme/okuma",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Test için en fazla N bildirim işle",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Ayrıntılı log",
    )
    return parser.parse_args(argv)


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        LOGGER.warning("Saat dilimi bulunamadı (%s); UTC kullanılacak", name)
        return ZoneInfo("UTC")


def parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def in_quiet_hours(now: datetime, config: AppConfig) -> bool:
    quiet = config.quiet_hours
    if not quiet.enabled:
        return False
    try:
        start = parse_clock(quiet.start)
        end = parse_clock(quiet.end)
    except ValueError:
        LOGGER.warning("Sessiz saat formatı geçersiz: %s-%s", quiet.start, quiet.end)
        return False
    if start == end:
        return False
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def collect_new(candidates: list[Candidate], state: State) -> list[Candidate]:
    selected: list[Candidate] = []
    for candidate in candidates:
        status = state.status(candidate.disclosure)
        if status is None:
            continue
        candidate.status = status
        selected.append(candidate)
    return selected


def write_preview(
    rendered: RenderedEmail, now: datetime, suffix: str = ""
) -> Path:
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = now.strftime("preview_%Y%m%d_%H%M%S") + suffix
    html_path = out_dir / f"{stem}.html"
    text_path = out_dir / f"{stem}.txt"
    html_path.write_text(rendered.html, encoding="utf-8")
    text_path.write_text(rendered.text, encoding="utf-8")
    return html_path


def print_candidates(candidates: list[Candidate]) -> None:
    for item in candidates:
        disclosure = item.disclosure
        LOGGER.info(
            "- %s | %s | %s | skor=%s | %s%s",
            disclosure.time_label,
            disclosure.title[:40],
            disclosure.subject[:60],
            item.score,
            item.materiality,
            " | GÜNCELLENDİ" if item.status == "updated" else "",
        )


def build_test_email(config: AppConfig, now: datetime) -> RenderedEmail:
    disclosure = Disclosure(
        index="test",
        title="TEST ŞİRKETİ A.Ş.",
        subject="Özel Durum Açıklaması (Test)",
        summary="Bu, kap-notifier e-posta ayarlarını doğrulamak için gönderilen bir test bildirimidir.",
        publish_date=now.strftime(DATE_FORMAT),
    )
    candidate = Candidate(
        disclosure=disclosure,
        score=2,
        priority=0,
        reasons=["test"],
        status="new",
    )
    rendered = render_email(
        [candidate],
        {
            disclosure.index: Summary(
                text="E-posta gönderimi çalışıyor. Bu özet test amaçlı üretilmiştir.",
                category="Test",
            )
        },
        config,
        now,
    )
    return replace(rendered, subject=f"TEST | {rendered.subject}")


def run(args: argparse.Namespace) -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    config = load_config(args.config)
    now = datetime.now(get_timezone(config.timezone))

    if args.send_test:
        rendered = build_test_email(config, now)
        send_email(config.email, rendered)
        LOGGER.info("Test e-postası gönderildi: %s", ", ".join(config.email.recipients))
        return 0

    is_fixture = bool(args.input)
    if not args.dry_run and not args.force and not is_fixture:
        if in_quiet_hours(now, config):
            LOGGER.info(
                "Sessiz saatler (%s-%s); e-posta gönderilmedi",
                config.quiet_hours.start,
                config.quiet_hours.end,
            )
            return 0

    if args.no_llm:
        config = replace(config, llm=replace(config.llm, enabled=False))

    disclosures = (
        fetch_from_file(args.input) if args.input else fetch_disclosures()
    )
    candidates = select_candidates(disclosures, config.filters, config.companies)
    LOGGER.info("Filtreden %s aday geçti", len(candidates))

    state_dir = Path(os.getenv("KAP_STATE_DIR", "state"))
    state = State(state_dir)
    selected = collect_new(candidates, state)

    if args.limit > 0:
        selected = selected[: args.limit]

    if not selected:
        LOGGER.info("Gönderilecek yeni bildirim yok")
        return 0

    print_candidates(selected)

    summarizer = Summarizer(config.llm, state)
    rating_extractor = RatingExtractor(config.ratings, state)
    document_fetcher = DocumentFetcher(config.documents)
    ratings_enabled = (
        config.ratings.enabled and not args.no_ratings and not is_fixture
    )
    documents_enabled = (
        config.documents.enabled
        and not args.no_documents
        and not is_fixture
        and config.llm.enabled
    )
    chunk_size = max(1, config.filters.max_items)
    chunks = [
        selected[start : start + chunk_size]
        for start in range(0, len(selected), chunk_size)
    ]
    LOGGER.info("%s bildirim, %s e-posta halinde işlenecek", len(selected), len(chunks))

    for part, chunk in enumerate(chunks, start=1):
        ratings = (
            rating_extractor.extract([item.disclosure for item in chunk], now)
            if ratings_enabled
            else {}
        )
        if ratings and not args.dry_run:
            state.save_ratings(now)
            LOGGER.info("%s kredi notu bildirimi çıkarıldı", len(ratings))

        documents: dict[str, str] = {}
        if documents_enabled:
            # Önce kritik/yüksek maddiyetli bildirimlerin ekleri okunur.
            ordered = sorted(
                chunk,
                key=lambda item: (
                    MATERIALITY_ORDER.get(item.materiality, 2),
                    item.priority,
                ),
            )
            documents = document_fetcher.fetch(
                [item.disclosure for item in ordered],
                skip=set(ratings),
            )
            if documents:
                LOGGER.info(
                    "%s bildirimin PDF eki okundu", len(documents)
                )

        summaries = summarizer.summarize(
            [item.disclosure for item in chunk],
            ratings=ratings,
            documents=documents,
            now=now,
        )
        if summaries and not args.dry_run:
            state.save_summaries(now)
            LOGGER.info("%s bildirim için özet hazır", len(summaries))

        rendered = render_email(chunk, summaries, config, now, ratings=ratings)
        if len(chunks) > 1:
            rendered = replace(
                rendered, subject=f"{rendered.subject} ({part}/{len(chunks)})"
            )

        if args.dry_run:
            suffix = f"_{part}" if len(chunks) > 1 else ""
            preview = write_preview(rendered, now, suffix=suffix)
            LOGGER.info("DRY-RUN parça %s/%s: %s", part, len(chunks), preview)
            print(rendered.text)
            continue

        send_email(config.email, rendered)
        state.mark_sent(chunk, now)
        state.save(now)
        LOGGER.info(
            "Parça %s/%s gönderildi: %s bildirim (%s yeni, %s güncelleme)",
            part,
            len(chunks),
            len(chunk),
            sum(1 for item in chunk if item.status == "new"),
            sum(1 for item in chunk if item.status == "updated"),
        )

    if args.dry_run:
        LOGGER.info("DRY-RUN: e-posta gönderilmedi, state yazılmadı")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except (ConfigError, FetchError, MailerError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001 - GitHub Actions'ta yığın izini görmek için
        LOGGER.exception("Beklenmeyen hata")
        return 1


if __name__ == "__main__":
    sys.exit(main())