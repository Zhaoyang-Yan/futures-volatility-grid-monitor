from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context
from sqlalchemy.exc import IntegrityError

from app.db import AppSetting, db


MONITOR_POLL_INTERVAL_KEY = "monitor_poll_interval_seconds"
DEFAULT_MONITOR_POLL_INTERVAL_SECONDS = 30
MIN_MONITOR_POLL_INTERVAL_SECONDS = 10
MAX_MONITOR_POLL_INTERVAL_SECONDS = 300
MONITOR_POLL_INTERVAL_OPTIONS = [10, 15, 30, 60, 120, 300]

AUTO_SESSION_SCHEDULER_ENABLED_KEY = "auto_session_scheduler_enabled"
DAY_SESSION_START_KEY = "day_session_start"
DAY_SESSION_STOP_KEY = "day_session_stop"
DAY_MORNING_START_KEY = "day_morning_start"
DAY_MORNING_STOP_KEY = "day_morning_stop"
DAY_AFTERNOON_START_KEY = "day_afternoon_start"
DAY_AFTERNOON_STOP_KEY = "day_afternoon_stop"
NIGHT_SESSION_START_KEY = "night_session_start"
NIGHT_CLOSE_BUFFER_MINUTES_KEY = "night_close_buffer_minutes"
TRADING_SESSION_OVERRIDES_KEY = "trading_session_overrides"
SESSION_CONTROL_SOURCE_KEY = "session_control_source"
MANUAL_OVERRIDE_UNTIL_KEY = "manual_override_until"

DEFAULT_AUTO_SESSION_SCHEDULER_ENABLED = False
DEFAULT_DAY_SESSION_START = "08:50"
DEFAULT_DAY_SESSION_STOP = "15:05"
DEFAULT_DAY_MORNING_START = "08:50"
DEFAULT_DAY_MORNING_STOP = "11:35"
DEFAULT_DAY_AFTERNOON_START = "13:20"
DEFAULT_DAY_AFTERNOON_STOP = "15:05"
DEFAULT_NIGHT_SESSION_START = "20:50"
DEFAULT_NIGHT_CLOSE_BUFFER_MINUTES = 10
DEFAULT_TRADING_SESSION_OVERRIDES = ""


class AppSettingValidationError(ValueError):
    pass


def get_app_setting(key: str, default: str | None = None) -> str | None:
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None:
        return default
    return setting.value


def set_app_setting(key: str, value: str, *, commit: bool = True) -> AppSetting:
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None:
        setting = AppSetting(key=key, value=str(value))
        db.session.add(setting)
    else:
        setting.value = str(value)

    if commit:
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            setting = AppSetting.query.filter_by(key=key).first()
            if setting is None:
                raise
            setting.value = str(value)
            db.session.commit()
    return setting


def validate_monitor_poll_interval_seconds(raw_value) -> int:
    if raw_value is None or str(raw_value).strip() == "":
        raise AppSettingValidationError("轮询间隔不能为空")

    try:
        seconds = int(str(raw_value).strip())
    except (TypeError, ValueError):
        raise AppSettingValidationError("轮询间隔必须是整数秒") from None

    if seconds < MIN_MONITOR_POLL_INTERVAL_SECONDS:
        raise AppSettingValidationError("轮询间隔不能小于 10 秒")
    if seconds > MAX_MONITOR_POLL_INTERVAL_SECONDS:
        raise AppSettingValidationError("轮询间隔不能大于 300 秒")
    return seconds


def get_monitor_poll_interval_seconds(default: int | None = None, *, create_missing: bool = True) -> int:
    fallback = _default_interval(default)
    raw_value = get_app_setting(MONITOR_POLL_INTERVAL_KEY)
    if raw_value is None:
        if create_missing:
            set_app_setting(MONITOR_POLL_INTERVAL_KEY, str(fallback))
        return fallback

    try:
        return validate_monitor_poll_interval_seconds(raw_value)
    except AppSettingValidationError:
        return fallback


def set_monitor_poll_interval_seconds(seconds: int) -> AppSetting:
    validated = validate_monitor_poll_interval_seconds(seconds)
    return set_app_setting(MONITOR_POLL_INTERVAL_KEY, str(validated))


def validate_session_time(raw_value, field_label: str = "交易时段时间") -> str:
    value = str(raw_value or "").strip()
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        raise AppSettingValidationError(f"{field_label}必须使用 HH:MM 格式") from None
    return parsed.strftime("%H:%M")


def validate_night_close_buffer_minutes(raw_value) -> int:
    try:
        minutes = int(str(raw_value).strip())
    except (TypeError, ValueError):
        raise AppSettingValidationError("夜盘缓冲时间必须是整数分钟") from None
    if not 5 <= minutes <= 30:
        raise AppSettingValidationError("夜盘缓冲时间必须在 5 到 30 分钟之间")
    return minutes


