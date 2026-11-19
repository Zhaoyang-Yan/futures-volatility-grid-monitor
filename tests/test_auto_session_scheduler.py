from datetime import datetime

from app.db import AlertLog, AppSetting, MonitorTask, db
from app.market.trading_sessions import recognize_contract_session
from app.monitor.engine import MonitorRunResult
from app.monitor.session_scheduler import (
    BEIJING_TZ,
    apply_auto_session_schedule,
    apply_manual_monitoring_command,
    get_session_schedule_view,
)
from app.monitor.status import configure_auto_monitoring, get_monitor_status, reset_monitor_status
from app.monitor_worker import run_monitor_cycle
from app.settings import (
    DAY_AFTERNOON_START_KEY,
    DAY_MORNING_STOP_KEY,
    DAY_SESSION_START_KEY,
    DAY_SESSION_STOP_KEY,
    NIGHT_CLOSE_BUFFER_MINUTES_KEY,
    get_auto_session_scheduler_settings,
    set_auto_session_scheduler_settings,
)


def _at(day, hour, minute):
    return datetime(2026, 7, day, hour, minute, tzinfo=BEIJING_TZ)


def _enable_scheduler():
    set_auto_session_scheduler_settings(
        enabled=True,
        day_session_start="08:50",
        day_morning_stop="11:35",
        day_afternoon_start="13:20",
        day_session_stop="15:05",
        night_session_start="20:50",
        night_close_buffer_minutes=10,
    )


def test_session_scheduler_defaults_to_disabled_and_persists_settings(app):
    settings = get_auto_session_scheduler_settings()

    assert settings["enabled"] is False
    assert settings["day_session_start"].strftime("%H:%M") == "08:50"
    assert settings["day_morning_stop"].strftime("%H:%M") == "11:35"
    assert settings["day_afternoon_start"].strftime("%H:%M") == "13:20"
    assert settings["day_session_stop"].strftime("%H:%M") == "15:05"
    assert settings["night_close_buffer_minutes"] == 10
    assert AppSetting.query.filter_by(key=DAY_SESSION_START_KEY).one().value == "08:50"
    assert AppSetting.query.filter_by(key=DAY_MORNING_STOP_KEY).one().value == "11:35"
    assert AppSetting.query.filter_by(key=DAY_AFTERNOON_START_KEY).one().value == "13:20"


def test_legacy_day_stop_setting_remains_compatible(app):
    db.session.add_all(
        [
            AppSetting(key=DAY_SESSION_START_KEY, value="08:55"),
            AppSetting(key=DAY_SESSION_STOP_KEY, value="15:10"),
        ]
    )
    db.session.commit()

    settings = get_auto_session_scheduler_settings()

    assert settings["day_session_start"].strftime("%H:%M") == "08:55"
    assert settings["day_session_stop"].strftime("%H:%M") == "15:10"
    assert settings["day_morning_stop"].strftime("%H:%M") == "11:35"
    assert settings["day_afternoon_start"].strftime("%H:%M") == "13:20"


def test_day_session_boundaries_enable_pause_and_midday_break(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(True)

    cases = [
        ((8, 49), True, "closed"),
        ((8, 50), False, "day_morning"),
        ((10, 14), False, "day_morning"),
        ((10, 16), False, "day_morning"),
        ((10, 29), False, "day_morning"),
        ((10, 30), False, "day_morning"),
        ((11, 34), False, "day_morning"),
        ((11, 35), True, "midday_break"),
        ((12, 0), True, "midday_break"),
        ((13, 19), True, "midday_break"),
        ((13, 20), False, "day_afternoon"),
        ((15, 4), False, "day_afternoon"),
        ((15, 5), True, "day_closed"),
    ]

    for (hour, minute), expected_paused, expected_state in cases:
        view = apply_auto_session_schedule(_at(13, hour, minute))
        assert get_monitor_status()["auto_monitoring_paused"] is expected_paused
        assert view["session_state_key"] == expected_state

    midday_view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 11, 35))
    assert midday_view["next_action"]["at"].strftime("%H:%M") == "13:20"
    assert midday_view["next_action"]["action"] == "自动恢复"


def test_night_plan_uses_latest_enabled_contract_and_buffer(app, make_task):
    make_task(code="CU2608.SHF")
    make_task(code="NR2608.INE")
    make_task(code="LC2609.GFE")
    _enable_scheduler()

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 49))

    assert view["latest_night_contract"] == "CU2608.SHF"
    assert view["latest_night_end"].strftime("%H:%M") == "01:00"
    assert view["night_close_time"].strftime("%H:%M") == "01:10"


