"""Persisted automatic trading-session control for the existing monitor worker."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db import MonitorTask
from app.market.trading_sessions import (
    NIGHT_SESSION_REFERENCE_START,
    ContractSession,
    format_clock,
    minutes_after_session_start,
    recognize_contract_session,
)
from app.monitor.status import (
    get_monitor_status,
    pause_auto_monitoring,
    resume_auto_monitoring,
    update_session_scheduler_status,
)
from app.settings import (
    clear_manual_override,
    get_auto_session_scheduler_settings,
    get_manual_override_until,
    parse_trading_session_overrides,
    set_manual_control,
    get_session_control_source,
    set_manual_override,
)


logger = logging.getLogger(__name__)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
COMMODITY_DAY_EXCHANGES = {"SHF", "INE", "DCE", "CZC", "GFE"}
_last_unknown_contract_signature: tuple[str, ...] = ()


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def get_session_schedule_view(
    tasks: list[MonitorTask],
    now: datetime | None = None,
) -> dict:
    local_now = _as_beijing(now)
    settings = get_auto_session_scheduler_settings()
    overrides = parse_trading_session_overrides(settings["trading_session_overrides"])
    recognitions = [recognize_contract_session(task.code, overrides) for task in tasks if task.is_active]
    day_available = _has_commodity_day_contract(recognitions)
    night_recognitions = [item for item in recognitions if item.has_night_session]
    latest = _latest_night_recognition(night_recognitions)
    close_time = _night_close_time(latest, settings["night_close_buffer_minutes"])
    night_available = bool(night_recognitions)
    scheduled_active = _is_scheduled_active(
        local_now,
        settings,
        day_available=day_available,
        night_available=night_available,
        night_close_time=close_time,
    )
    next_action = _next_schedule_action(
        local_now,
        settings,
        day_available=day_available,
        night_available=night_available,
        night_close_time=close_time,
    )
    session_state = _current_session_state(
        local_now,
        settings,
        day_available=day_available,
        night_available=night_available,
        night_close_time=close_time,
    )
    override_until = get_manual_override_until()
    control_source = get_session_control_source()
    unknown = [item for item in recognitions if item.status == "unknown"]

    return {
        "enabled": settings["enabled"],
        "day_session_start": settings["day_session_start"],
        "day_session_stop": settings["day_session_stop"],
        "day_morning_start": settings["day_morning_start"],
        "day_morning_stop": settings["day_morning_stop"],
        "day_afternoon_start": settings["day_afternoon_start"],
        "day_afternoon_stop": settings["day_afternoon_stop"],
        "day_session_windows": settings["day_session_windows"],
        "night_session_start": settings["night_session_start"],
        "night_close_buffer_minutes": settings["night_close_buffer_minutes"],
        "now": local_now,
        "contract_sessions": recognitions,
        "day_available": day_available,
        "night_available": night_available,
        "latest_night_contract": latest.code if latest else None,
        "latest_night_end": latest.night_end if latest else None,
        "night_close_time": close_time,
        "unknown_contracts": unknown,
        "scheduled_active": scheduled_active,
        "next_action": next_action,
        "control_source": control_source,
        "manual_override_until": override_until,
        "session_state": session_state,
        "session_state_key": session_state["key"],
        "session_state_title": session_state["title"],
    }


def apply_auto_session_schedule(now: datetime | None = None) -> dict:
    """Apply a schedule decision once inside the worker/request app context.

    It changes the existing runtime monitor switch only at a plan boundary. The
    only persistent writes are manual override creation/expiration, never each
    polling tick.
    """
    tasks = MonitorTask.query.filter(MonitorTask.is_active.is_(True)).order_by(MonitorTask.code.asc()).all()
    view = get_session_schedule_view(tasks, now)
    local_now = view["now"]
    current = get_monitor_status()
    _log_unknown_contracts_once(view)

    if not view["enabled"]:
        set_manual_control()
        update_session_scheduler_status(
            enabled=False,
            control_source="manual",
            manual_override_until=None,
            next_action=_action_label(view["next_action"]),
        )
        return view

    override_until = view["manual_override_until"]
    if override_until and local_now < override_until:
        update_session_scheduler_status(
            enabled=True,
            control_source="manual_override",
            manual_override_until=override_until,
            next_action=_action_label(view["next_action"]),
        )
        return view

    if override_until:
        clear_manual_override()
        view["manual_override_until"] = None
        logger.info("Manual monitoring override expired; session schedule resumed.")

    desired_enabled = bool(view["scheduled_active"])
    currently_paused = bool(current.get("auto_monitoring_paused"))
    if desired_enabled and (currently_paused or not current.get("auto_monitoring_config_enabled")):
        resume_auto_monitoring()
        logger.info("Auto monitoring enabled by session schedule.")
    elif not desired_enabled and not currently_paused:
        pause_auto_monitoring(_schedule_pause_reason(view))
        logger.info("Auto monitoring disabled by session schedule: %s.", view["session_state_key"])

    update_session_scheduler_status(
        enabled=True,
        control_source="schedule",
        manual_override_until=None,
        next_action=_action_label(view["next_action"]),
    )
    return view


def apply_manual_monitoring_command(paused: bool, now: datetime | None = None) -> dict:
    """Apply a manual command, optionally persisting a schedule-bounded override."""
    tasks = MonitorTask.query.filter(MonitorTask.is_active.is_(True)).order_by(MonitorTask.code.asc()).all()
    view = get_session_schedule_view(tasks, now)
    if not view["enabled"]:
        set_manual_control()
        if paused:
            pause_auto_monitoring()
        else:
            resume_auto_monitoring()
        update_session_scheduler_status(
            enabled=False,
            control_source="manual",
            manual_override_until=None,
            next_action=_action_label(view["next_action"]),
        )
        return view

    override_until = view["next_action"]["at"]
    set_manual_override(override_until)
    if paused:
        pause_auto_monitoring(f"手动临时覆盖，至 {override_until.strftime('%Y-%m-%d %H:%M')}。")
    else:
        resume_auto_monitoring()
    update_session_scheduler_status(
        enabled=True,
        control_source="manual_override",
        manual_override_until=override_until,
        next_action=_action_label(view["next_action"]),
    )
    view["manual_override_until"] = override_until
    view["control_source"] = "manual_override"
    logger.info("Manual monitoring override active until %s.", override_until.isoformat())
    return view


def _latest_night_recognition(items: list[ContractSession]) -> ContractSession | None:
    if not items:
        return None
    return max(items, key=lambda item: minutes_after_session_start(item.night_end))


def _has_commodity_day_contract(items: list[ContractSession]) -> bool:
    return any(item.exchange in COMMODITY_DAY_EXCHANGES for item in items)


def _night_close_time(latest: ContractSession | None, buffer_minutes: int) -> time | None:
    if latest is None or latest.night_end is None:
        return None
    anchor = datetime.combine(date(2000, 1, 1), latest.night_end)
    return (anchor + timedelta(minutes=buffer_minutes)).time().replace(second=0, microsecond=0)


def _is_scheduled_active(
    now: datetime,
    settings: dict,
    *,
    day_available: bool,
    night_available: bool,
    night_close_time: time | None,
) -> bool:
    current_time = now.timetz().replace(tzinfo=None)
    if day_available and _is_weekday(now.date()) and _is_in_day_window(current_time, settings):
        return True
    if not night_available or night_close_time is None:
        return False
    if current_time >= settings["night_session_start"]:
        return _is_weekday(now.date())
    if current_time < night_close_time:
        return _is_weekday(now.date() - timedelta(days=1))
    return False


def _next_schedule_action(
    now: datetime,
    settings: dict,
    *,
    day_available: bool,
    night_available: bool,
    night_close_time: time | None,
) -> dict:
    candidates: list[tuple[datetime, str]] = []
    for offset in range(0, 9):
        current_date = now.date() + timedelta(days=offset)
        if _is_weekday(current_date) and day_available:
            candidates.extend(
                [
                    (_combine(current_date, settings["day_morning_start"]), "自动开启"),
                    (_combine(current_date, settings["day_morning_stop"]), "自动暂停"),
                    (_combine(current_date, settings["day_afternoon_start"]), "自动恢复"),
                    (_combine(current_date, settings["day_afternoon_stop"]), "自动暂停"),
                ]
            )
        if _is_weekday(current_date):
            if night_available and night_close_time is not None:
                candidates.append((_combine(current_date, settings["night_session_start"]), "自动开启"))
                candidates.append((_combine(current_date + timedelta(days=1), night_close_time), "自动暂停"))
    future = [(at, action) for at, action in candidates if at > now]
    if not future:
        return {"at": _combine(now.date() + timedelta(days=1), settings["day_morning_start"]), "action": "自动开启"}
    at, action = min(future, key=lambda item: item[0])
    return {"at": at, "action": action}


def _is_in_day_window(current_time: time, settings: dict) -> bool:
    return any(start <= current_time < stop for start, stop in settings["day_session_windows"])


def _current_session_state(
    now: datetime,
    settings: dict,
    *,
    day_available: bool,
    night_available: bool,
    night_close_time: time | None,
) -> dict:
    current_time = now.timetz().replace(tzinfo=None)
    if day_available and _is_weekday(now.date()):
        if settings["day_morning_start"] <= current_time < settings["day_morning_stop"]:
            return {"key": "day_morning", "title": "日盘上午轮询中"}
        if settings["day_morning_stop"] <= current_time < settings["day_afternoon_start"]:
            return {"key": "midday_break", "title": "午间休盘中"}
        if settings["day_afternoon_start"] <= current_time < settings["day_afternoon_stop"]:
            return {"key": "day_afternoon", "title": "日盘下午轮询中"}
        if current_time >= settings["day_afternoon_stop"] and current_time < settings["night_session_start"]:
            return {"key": "day_closed", "title": "日盘已结束"}

    if night_available and night_close_time is not None:
        if current_time >= settings["night_session_start"] and _is_weekday(now.date()):
            return {"key": "night", "title": "夜盘轮询中"}
        if current_time < night_close_time and _is_weekday(now.date() - timedelta(days=1)):
            return {"key": "night", "title": "夜盘轮询中"}

    return {"key": "closed", "title": "计划暂停中"}


def _schedule_pause_reason(view: dict) -> str:
    if view["session_state_key"] == "midday_break":
        next_at = view["next_action"]["at"].strftime("%H:%M")
        return f"午间休盘中，预计 {next_at} 自动恢复。"
    if view["session_state_key"] == "day_closed":
        return "日盘已结束，等待夜盘或下一个交易时段。"
    return "交易时段自动暂停"


def _action_label(action: dict) -> str:
    return f"{action['at'].strftime('%Y-%m-%d %H:%M')} {action['action']}"


def _combine(value: date, clock: time) -> datetime:
    return datetime.combine(value, clock, tzinfo=BEIJING_TZ)


def _is_weekday(value: date) -> bool:
    return value.weekday() < 5


def _as_beijing(value: datetime | None) -> datetime:
    if value is None:
        return beijing_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ)


def _log_unknown_contracts_once(view: dict) -> None:
    global _last_unknown_contract_signature

    signature = tuple(sorted(item.code for item in view["unknown_contracts"]))
    if signature and signature != _last_unknown_contract_signature:
        logger.warning(
            "Trading session is not recognized for enabled contracts: %s; using conservative night fallback.",
            ", ".join(signature),
        )
    _last_unknown_contract_signature = signature
