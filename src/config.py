from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(RuntimeError):
    pass


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "evet", "on"}


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AnalystConfig:
    """Mali analist için maddi etki kuralları."""

    enabled: bool = False
    drop_routine: bool = True
    routine_topics: tuple[str, ...] = ()
    override_keywords: tuple[str, ...] = ()
    critical_keywords: tuple[str, ...] = ()
    high_value_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class FilterConfig:
    subject_allow: tuple[str, ...] = ()
    subject_deny: tuple[str, ...] = ()
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    require_keyword: bool = False
    min_score: int = 1
    priority_subjects: tuple[str, ...] = ()
    max_items: int = 40
    analyst: AnalystConfig = field(default_factory=AnalystConfig)


@dataclass(frozen=True)
class QuietHoursConfig:
    enabled: bool = True
    start: str = "00:00"
    end: str = "08:00"


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool = True
    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    fallback_model: str = "gemini-3.5-flash-lite"
    api_key_env: str = "GEMINI_API_KEY"
    batch_size: int = 5
    max_items_per_run: int = 30
    timeout_seconds: int = 60


@dataclass(frozen=True)
class RatingsConfig:
    enabled: bool = True
    max_items_per_run: int = 25
    max_attachments: int = 2
    max_pdf_bytes: int = 15_000_000
    timeout_seconds: int = 45


@dataclass(frozen=True)
class DocumentsConfig:
    """Bildirim eklerindeki PDF'lerin okunması."""

    enabled: bool = True
    max_items_per_run: int = 8
    max_attachments: int = 2
    max_chars: int = 6000
    max_pdf_bytes: int = 15_000_000
    timeout_seconds: int = 30
    request_delay_seconds: float = 0.4


@dataclass(frozen=True)
class EmailConfig:
    subject_prefix: str = "KAP"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    from_addr: str = ""
    recipients: tuple[str, ...] = ()

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_user and self.smtp_pass and self.recipients)

    @property
    def effective_from(self) -> str:
        return self.from_addr or self.smtp_user


