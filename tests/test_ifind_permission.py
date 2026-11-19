from datetime import date

from app.monitor.ifind_permission import (
    get_ifind_permission_status,
    get_next_permission_renewal_date,
)


def config(**overrides):
    base = {
        "IFIND_PERMISSION_RENEWAL_DAY": 22,
        "IFIND_PERMISSION_REMINDER_DAYS": 3,
        "IFIND_PERMISSION_GRACE_DAYS": 2,
        "IFIND_PERMISSION_TIMEZONE": "Asia/Shanghai",
    }
    base.update(overrides)
    return base


def test_permission_status_ok_before_reminder_window():
    status = get_ifind_permission_status(today=date(2026, 7, 10), config=config())

    assert status.status == "ok"
    assert status.next_renewal_date == date(2026, 7, 22)
    assert status.days_until_renewal == 12


def test_permission_status_expiring_soon():
    for day in (19, 20, 21):
        status = get_ifind_permission_status(today=date(2026, 7, day), config=config())

        assert status.status == "expiring_soon"
        assert status.days_until_renewal == 22 - day


def test_permission_status_renewal_day():
    status = get_ifind_permission_status(today=date(2026, 7, 22), config=config())

    assert status.status == "renewal_day"
    assert status.days_until_renewal == 0
    assert "今天是权限更新日" in status.message


def test_permission_status_renewal_grace_period():
    for day in (23, 24):
        status = get_ifind_permission_status(today=date(2026, 7, day), config=config())

        assert status.status == "renewal_grace"
        assert "权限刚更新" in status.message


def test_permission_status_after_grace_moves_to_next_month():
    status = get_ifind_permission_status(today=date(2026, 7, 25), config=config())

    assert status.status == "ok"
    assert status.next_renewal_date == date(2026, 8, 22)
    assert status.days_until_renewal == 28


def test_next_permission_renewal_handles_month_boundary():
    next_date = get_next_permission_renewal_date(
        today=date(2026, 1, 31),
        renewal_day=22,
        timezone="Asia/Shanghai",
    )

    assert next_date == date(2026, 2, 22)


def test_permission_config_error_for_invalid_day_string():
    status = get_ifind_permission_status(
        today=date(2026, 7, 10),
        config=config(IFIND_PERMISSION_RENEWAL_DAY="abc"),
    )

    assert status.status == "config_error"
    assert status.error


def test_permission_config_error_for_day_out_of_range():
    for invalid_day in (0, 32):
        status = get_ifind_permission_status(
            today=date(2026, 7, 10),
            config=config(IFIND_PERMISSION_RENEWAL_DAY=invalid_day),
        )

        assert status.status == "config_error"
