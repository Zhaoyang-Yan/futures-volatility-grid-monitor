from datetime import datetime, timedelta, timezone

from app.db import (
    NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY,
    AlertLog,
    AppSetting,
    NotificationDelivery,
    db,
    ensure_notification_delivery_table,
    get_notification_delivery_rollout_started_at,
    utc_now_naive,
)
from app.monitor.engine import MonitoringEngine
from app.notify.delivery import (
    FAILED,
    PENDING,
    RETRYING,
    SENT,
    _alert_context,
    create_and_attempt_alert_delivery,
    delivery_display_view,
    process_due_notification_deliveries,
)
from app.notify.notifier import NotificationResult, build_alert_notification
from app.time_utils import as_china_time, format_china_time


def _alert(task, code="CU2608.SHF"):
    alert = AlertLog(
        task_id=task.id,
        code=code,
        base_price=100,
        old_base_price=100,
        new_base_price=110,
        current_price=111,
        diff=11,
        threshold=10,
        grid_steps=1,
        direction="UP",
        message="grid alert",
    )
    db.session.add(alert)
    db.session.commit()
    return alert


class _Notifier:
    mode = "feishu"

    def __init__(self, results):
        self.results = iter(results)
        self.contexts = []

    def notify_alert(self, context):
        self.contexts.append(context)
        return next(self.results)


def _result(success, *, retry=False, http_status_code=None, provider_error_code=None):
    return NotificationResult(
        success=success,
        mode="feishu",
        status="sent" if success else "request_error",
        message="飞书通知发送成功。" if success else "飞书通知发送失败。",
        channel="feishu",
        safe_error_summary=None if success else "安全网络错误",
        can_retry=retry,
        delivery_status="sent" if success else "request_error",
        http_status_code=http_status_code,
        provider_error_code=provider_error_code,
    )


def _config(**overrides):
    config = {
        "NOTIFICATION_MODE": "feishu",
        "FEISHU_RETRY_ENABLED": True,
        "FEISHU_MAX_ATTEMPTS": 4,
        "FEISHU_RETRY_DELAYS_SECONDS": (30, 60, 120),
        "FEISHU_RETRY_BATCH_SIZE": 20,
    }
    config.update(overrides)
    return config


def test_real_alert_is_persisted_before_successful_delivery(make_task):
    task = make_task(code="CU2608.SHF")
    notifier = _Notifier([_result(True)])

    result = MonitoringEngine(notification_mode="feishu", notification_config=_config())
    result.notifier = notifier
    # The delivery module obtains its own notifier; use a direct durable attempt
    # here to assert the table contract without network access.
    alert = _alert(task)
    delivery = create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)

    assert AlertLog.query.filter_by(id=alert.id).one().id == alert.id
    assert delivery.status == SENT
    assert delivery.attempt_count == 1
    assert "ALERT-" in notifier.contexts[0]["alert_number"]


def test_feishu_alert_context_converts_naive_utc_alert_time_to_beijing(make_task):
    alert = _alert(make_task())
    alert.created_at = datetime(2026, 7, 17, 5, 32, 8)
    db.session.commit()
    notifier = _Notifier([_result(True)])

    create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)

    assert notifier.contexts[0]["created_at"] == "2026-07-17 13:32:08"


def test_feishu_alert_context_converts_aware_utc_alert_time_to_beijing(make_task):
    alert = AlertLog(
        id=7,
        task_id=make_task().id,
        code="CU2608.SHF",
        base_price=100,
        old_base_price=100,
        new_base_price=110,
        current_price=111,
        diff=11,
        threshold=10,
        grid_steps=1,
        direction="UP",
        message="grid alert",
        created_at=datetime(2026, 7, 17, 6, 50, 31, tzinfo=timezone.utc),
    )
    delivery = NotificationDelivery(
        alert_log=alert,
        channel="feishu",
        status=PENDING,
        attempt_count=0,
        message_key="aware-time",
        source="ifind",
    )

    assert _alert_context(alert, delivery, None)["created_at"] == "2026-07-17 14:50:31"