def test_sp_and_sr_together_close_at_2300(app, make_task):
    make_task(code="SP2609.SHF")
    make_task(code="SR609.CZC")
    _enable_scheduler()

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 49))

    assert view["unknown_contracts"] == []
    assert view["latest_night_end"].strftime("%H:%M") == "23:00"
    assert view["night_close_time"].strftime("%H:%M") == "23:10"


def test_sp_sr_do_not_change_later_cu_close(app, make_task):
    make_task(code="CU2608.SHF")
    make_task(code="SP2609.SHF")
    make_task(code="SR609.CZC")
    _enable_scheduler()

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 49))

    assert view["latest_night_contract"] == "CU2608.SHF"
    assert view["latest_night_end"].strftime("%H:%M") == "01:00"
    assert view["night_close_time"].strftime("%H:%M") == "01:10"


def test_adding_later_sc_contract_recalculates_night_close(app, make_task):
    make_task(code="CU2608.SHF")
    make_task(code="SC2609.INE")
    _enable_scheduler()

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 49))

    assert view["latest_night_contract"] == "SC2609.INE"
    assert view["night_close_time"].strftime("%H:%M") == "02:40"


def test_no_night_contract_does_not_open_at_night_start(app, make_task):
    make_task(code="LC2609.GFE")
    _enable_scheduler()
    configure_auto_monitoring(True)

    apply_auto_session_schedule(_at(13, 20, 50))
    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 50))

    assert view["night_available"] is False
    assert get_monitor_status()["auto_monitoring_paused"] is True


def test_unknown_contract_uses_safe_fallback_and_is_visible(app, make_task):
    make_task(code="ZZ2609.CZC")
    _enable_scheduler()

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 50))

    assert view["unknown_contracts"][0].code == "ZZ2609.CZC"
    assert view["night_close_time"].strftime("%H:%M") == "02:40"
    assert "交易时段未识别" in view["unknown_contracts"][0].message


def test_confirmed_product_override_avoids_source_code_changes(app, make_task):
    make_task(code="ZZ2609.CZC")
    set_auto_session_scheduler_settings(
        enabled=True,
        day_session_start="08:50",
        day_morning_stop="11:35",
        day_afternoon_start="13:20",
        day_session_stop="15:05",
        night_session_start="20:50",
        night_close_buffer_minutes=10,
        trading_session_overrides="ZZ.CZC=23:00",
    )

    view = get_session_schedule_view(MonitorTask.query.all(), _at(13, 20, 50))

    assert view["contract_sessions"][0].status == "override"
    assert view["contract_sessions"][0].night_end.strftime("%H:%M") == "23:00"
    assert view["unknown_contracts"] == []


def test_contract_month_changes_keep_product_and_exchange_recognition():
    assert recognize_contract_session("CU2608.SHF").night_end.strftime("%H:%M") == "01:00"
    assert recognize_contract_session("CU2610.SHF").night_end.strftime("%H:%M") == "01:00"
    assert recognize_contract_session("LC2609.GFE").status == "no_night"
    assert recognize_contract_session("SR609.CZC").product == "SR"
    assert recognize_contract_session("SR609.CZC").exchange == "CZC"


def test_sp_and_sr_are_recognized_as_2300_night_products():
    sp = recognize_contract_session("SP2609.SHF")
    sr = recognize_contract_session("SR609.CZC")

    assert sp.product == "SP"
    assert sp.exchange == "SHF"
    assert sp.status == "night"
    assert sp.night_end.strftime("%H:%M") == "23:00"
    assert "02:30" not in sp.message

    assert sr.product == "SR"
    assert sr.exchange == "CZC"
    assert sr.status == "night"
    assert sr.night_end.strftime("%H:%M") == "23:00"
    assert "02:30" not in sr.message