def validate_trading_session_overrides(raw_value) -> str:
    """Validate `PRODUCT.EXCHANGE=HH:MM|NO_NIGHT` lines without guessing sessions."""
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return ""
    normalized_lines = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z]+)\.([A-Za-z]{2,6})\s*=\s*([0-2]\d:[0-5]\d|no_night)", line, re.IGNORECASE)
        if not match:
            raise AppSettingValidationError("品种时段覆盖格式应为 PRODUCT.EXCHANGE=HH:MM 或 PRODUCT.EXCHANGE=NO_NIGHT")
        product, exchange, value = match.groups()
        if value.lower() != "no_night":
            validate_session_time(value, "品种夜盘结束时间")
        normalized_lines.append(f"{product.upper()}.{exchange.upper()}={value.upper() if value.lower() == 'no_night' else value}")
    return "\n".join(normalized_lines)


def parse_trading_session_overrides(raw_value) -> dict[tuple[str, str], object]:
    from datetime import datetime as _datetime

    overrides = {}
    normalized = validate_trading_session_overrides(raw_value)
    for line in normalized.splitlines():
        product_exchange, value = line.split("=", 1)
        product, exchange = product_exchange.split(".", 1)
        overrides[(exchange, product)] = None if value == "NO_NIGHT" else _datetime.strptime(value, "%H:%M").time()
    return overrides


def get_auto_session_scheduler_settings(*, create_missing: bool = True) -> dict:
    defaults = {
        AUTO_SESSION_SCHEDULER_ENABLED_KEY: "true" if DEFAULT_AUTO_SESSION_SCHEDULER_ENABLED else "false",
        DAY_SESSION_START_KEY: DEFAULT_DAY_SESSION_START,
        DAY_SESSION_STOP_KEY: DEFAULT_DAY_SESSION_STOP,
        DAY_MORNING_START_KEY: DEFAULT_DAY_MORNING_START,
        DAY_MORNING_STOP_KEY: DEFAULT_DAY_MORNING_STOP,
        DAY_AFTERNOON_START_KEY: DEFAULT_DAY_AFTERNOON_START,
        DAY_AFTERNOON_STOP_KEY: DEFAULT_DAY_AFTERNOON_STOP,
        NIGHT_SESSION_START_KEY: DEFAULT_NIGHT_SESSION_START,
        NIGHT_CLOSE_BUFFER_MINUTES_KEY: str(DEFAULT_NIGHT_CLOSE_BUFFER_MINUTES),
        TRADING_SESSION_OVERRIDES_KEY: DEFAULT_TRADING_SESSION_OVERRIDES,
    }
    values = {}
    existing_values = {}
    for key, default in defaults.items():
        raw_value = get_app_setting(key)
        existing_values[key] = raw_value
        if raw_value is None:
            if create_missing:
                set_app_setting(key, default)
            raw_value = default
        values[key] = raw_value

    morning_start_raw = (
        values[DAY_MORNING_START_KEY]
        if existing_values[DAY_MORNING_START_KEY] is not None
        else values[DAY_SESSION_START_KEY]
    )
    afternoon_stop_raw = (
        values[DAY_AFTERNOON_STOP_KEY]
        if existing_values[DAY_AFTERNOON_STOP_KEY] is not None
        else values[DAY_SESSION_STOP_KEY]
    )
    morning_start = _clock_from_value(
        morning_start_raw,
        DEFAULT_DAY_MORNING_START,
    )
    morning_stop = _clock_from_value(values[DAY_MORNING_STOP_KEY], DEFAULT_DAY_MORNING_STOP)
    afternoon_start = _clock_from_value(values[DAY_AFTERNOON_START_KEY], DEFAULT_DAY_AFTERNOON_START)
    afternoon_stop = _clock_from_value(
        afternoon_stop_raw,
        DEFAULT_DAY_AFTERNOON_STOP,
    )

    return {
        "enabled": str(values[AUTO_SESSION_SCHEDULER_ENABLED_KEY]).strip().lower() in {"1", "true", "yes", "on"},
        "day_session_start": morning_start,
        "day_session_stop": afternoon_stop,
        "day_morning_start": morning_start,
        "day_morning_stop": morning_stop,
        "day_afternoon_start": afternoon_start,
        "day_afternoon_stop": afternoon_stop,
        "day_session_windows": ((morning_start, morning_stop), (afternoon_start, afternoon_stop)),
        "night_session_start": _clock_from_value(values[NIGHT_SESSION_START_KEY], DEFAULT_NIGHT_SESSION_START),
        "night_close_buffer_minutes": _buffer_from_value(values[NIGHT_CLOSE_BUFFER_MINUTES_KEY]),
        "trading_session_overrides": validate_trading_session_overrides(values[TRADING_SESSION_OVERRIDES_KEY]),
    }


