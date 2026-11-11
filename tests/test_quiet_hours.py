from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from main import in_quiet_hours
from src.config import AppConfig, QuietHoursConfig

TZ = ZoneInfo("Europe/Istanbul")


def config(start: str, end: str, enabled: bool = True) -> AppConfig:
    return replace(
        AppConfig(),
        quiet_hours=QuietHoursConfig(enabled=enabled, start=start, end=end),
    )


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 11, hour, minute, tzinfo=TZ)


def test_same_day_window():
    cfg = config("00:00", "08:00")
    assert in_quiet_hours(at(3), cfg) is True
    assert in_quiet_hours(at(8), cfg) is False
    assert in_quiet_hours(at(9), cfg) is False


def test_cross_midnight_window():
    cfg = config("23:00", "07:00")
    assert in_quiet_hours(at(23, 30), cfg) is True
    assert in_quiet_hours(at(2), cfg) is True
    assert in_quiet_hours(at(7), cfg) is False
    assert in_quiet_hours(at(12), cfg) is False


def test_disabled_window():
    cfg = config("00:00", "08:00", enabled=False)
    assert in_quiet_hours(at(3), cfg) is False