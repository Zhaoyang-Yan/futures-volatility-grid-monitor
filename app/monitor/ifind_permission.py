from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_RENEWAL_DAY = 22
DEFAULT_REMINDER_DAYS = 3
DEFAULT_GRACE_DAYS = 2
DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class IfindPermissionStatus:
    renewal_day: int
    next_renewal_date: date | None
    days_until_renewal: int | None
    reminder_days: int
    grace_days: int
    status: str
    message: str
    error: str | None = None


def get_next_permission_renewal_date(
    today=None,
    renewal_day=DEFAULT_RENEWAL_DAY,
    timezone=DEFAULT_TIMEZONE,
) -> date:
    current_date = _normalize_today(today, timezone)
    renewal_day = _parse_renewal_day(renewal_day)
    current_month_renewal = date(current_date.year, current_date.month, renewal_day)
    if current_date <= current_month_renewal:
        return current_month_renewal

    next_year = current_date.year + 1 if current_date.month == 12 else current_date.year
    next_month = 1 if current_date.month == 12 else current_date.month + 1
    return date(next_year, next_month, renewal_day)


def get_ifind_permission_status(today=None, config=None) -> IfindPermissionStatus:
    config = config or {}
    try:
        renewal_day = _parse_renewal_day(config.get("IFIND_PERMISSION_RENEWAL_DAY", DEFAULT_RENEWAL_DAY))
        reminder_days = _parse_non_negative_int(
            config.get("IFIND_PERMISSION_REMINDER_DAYS", DEFAULT_REMINDER_DAYS),
            "IFIND_PERMISSION_REMINDER_DAYS",
        )
        grace_days = _parse_non_negative_int(
            config.get("IFIND_PERMISSION_GRACE_DAYS", DEFAULT_GRACE_DAYS),
            "IFIND_PERMISSION_GRACE_DAYS",
        )
        timezone = config.get("IFIND_PERMISSION_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        current_date = _normalize_today(today, timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        return IfindPermissionStatus(
            renewal_day=DEFAULT_RENEWAL_DAY,
            next_renewal_date=None,
            days_until_renewal=None,
            reminder_days=DEFAULT_REMINDER_DAYS,
            grace_days=DEFAULT_GRACE_DAYS,
            status="config_error",
            message="iFind 权限更新提醒：配置错误，请检查 iFind 权限提醒配置。",
            error=str(exc),
        )

    current_month_renewal = date(current_date.year, current_date.month, renewal_day)
    days_from_current_renewal = (current_date - current_month_renewal).days

    if days_from_current_renewal == 0:
        next_renewal = current_month_renewal
        days_until = 0
        status = "renewal_day"
        message = "iFind 权限更新提醒：今天是权限更新日，请确认账号权限是否已更新，必要时重新获取 refresh token。"
    elif 0 < days_from_current_renewal <= grace_days:
        next_renewal = get_next_permission_renewal_date(
            today=current_date,
            renewal_day=renewal_day,
            timezone=timezone,
        )
        days_until = (next_renewal - current_date).days
        status = "renewal_grace"
        message = "iFind 权限更新提醒：权限刚更新，请检查 refresh token 是否仍可换取访问令牌。"
    else:
        next_renewal = get_next_permission_renewal_date(
            today=current_date,
            renewal_day=renewal_day,
            timezone=timezone,
        )
        days_until = (next_renewal - current_date).days
        if 0 < days_until <= reminder_days:
            status = "expiring_soon"
            message = "iFind 权限更新提醒：即将到期，请提前确认 refresh token 是否仍可使用。"
        else:
            status = "ok"
            message = "iFind 权限更新提醒：正常。"

    return IfindPermissionStatus(
        renewal_day=renewal_day,
        next_renewal_date=next_renewal,
        days_until_renewal=days_until,
        reminder_days=reminder_days,
        grace_days=grace_days,
        status=status,
        message=message,
    )


def _normalize_today(today, timezone: str) -> date:
    tz = ZoneInfo(timezone)
    if today is None:
        return datetime.now(tz).date()
    if isinstance(today, datetime):
        if today.tzinfo is None:
            return today.replace(tzinfo=tz).date()
        return today.astimezone(tz).date()
    if isinstance(today, date):
        return today
    raise ValueError("today must be a date or datetime")


def _parse_renewal_day(value) -> int:
    renewal_day = int(value)
    if renewal_day < 1 or renewal_day > 28:
        raise ValueError("IFIND_PERMISSION_RENEWAL_DAY must be between 1 and 28")
    return renewal_day


def _parse_non_negative_int(value, name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{name} must be greater than or equal to 0")
    return parsed
