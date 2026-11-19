from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def as_utc_aware(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime using the app's storage convention."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_china_time(value: datetime) -> datetime:
    return as_utc_aware(value).astimezone(CHINA_TZ)


def format_china_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return as_china_time(value).strftime("%Y-%m-%d %H:%M:%S")