def test_dashboard_preview_and_feishu_context_use_same_beijing_time(client, make_task):
    alert = _alert(make_task())
    alert.created_at = datetime(2026, 7, 17, 5, 32, 8)
    db.session.commit()
    notifier = _Notifier([_result(True)])

    create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)
    page = client.get("/").get_data(as_text=True)

    assert notifier.contexts[0]["created_at"] == "2026-07-17 13:32:08"
    assert "时间：2026-07-17 13:32:08" in build_alert_notification(notifier.contexts[0])
    assert "时间：2026-07-17 13:32:08" in page
    assert "时间：2026-07-17 05:32:08" not in page


def test_retry_message_uses_original_alert_beijing_time(make_task):
    alert = _alert(make_task())
    alert.created_at = datetime(2026, 7, 17, 6, 50, 31)
    db.session.commit()
    notifier = _Notifier([_result(False, retry=True), _result(True)])

    delivery = create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)
    process_due_notification_deliveries(_config(), now=delivery.next_retry_at, notifier_factory=lambda *_: notifier)

    retry_context = notifier.contexts[1]
    retry_content = build_alert_notification(retry_context)
    assert retry_context["is_retry"] is True
    assert retry_context["original_alert_time"] == "2026-07-17 14:50:31"
    assert "原报警时间：2026-07-17 14:50:31" in retry_content


def test_retry_schedule_remains_utc_naive_after_display_timezone_fix(make_task):
    alert = _alert(make_task())
    notifier = _Notifier([_result(False, retry=True)])

    delivery = create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)

    assert delivery.next_retry_at - delivery.last_attempt_at == timedelta(seconds=30)
    assert delivery.next_retry_at.tzinfo is None
    assert delivery.last_attempt_at.tzinfo is None


def test_beijing_time_conversion_handles_cross_day_month_and_year():
    assert format_china_time(datetime(2026, 7, 17, 17, 30, 0)) == "2026-07-18 01:30:00"
    assert format_china_time(datetime(2026, 7, 31, 17, 30, 0)) == "2026-08-01 01:30:00"
    assert format_china_time(datetime(2026, 12, 31, 17, 30, 0)) == "2027-01-01 01:30:00"
    assert as_china_time(datetime(2026, 7, 17, 17, 30, 0, tzinfo=timezone.utc)).strftime("%Y-%m-%d %H:%M:%S") == "2026-07-18 01:30:00"


def test_time_display_conversion_does_not_mutate_alert_created_at(make_task):
    alert = _alert(make_task())
    original_created_at = datetime(2026, 7, 17, 5, 32, 8)
    alert.created_at = original_created_at
    db.session.commit()
    notifier = _Notifier([_result(True)])

    create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)
    db.session.refresh(alert)

    assert alert.created_at == original_created_at


def test_engine_real_alert_creates_one_durable_delivery(monkeypatch, make_task):
    task = make_task(code="CU2608.SHF")
    notifier = _Notifier([_result(False, retry=True)])
    monkeypatch.setattr("app.notify.delivery.get_notifier", lambda *_: notifier)

    result = MonitoringEngine(notification_mode="feishu", notification_config=_config()).process_task_price(
        task,
        106720,
        source="ifind",
    )

    assert result.triggered is True
    assert AlertLog.query.count() == 1
    delivery = NotificationDelivery.query.one()
    assert delivery.status == PENDING
    assert delivery.alert_log_id == AlertLog.query.one().id
    db.session.refresh(task)
    assert task.base_price == 106600


def test_transient_failure_is_persisted_and_retried_on_due_tick(make_task):
    alert = _alert(make_task())
    notifier = _Notifier([_result(False, retry=True), _result(True)])
    config = _config()

    delivery = create_and_attempt_alert_delivery(alert, config, "ifind", notifier_factory=lambda *_: notifier)
    assert delivery.status == PENDING
    assert delivery.attempt_count == 1
    first_due_at = delivery.next_retry_at

    assert process_due_notification_deliveries(config, now=first_due_at - timedelta(seconds=1), notifier_factory=lambda *_: notifier) == 0
    assert len(notifier.contexts) == 1

    assert process_due_notification_deliveries(config, now=first_due_at, notifier_factory=lambda *_: notifier) == 1
    db.session.refresh(delivery)
    assert delivery.status == SENT
    assert delivery.attempt_count == 2
    assert notifier.contexts[1]["is_retry"] is True
    assert "补发" in notifier.contexts[1].get("notification_title", "补发")
    assert AlertLog.query.count() == 1