def test_night_session_crosses_midnight_from_previous_trading_day(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()

    before_close = get_session_schedule_view(MonitorTask.query.all(), _at(14, 1, 9))
    at_close = get_session_schedule_view(MonitorTask.query.all(), _at(14, 1, 10))

    assert before_close["scheduled_active"] is True
    assert at_close["scheduled_active"] is False


def test_friday_night_session_can_continue_into_saturday_early_morning(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()

    before_saturday_close = get_session_schedule_view(MonitorTask.query.all(), _at(18, 1, 9))
    at_saturday_close = get_session_schedule_view(MonitorTask.query.all(), _at(18, 1, 10))

    assert before_saturday_close["scheduled_active"] is True
    assert at_saturday_close["scheduled_active"] is False


def test_weekend_does_not_auto_open_day_or_new_night_session(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()

    saturday_day = get_session_schedule_view(MonitorTask.query.all(), _at(18, 9, 0))
    saturday_night = get_session_schedule_view(MonitorTask.query.all(), _at(18, 20, 50))

    assert saturday_day["scheduled_active"] is False
    assert saturday_night["scheduled_active"] is False


def test_cfe_contract_does_not_use_commodity_day_windows(app, make_task):
    make_task(code="IF2609.CFE")
    _enable_scheduler()

    morning = get_session_schedule_view(MonitorTask.query.all(), _at(13, 9, 0))
    afternoon = get_session_schedule_view(MonitorTask.query.all(), _at(13, 13, 20))

    assert morning["day_available"] is False
    assert morning["scheduled_active"] is False
    assert afternoon["scheduled_active"] is False


def test_disabled_scheduler_keeps_existing_manual_control(app, make_task):
    make_task(code="CU2608.SHF")
    configure_auto_monitoring(True)

    apply_manual_monitoring_command(paused=True, now=_at(13, 10, 0))
    apply_auto_session_schedule(_at(13, 10, 1))

    status = get_monitor_status()
    assert status["auto_monitoring_paused"] is True
    assert status["auto_monitoring_control_source"] == "manual"


def test_manual_override_holds_until_next_boundary_then_schedule_recovers(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(True)

    view = apply_manual_monitoring_command(paused=True, now=_at(13, 10, 0))
    assert view["manual_override_until"].strftime("%H:%M") == "11:35"
    assert get_monitor_status()["auto_monitoring_paused"] is True
    assert get_monitor_status()["auto_monitoring_control_source"] == "manual_override"

    apply_auto_session_schedule(_at(13, 10, 30))
    assert get_monitor_status()["auto_monitoring_paused"] is True
    assert get_monitor_status()["auto_monitoring_control_source"] == "manual_override"

    apply_auto_session_schedule(_at(13, 11, 35))
    assert get_monitor_status()["auto_monitoring_control_source"] == "schedule"
    assert get_monitor_status()["auto_monitoring_paused"] is True

    apply_auto_session_schedule(_at(13, 13, 20))
    assert get_monitor_status()["auto_monitoring_control_source"] == "schedule"
    assert get_monitor_status()["auto_monitoring_paused"] is False


def test_manual_resume_during_midday_holds_until_afternoon_boundary(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(True)

    apply_auto_session_schedule(_at(13, 11, 35))
    assert get_monitor_status()["auto_monitoring_paused"] is True

    view = apply_manual_monitoring_command(paused=False, now=_at(13, 12, 0))

    assert view["manual_override_until"].strftime("%H:%M") == "13:20"
    assert get_monitor_status()["auto_monitoring_paused"] is False
    apply_auto_session_schedule(_at(13, 12, 30))
    assert get_monitor_status()["auto_monitoring_paused"] is False
    apply_auto_session_schedule(_at(13, 13, 20))
    assert get_monitor_status()["auto_monitoring_control_source"] == "schedule"
    assert get_monitor_status()["auto_monitoring_paused"] is False


def test_manual_pause_in_afternoon_holds_until_day_close(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(True)

    view = apply_manual_monitoring_command(paused=True, now=_at(13, 14, 0))

    assert view["manual_override_until"].strftime("%H:%M") == "15:05"
    apply_auto_session_schedule(_at(13, 14, 30))
    assert get_monitor_status()["auto_monitoring_paused"] is True
    apply_auto_session_schedule(_at(13, 15, 5))
    assert get_monitor_status()["auto_monitoring_control_source"] == "schedule"
    assert get_monitor_status()["auto_monitoring_paused"] is True


def test_restart_recomputes_current_session_without_saved_runtime_state(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(False)
    reset_monitor_status()
    configure_auto_monitoring(False)

    apply_auto_session_schedule(_at(13, 10, 0))

    assert get_monitor_status()["auto_monitoring_paused"] is False
    assert get_monitor_status()["auto_monitoring_control_source"] == "schedule"


def test_repeated_schedule_ticks_do_not_rewrite_settings(app, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    setting = AppSetting.query.filter_by(key=NIGHT_CLOSE_BUFFER_MINUTES_KEY).one()
    original_updated_at = setting.updated_at

    apply_auto_session_schedule(_at(13, 10, 0))
    db.session.refresh(setting)
    first_updated_at = setting.updated_at
    apply_auto_session_schedule(_at(13, 10, 30))
    db.session.refresh(setting)

    assert first_updated_at == original_updated_at
    assert setting.updated_at == original_updated_at


def test_worker_skips_provider_and_alerts_during_midday_break(app, make_task, monkeypatch):
    task = make_task(code="CU2608.SHF")
    original_base_price = task.base_price
    _enable_scheduler()
    configure_auto_monitoring(True)
    calls = {"engine": 0}

    def apply_midday_schedule():
        return apply_auto_session_schedule(_at(13, 12, 0))

    class ForbiddenEngine:
        def __init__(self, *args, **kwargs):
            calls["engine"] += 1

        def run_once(self):
            raise AssertionError("midday break must not poll provider")

    monkeypatch.setattr("app.monitor_worker.apply_auto_session_schedule", apply_midday_schedule)
    monkeypatch.setattr("app.monitor_worker.MonitoringEngine", ForbiddenEngine)

    run_monitor_cycle(app, object(), "ifind", "ready", True)

    status = get_monitor_status()
    assert calls["engine"] == 0
    assert status["last_poll_status"] == "skipped"
    assert "午间休盘中" in status["last_poll_message"]
    assert status["consecutive_market_failures"] == 0
    assert AlertLog.query.count() == 0
    assert MonitorTask.query.filter_by(code="CU2608.SHF").one().base_price == original_base_price


def test_worker_resumes_polling_at_afternoon_boundary(app, make_task, monkeypatch):
    make_task(code="CU2608.SHF")
    _enable_scheduler()
    configure_auto_monitoring(True)
    calls = {"engine": 0}

    def apply_afternoon_schedule():
        return apply_auto_session_schedule(_at(13, 13, 20))

    class SuccessfulEngine:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self):
            calls["engine"] += 1
            return MonitorRunResult(
                enabled_task_count=1,
                checked_task_count=1,
                alert_count=0,
                skipped_count=0,
                message="mock poll ok",
            )

    monkeypatch.setattr("app.monitor_worker.apply_auto_session_schedule", apply_afternoon_schedule)
    monkeypatch.setattr("app.monitor_worker.MonitoringEngine", SuccessfulEngine)

    run_monitor_cycle(app, object(), "ifind", "ready", True)

    assert calls["engine"] == 1
    assert get_monitor_status()["last_poll_status"] == "success"


def test_dashboard_shows_auto_session_controls_and_contract_results(client, make_task):
    make_task(code="CU2608.SHF")
    make_task(code="LC2609.GFE")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "自动交易时段" in page
    assert 'action="/settings/auto-session-scheduler"' in page
    assert "上午暂停" in page
    assert "下午恢复" in page
    assert "11:35" in page
    assert "13:20" in page
    assert "10:15-10:30 短休第一版仍保持轮询" in page
    assert "CU2608.SHF" in page
    assert "LC2609.GFE" in page
    assert "无夜盘" in page
    assert "法定节假日和节前无夜盘" in page
    assert 'class="auto-session-advanced-settings"' in page
    assert "高级设置" in page
    assert "仅用于修正未识别品种或临时覆盖内置交易时段，正常情况下无需填写。" in page
    assert "PRODUCT.EXCHANGE=HH:MM" in page
    assert "PRODUCT.EXCHANGE=NO_NIGHT" in page


def test_dashboard_updates_auto_session_settings(client):
    response = client.post(
        "/settings/auto-session-scheduler",
        data={
            "auto_session_scheduler_enabled": "on",
            "day_session_start": "08:50",
            "day_morning_stop": "11:35",
            "day_afternoon_start": "13:20",
            "day_session_stop": "15:05",
            "night_session_start": "20:50",
            "night_close_buffer_minutes": "15",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "自动交易时段已开启" in response.get_data(as_text=True)
    assert get_auto_session_scheduler_settings()["enabled"] is True
    assert get_auto_session_scheduler_settings()["night_close_buffer_minutes"] == 15


def test_healthz_includes_safe_session_schedule_summary(client, make_task):
    make_task(code="CU2608.SHF")
    _enable_scheduler()

    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert data["auto_session_scheduler_enabled"] is True
    assert data["session_scheduler_day_morning_stop"] == "11:35"
    assert data["session_scheduler_day_afternoon_start"] == "13:20"
    assert data["session_scheduler_night_available"] is True
    assert data["session_scheduler_night_close_time"] == "01:10"
    assert "access_token" not in response.get_data(as_text=True)
    assert "refresh_token" not in response.get_data(as_text=True)