def set_auto_session_scheduler_settings(
    *,
    enabled: bool,
    day_session_start: str,
    day_session_stop: str,
    night_session_start: str,
    night_close_buffer_minutes,
    day_morning_stop: str | None = None,
    day_afternoon_start: str | None = None,
    trading_session_overrides: str = "",
) -> None:
    start = validate_session_time(day_session_start, "日盘开始时间")
    morning_stop = validate_session_time(day_morning_stop or DEFAULT_DAY_MORNING_STOP, "上午日盘暂停时间")
    afternoon_start = validate_session_time(day_afternoon_start or DEFAULT_DAY_AFTERNOON_START, "下午日盘恢复时间")
    stop = validate_session_time(day_session_stop, "日盘结束时间")
    night_start = validate_session_time(night_session_start, "夜盘开始时间")
    buffer_minutes = validate_night_close_buffer_minutes(night_close_buffer_minutes)
    overrides = validate_trading_session_overrides(trading_session_overrides)
    if not (start < morning_stop < afternoon_start < stop):
        raise AppSettingValidationError("日盘时间必须满足 08:50 < 11:35 < 13:20 < 15:05 的窗口顺序")
    set_app_setting(AUTO_SESSION_SCHEDULER_ENABLED_KEY, "true" if enabled else "false", commit=False)
    set_app_setting(DAY_SESSION_START_KEY, start, commit=False)
    set_app_setting(DAY_SESSION_STOP_KEY, stop, commit=False)
    set_app_setting(DAY_MORNING_START_KEY, start, commit=False)
    set_app_setting(DAY_MORNING_STOP_KEY, morning_stop, commit=False)
    set_app_setting(DAY_AFTERNOON_START_KEY, afternoon_start, commit=False)
    set_app_setting(DAY_AFTERNOON_STOP_KEY, stop, commit=False)
    set_app_setting(NIGHT_SESSION_START_KEY, night_start, commit=False)
    set_app_setting(NIGHT_CLOSE_BUFFER_MINUTES_KEY, str(buffer_minutes), commit=False)
    set_app_setting(TRADING_SESSION_OVERRIDES_KEY, overrides, commit=False)
    db.session.commit()


def get_session_control_source() -> str:
    value = (get_app_setting(SESSION_CONTROL_SOURCE_KEY, "manual") or "manual").strip()
    return value if value in {"manual", "schedule", "manual_override"} else "manual"


def get_manual_override_until():
    raw_value = (get_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, "") or "").strip()
    if not raw_value:
        return None
    try:
        value = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return value.astimezone(ZoneInfo("Asia/Shanghai"))


def set_manual_override(until) -> None:
    set_app_setting(SESSION_CONTROL_SOURCE_KEY, "manual_override", commit=False)
    set_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, until.isoformat(), commit=False)
    db.session.commit()


def clear_manual_override() -> None:
    if get_session_control_source() == "schedule" and not get_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, ""):
        return
    set_app_setting(SESSION_CONTROL_SOURCE_KEY, "schedule", commit=False)
    set_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, "", commit=False)
    db.session.commit()


def set_manual_control() -> None:
    if get_session_control_source() == "manual" and not get_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, ""):
        return
    set_app_setting(SESSION_CONTROL_SOURCE_KEY, "manual", commit=False)
    set_app_setting(MANUAL_OVERRIDE_UNTIL_KEY, "", commit=False)
    db.session.commit()


def _default_interval(default: int | None = None) -> int:
    candidate = default
    if candidate is None and has_app_context():
        candidate = current_app.config.get("MONITOR_INTERVAL_SECONDS")
    if candidate is None:
        candidate = DEFAULT_MONITOR_POLL_INTERVAL_SECONDS
    try:
        return validate_monitor_poll_interval_seconds(candidate)
    except AppSettingValidationError:
        return DEFAULT_MONITOR_POLL_INTERVAL_SECONDS


def _clock_from_value(raw_value, fallback: str):
    try:
        return datetime.strptime(validate_session_time(raw_value), "%H:%M").time()
    except AppSettingValidationError:
        return datetime.strptime(fallback, "%H:%M").time()


def _buffer_from_value(raw_value) -> int:
    try:
        return validate_night_close_buffer_minutes(raw_value)
    except AppSettingValidationError:
        return DEFAULT_NIGHT_CLOSE_BUFFER_MINUTES