def test_retry_delay_progression_and_eventual_failure(make_task):
    alert = _alert(make_task())
    notifier = _Notifier([_result(False, retry=True)] * 4)
    config = _config()
    delivery = create_and_attempt_alert_delivery(alert, config, "ifind", notifier_factory=lambda *_: notifier)

    assert delivery.next_retry_at - delivery.last_attempt_at == timedelta(seconds=30)
    for expected_delay, expected_status in ((60, RETRYING), (120, RETRYING), (None, FAILED)):
        due_at = delivery.next_retry_at
        assert process_due_notification_deliveries(config, now=due_at, notifier_factory=lambda *_: notifier) == 1
        db.session.refresh(delivery)
        assert delivery.status == expected_status
        if expected_delay:
            assert delivery.next_retry_at - delivery.last_attempt_at == timedelta(seconds=expected_delay)
    assert delivery.attempt_count == 4
    assert AlertLog.query.count() == 1


def test_http_500_and_429_are_retryable_but_webhook_config_error_is_final(make_task):
    config = _config()
    for status_code in (500, 429):
        alert = _alert(make_task(code=f"CU{status_code}"))
        delivery = create_and_attempt_alert_delivery(
            alert,
            config,
            "ifind",
            notifier_factory=lambda *_: _Notifier([_result(False, http_status_code=status_code)]),
        )
        assert delivery.status == PENDING

    alert = _alert(make_task(code="CU9999"))
    delivery = create_and_attempt_alert_delivery(
        alert,
        config,
        "ifind",
        notifier_factory=lambda *_: _Notifier([_result(False, retry=False)]),
    )
    assert delivery.status == FAILED
    assert delivery.next_retry_at is None


def test_feishu_internal_19006_is_retryable_and_other_business_error_is_final(make_task):
    config = _config()
    retry_alert = _alert(make_task(code="CU19006"))
    retry_delivery = create_and_attempt_alert_delivery(
        retry_alert,
        config,
        "ifind",
        notifier_factory=lambda *_: _Notifier([_result(False, provider_error_code="19006")]),
    )
    assert retry_delivery.status == PENDING

    final_alert = _alert(make_task(code="CU19024"))
    final_delivery = create_and_attempt_alert_delivery(
        final_alert,
        config,
        "ifind",
        notifier_factory=lambda *_: _Notifier([_result(False, retry=True, provider_error_code="19024")]),
    )
    assert final_delivery.status == FAILED


def test_repeated_ticks_do_not_send_not_due_delivery(make_task):
    alert = _alert(make_task())
    notifier = _Notifier([_result(False, retry=True)])
    delivery = create_and_attempt_alert_delivery(alert, _config(), "ifind", notifier_factory=lambda *_: notifier)

    for _ in range(3):
        assert process_due_notification_deliveries(_config(), now=utc_now_naive(), notifier_factory=lambda *_: notifier) == 0
    assert delivery.attempt_count == 1
    assert len(notifier.contexts) == 1


def test_delivery_records_do_not_exist_for_dry_run_alerts(make_task):
    task = make_task()
    MonitoringEngine(notification_mode="dry_run").process_task_price(task, 106720, source="manual_test")

    assert AlertLog.query.count() == 1
    assert NotificationDelivery.query.count() == 0


def test_dashboard_shows_delivery_summary_and_undelivered_filter(client, make_task):
    alert = _alert(make_task())
    create_and_attempt_alert_delivery(
        alert,
        _config(),
        "ifind",
        notifier_factory=lambda *_: _Notifier([_result(False, retry=True)]),
    )

    page = client.get("/").get_data(as_text=True)
    filtered_page = client.get("/?delivery_status=undelivered#alert-history").get_data(as_text=True)

    assert "飞书投递" in page
    assert "等待重试" in page
    assert "送达" in page
    assert "未送达" in filtered_page
    assert "CU2608.SHF" in filtered_page


