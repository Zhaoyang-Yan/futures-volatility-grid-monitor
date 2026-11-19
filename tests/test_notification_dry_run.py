from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.status import get_monitor_status
from app.notify.notifier import DryRunNotifier, OffNotifier, UnsupportedNotifier, get_notifier


def alert_context():
    return {
        "code": "CU2608",
        "direction": "UP",
        "current_price": 108720,
        "old_base_price": 107600,
        "new_base_price": 108600,
        "grid_steps": 1,
        "threshold": 1000,
        "source": "ifind",
        "source_label": "iFind 实时行情",
        "created_at": "2026-06-29 21:00:00",
    }


def test_notification_off_does_not_generate_content():
    result = OffNotifier().notify_alert(alert_context())

    assert result.success is False
    assert result.enabled is False
    assert result.mode == "off"
    assert result.status == "disabled"
    assert result.delivery_status == "disabled"
    assert result.channel == "off"
    assert result.message == "未启用"
    assert result.content is None
    assert get_monitor_status()["last_notification_status"] == "未启用"


def test_dry_run_notification_generates_safe_content():
    result = DryRunNotifier().notify_alert(alert_context())

    assert result.success is True
    assert result.enabled is True
    assert result.mode == "dry_run"
    assert result.status == "dry_run_generated"
    assert result.delivery_status == "dry_run"
    assert result.channel == "dry_run"
    assert result.duration_ms is not None
    assert result.can_retry is False
    assert result.message == "Dry Run 已生成"
    assert "CU2608" in result.content
    assert "方向：上涨" in result.content
    assert "当前价格：108720.00" in result.content
    assert "旧基准价：107600.00" in result.content
    assert "新基准价：108600.00" in result.content
    assert "突破网格数：1" in result.content
    assert "access_token" not in result.content
    assert "refresh_token" not in result.content
    assert "Bearer" not in result.content
    assert "IFIND_REFRESH_TOKEN" not in result.content


def test_unknown_notification_mode_is_disabled_safely():
    notifier = get_notifier("telegram")
    result = notifier.notify_alert(alert_context())

    assert isinstance(notifier, UnsupportedNotifier)
    assert result.success is False
    assert result.mode == "telegram"
    assert result.status == "unsupported"
    assert "尚未支持" in result.message
    assert result.content is None


def test_engine_dry_run_notification_updates_status(make_task):
    task = make_task()

    result = MonitoringEngine(notification_mode="dry_run").process_task_price(
        task,
        106720,
        source="manual_test",
    )
    db.session.commit()

    assert result.triggered is True
    assert AlertLog.query.count() == 1
    status = get_monitor_status()
    assert status["notification_mode"] == "dry_run"
    assert status["last_notification_status"] == "Dry Run 已生成"
    assert "CU2608" in status["last_notification_content"]
    assert status["last_notification_success"] is True
    assert status["last_notification_error"] is None


def test_engine_notification_off_updates_status_without_content(make_task):
    task = make_task()

    result = MonitoringEngine(notification_mode="off").process_task_price(
        task,
        106720,
        source="manual_test",
    )
    db.session.commit()

    assert result.triggered is True
    status = get_monitor_status()
    assert status["notification_mode"] == "off"
    assert status["last_notification_status"] == "未启用"
    assert status["last_notification_content"] is None
    assert status["last_notification_success"] is False


def test_notification_exception_does_not_break_grid_rebase(monkeypatch, make_task):
    class FailingNotifier:
        mode = "failing_test"

        def notify_alert(self, alert_context):
            raise RuntimeError(
                "boom access_token refresh_token Bearer IFIND_REFRESH_TOKEN headers .env"
            )

    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: FailingNotifier())
    task = make_task()

    result = MonitoringEngine(notification_mode="dry_run").process_task_price(
        task,
        106720,
        source="manual_test",
    )
    db.session.commit()

    db.session.refresh(task)
    assert result.triggered is True
    assert task.base_price == 106600
    assert AlertLog.query.count() == 1

    status = get_monitor_status()
    assert status["notification_mode"] == "failing_test"
    assert status["last_notification_status"] == "通知失败"
    assert status["last_notification_success"] is False
    assert status["last_notification_error"]
    assert "access_token" not in status["last_notification_error"]
    assert "refresh_token" not in status["last_notification_error"]
    assert "Bearer" not in status["last_notification_error"]
    assert "IFIND_REFRESH_TOKEN" not in status["last_notification_error"]
    assert "headers" not in status["last_notification_error"]
    assert ".env" not in status["last_notification_error"]


def test_dashboard_notification_preview_has_copy_button_and_safe_source(client, make_task):
    task = make_task()
    result = MonitoringEngine(notification_mode="dry_run").process_task_price(
        task,
        106720,
        message_prefix="[手动测试]",
        source="manual_test",
    )
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "复制通知" in page
    assert "copy-notification-btn" in page
    assert "data-copy-target=" in page
    assert result.code in page
    assert "方向：上涨" in page
    assert "当前价格：106720.00" in page
    assert "旧基准价：105600.00" in page
    assert "新基准价：106600.00" in page
    assert "突破网格数：1" in page
    assert "阈值：1000.00" in page
    assert "行情来源：历史记录" not in page
    assert "行情来源：未记录" in page
    assert "access_token" not in page
    assert "refresh_token" not in page
    assert "Bearer" not in page
    assert "IFIND_REFRESH_TOKEN" not in page


def test_dashboard_notification_error_is_safe(client, monkeypatch, make_task):
    class FailingNotifier:
        mode = "failing_test"

        def notify_alert(self, alert_context):
            raise RuntimeError("access_token refresh_token Bearer IFIND_REFRESH_TOKEN headers .env")

    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: FailingNotifier())
    task = make_task()

    MonitoringEngine(notification_mode="dry_run").process_task_price(
        task,
        106720,
        source="manual_test",
    )
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "最近通知错误" in page
    assert "access_token" not in page
    assert "refresh_token" not in page
    assert "Bearer" not in page
    assert "IFIND_REFRESH_TOKEN" not in page
    assert "headers" not in page
    assert ".env" not in page