@dataclass(frozen=True)
class AppConfig:
    timezone: str = "Europe/Istanbul"
    companies: tuple[str, ...] = ()
    filters: FilterConfig = field(default_factory=FilterConfig)
    quiet_hours: QuietHoursConfig = field(default_factory=QuietHoursConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ratings: RatingsConfig = field(default_factory=RatingsConfig)
    documents: DocumentsConfig = field(default_factory=DocumentsConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Ayar dosyası bulunamadı: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Ayar dosyası bir sözlük olmalı: {path}")
    return data


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    raw = _read_yaml(config_path)
    filters_raw = raw.get("filters") or {}
    analyst_raw = filters_raw.get("analyst") or {}
    quiet_raw = raw.get("quiet_hours") or {}
    llm_raw = raw.get("llm") or {}
    ratings_raw = raw.get("ratings") or {}
    documents_raw = raw.get("documents") or {}
    email_raw = raw.get("email") or {}

    analyst = AnalystConfig(
        enabled=_as_bool(analyst_raw.get("enabled"), False),
        drop_routine=_as_bool(analyst_raw.get("drop_routine"), True),
        routine_topics=_as_tuple(analyst_raw.get("routine_topics")),
        override_keywords=_as_tuple(analyst_raw.get("override_keywords")),
        critical_keywords=_as_tuple(analyst_raw.get("critical_keywords")),
        high_value_keywords=_as_tuple(analyst_raw.get("high_value_keywords")),
    )

    filters = FilterConfig(
        subject_allow=_as_tuple(filters_raw.get("subject_allow")),
        subject_deny=_as_tuple(filters_raw.get("subject_deny")),
        include_keywords=_as_tuple(filters_raw.get("include_keywords")),
        exclude_keywords=_as_tuple(filters_raw.get("exclude_keywords")),
        require_keyword=_as_bool(filters_raw.get("require_keyword"), False),
        min_score=_as_int(filters_raw.get("min_score"), 1),
        priority_subjects=_as_tuple(filters_raw.get("priority_subjects")),
        max_items=max(1, _as_int(filters_raw.get("max_items"), 40)),
        analyst=analyst,
    )

    quiet_hours = QuietHoursConfig(
        enabled=_as_bool(quiet_raw.get("enabled"), True),
        start=str(quiet_raw.get("start") or "00:00"),
        end=str(quiet_raw.get("end") or "08:00"),
    )

    llm = LLMConfig(
        enabled=_as_bool(llm_raw.get("enabled"), True),
        provider=str(llm_raw.get("provider") or "gemini").lower(),
        model=str(llm_raw.get("model") or "gemini-3.5-flash"),
        fallback_model=str(
            llm_raw.get("fallback_model") or "gemini-3.5-flash-lite"
        ),
        api_key_env=str(llm_raw.get("api_key_env") or "GEMINI_API_KEY"),
        batch_size=max(1, _as_int(llm_raw.get("batch_size"), 5)),
        max_items_per_run=max(0, _as_int(llm_raw.get("max_items_per_run"), 30)),
        timeout_seconds=max(5, _as_int(llm_raw.get("timeout_seconds"), 60)),
    )

    ratings = RatingsConfig(
        enabled=_as_bool(ratings_raw.get("enabled"), True),
        max_items_per_run=max(0, _as_int(ratings_raw.get("max_items_per_run"), 25)),
        max_attachments=max(1, _as_int(ratings_raw.get("max_attachments"), 2)),
        max_pdf_bytes=max(
            1_000_000, _as_int(ratings_raw.get("max_pdf_bytes"), 15_000_000)
        ),
        timeout_seconds=max(5, _as_int(ratings_raw.get("timeout_seconds"), 45)),
    )

    documents = DocumentsConfig(
        enabled=_as_bool(documents_raw.get("enabled"), True),
        max_items_per_run=max(0, _as_int(documents_raw.get("max_items_per_run"), 8)),
        max_attachments=max(1, _as_int(documents_raw.get("max_attachments"), 2)),
        max_chars=max(1000, _as_int(documents_raw.get("max_chars"), 6000)),
        max_pdf_bytes=max(
            1_000_000, _as_int(documents_raw.get("max_pdf_bytes"), 15_000_000)
        ),
        timeout_seconds=max(5, _as_int(documents_raw.get("timeout_seconds"), 30)),
        request_delay_seconds=max(
            0.0, float(documents_raw.get("request_delay_seconds") or 0.4)
        ),
    )

    env_recipients = os.getenv("KAP_EMAIL_TO", "")
    if env_recipients:
        recipients = tuple(
            part.strip() for part in env_recipients.split(",") if part.strip()
        )
    else:
        recipients = _as_tuple(email_raw.get("to"))

    smtp_user = os.getenv("KAP_SMTP_USER", "").strip()
    smtp_pass = os.getenv("KAP_SMTP_PASS", "").replace(" ", "").strip()
    email = EmailConfig(
        subject_prefix=str(email_raw.get("subject_prefix") or "KAP"),
        smtp_host=os.getenv("KAP_SMTP_HOST", "").strip() or "smtp.gmail.com",
        smtp_port=_as_int(os.getenv("KAP_SMTP_PORT") or 465, 465),
        smtp_user=smtp_user,
        smtp_pass=smtp_pass,
        from_addr=os.getenv("KAP_EMAIL_FROM", "").strip() or smtp_user,
        recipients=recipients,
    )

    return AppConfig(
        timezone=str(raw.get("timezone") or "Europe/Istanbul"),
        companies=_as_tuple(raw.get("companies")),
        filters=filters,
        quiet_hours=quiet_hours,
        llm=llm,
        ratings=ratings,
        documents=documents,
        email=email,
    )