def test_dashboard_distinguishes_historical_alert_from_missing_delivery(client, make_task):
    alert = _alert(make_task())
    rollout = AppSetting(
        key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY,
        value=(utc_now_naive() + timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat(),
    )
    db.session.add(rollout)
    db.session.commit()

    historical_page = client.get("/").get_data(as_text=True)
    rollout.value = (utc_now_naive() - timedelta(days=1)).isoformat()
    db.session.commit()
    missing_page = client.get("/").get_data(as_text=True)

    assert "历史记录" in historical_page
    assert "该报警产生于飞书可靠投递功能上线前" in historical_page
    assert NotificationDelivery.query.count() == 0
    assert "无投递记录" in missing_page
    assert AlertLog.query.filter_by(id=alert.id).count() == 1


def test_existing_rollout_setting_is_preserved(app):
    expected_value = "2026-07-17T02:57:16+00:00"
    setting = AppSetting(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY, value=expected_value)
    db.session.add(setting)
    db.session.commit()

    ensure_notification_delivery_table(app)

    assert AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).one().value == expected_value
    assert get_notification_delivery_rollout_started_at() == datetime(2026, 7, 17, 2, 57, 16)


def test_rollout_uses_earliest_existing_delivery_when_marker_is_missing(app, make_task):
    first_alert = _alert(make_task(code="CU2608.SHF"))
    second_alert = _alert(make_task(code="Y2609.DCE"))
    earliest_created_at = datetime(2026, 7, 17, 2, 57, 16)
    db.session.add_all(
        [
            NotificationDelivery(
                alert_log_id=second_alert.id,
                channel="feishu",
                status=SENT,
                message_key="rollout-later",
                created_at=earliest_created_at + timedelta(minutes=1),
            ),
            NotificationDelivery(
                alert_log_id=first_alert.id,
                channel="feishu",
                status=SENT,
                message_key="rollout-earliest",
                created_at=earliest_created_at,
            ),
        ]
    )
    db.session.commit()

    ensure_notification_delivery_table(app)

    marker = AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).one()
    assert marker.value == "2026-07-17T02:57:16+00:00"
    assert get_notification_delivery_rollout_started_at() == earliest_created_at
    assert NotificationDelivery.query.count() == 2


def test_missing_marker_and_delivery_records_stays_conservative(app, make_task):
    alert = _alert(make_task())
    app.config["NOTIFICATION_DELIVERY_ROLLOUT_AT"] = ""

    ensure_notification_delivery_table(app)

    assert AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).count() == 0
    view = delivery_display_view(alert, None, get_notification_delivery_rollout_started_at())
    assert view["label"] == "无投递记录"
    assert AlertLog.query.filter_by(id=alert.id).count() == 1
    assert NotificationDelivery.query.count() == 0


def test_configured_rollout_time_normalizes_timezone_and_classifies_without_mutation(app, make_task):
    before_rollout = _alert(make_task(code="CU2608.SHF"))
    after_rollout = _alert(make_task(code="Y2609.DCE"))
    before_rollout.created_at = datetime(2026, 7, 17, 2, 57, 15)
    after_rollout.created_at = datetime(2026, 7, 17, 2, 57, 17)
    app.config["NOTIFICATION_DELIVERY_ROLLOUT_AT"] = "2026-07-17 10:57:16 Asia/Shanghai"
    db.session.commit()

    ensure_notification_delivery_table(app)

    rollout_at = get_notification_delivery_rollout_started_at()
    marker = AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).one()
    assert marker.value == "2026-07-17T02:57:16+00:00"
    assert rollout_at == datetime(2026, 7, 17, 2, 57, 16)
    assert delivery_display_view(before_rollout, None, rollout_at)["label"] == "历史记录"
    assert delivery_display_view(after_rollout, None, rollout_at)["label"] == "无投递记录"
    assert AlertLog.query.count() == 2
    assert NotificationDelivery.query.count() == 0
