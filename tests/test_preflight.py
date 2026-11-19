from datetime import date

from app.db import MonitorTask, db
from app.monitor.ifind_permission import get_ifind_permission_status
from app.monitor.preflight import get_preflight_checks
from app.monitor.status import configure_auto_monitoring, get_runtime_diagnostics, pause_auto_monitoring
from app.notify.notifier import get_notification_channel_status


SENSITIVE_REFRESH_TOKEN = "YOUR_IFIND_REFRESH_TOKEN"
SENSITIVE_ACCESS_TOKEN = "YOUR_IFIND_ACCESS_TOKEN"
SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_FEISHU_SECRET = "YOUR_FEISHU_WEBHOOK_SECRET"
SENSITIVE_WECOM_URL = "https://example.invalid/wecom-webhook"


def assert_safe_text(text: str):
    assert SENSITIVE_REFRESH_TOKEN not in text
    assert SENSITIVE_ACCESS_TOKEN not in text
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_FEISHU_SECRET not in text
    assert SENSITIVE_WECOM_URL not in text
    assert "access_token" not in text
    assert "Bearer" not in text
    assert "IFIND_REFRESH_TOKEN" not in text
    assert "IFIND_ACCESS_TOKEN" not in text
    assert "FEISHU_WEBHOOK_URL" not in text
    assert "FEISHU_WEBHOOK_SECRET" not in text
    assert "WECOM_WEBHOOK_URL" not in text
    assert ".env" not in text
    assert "headers" not in text


def check_by_key(preflight, key):
    return next(check for check in preflight["checks"] if check["key"] == key)


def preflight_for_app(app, enabled_tasks_count=0):
    permission_status = get_ifind_permission_status(today=date(2026, 7, 10), config=app.config)
    channel_status = get_notification_channel_status(app.config["NOTIFICATION_MODE"], app.config)
    return get_preflight_checks(
        app.config,
        get_runtime_diagnostics(),
        permission_status,
        channel_status,
        enabled_tasks_count,
    )


def test_manual_only_dry_run_without_ifind_token_is_blocked(app):
    app.config.update(
        MARKET_DATA_MODE="manual_only",
        NOTIFICATION_MODE="dry_run",
        IFIND_REFRESH_TOKEN="",
        IFIND_ACCESS_TOKEN="",
    )

    preflight = preflight_for_app(app)

    assert preflight["overall_status"] == "blocked"
    assert check_by_key(preflight, "market_mode")["status"] == "blocked"
    assert "manual_only 安全模式" in check_by_key(preflight, "market_mode")["message"]
    assert check_by_key(preflight, "ifind_refresh_ready")["status"] == "blocked"
    assert check_by_key(preflight, "notification_mode")["status"] == "warning"
    assert "通知预览" in check_by_key(preflight, "notification_mode")["message"]
    assert_safe_text(str(preflight))


def test_ifind_feishu_enabled_task_is_ready_or_warning(app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN=SENSITIVE_REFRESH_TOKEN,
        IFIND_ACCESS_TOKEN=SENSITIVE_ACCESS_TOKEN,
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )
    task = MonitorTask(code="CU2608", base_price=105600, threshold=1000, is_active=True)
    db.session.add(task)
    db.session.commit()

    preflight = preflight_for_app(app, enabled_tasks_count=1)

    assert preflight["overall_status"] in {"ready", "warning"}
    assert check_by_key(preflight, "market_mode")["status"] == "pass"
    assert check_by_key(preflight, "ifind_refresh_ready")["status"] == "pass"
    assert check_by_key(preflight, "ifind_access_ready")["status"] == "pass"
    assert check_by_key(preflight, "enabled_tasks")["status"] == "pass"
    assert check_by_key(preflight, "notification_mode")["status"] == "pass"
    assert_safe_text(str(preflight))


def test_no_enabled_tasks_blocks_preflight(app):
    preflight = preflight_for_app(app, enabled_tasks_count=0)

    task_check = check_by_key(preflight, "enabled_tasks")
    assert task_check["status"] == "blocked"
    assert "没有启用" in task_check["message"]


def test_auto_monitoring_disabled_blocks_preflight(app):
    configure_auto_monitoring(False)

    preflight = preflight_for_app(app)

    monitor_check = check_by_key(preflight, "auto_monitoring")
    assert monitor_check["status"] == "blocked"
    assert "已关闭" in monitor_check["message"]


def test_auto_monitoring_paused_blocks_preflight(app):
    configure_auto_monitoring(True)
    pause_auto_monitoring()

    preflight = preflight_for_app(app)

    monitor_check = check_by_key(preflight, "auto_monitoring")
    assert monitor_check["status"] == "blocked"
    assert "已暂停" in monitor_check["message"]


def test_notification_off_blocks_preflight(app):
    app.config.update(NOTIFICATION_MODE="off")

    preflight = preflight_for_app(app)

    notification_check = check_by_key(preflight, "notification_mode")
    assert notification_check["status"] == "blocked"
    assert "通知已关闭" in notification_check["message"]


def test_feishu_without_webhook_blocks_preflight(app):
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL="")

    preflight = preflight_for_app(app)

    notification_check = check_by_key(preflight, "notification_mode")
    assert notification_check["status"] == "blocked"
    assert "webhook 未配置" in notification_check["message"]
    assert_safe_text(str(preflight))


def test_feishu_with_webhook_passes_preflight(app):
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL)

    preflight = preflight_for_app(app)

    notification_check = check_by_key(preflight, "notification_mode")
    assert notification_check["status"] == "pass"
    assert "飞书真实通知已启用" in notification_check["message"]
    assert_safe_text(str(preflight))


def test_permission_reminder_is_warning(app):
    permission_status = get_ifind_permission_status(today=date(2026, 7, 21), config=app.config)
    channel_status = get_notification_channel_status(app.config["NOTIFICATION_MODE"], app.config)

    preflight = get_preflight_checks(
        app.config,
        get_runtime_diagnostics(),
        permission_status,
        channel_status,
        enabled_tasks_count=1,
    )

    permission_check = check_by_key(preflight, "ifind_permission")
    assert permission_check["status"] == "warning"
    assert "权限即将更新" in permission_check["message"]


def test_burst_disabled_is_warning(app):
    app.config.update(NOTIFICATION_BURST_ENABLED="false")

    preflight = preflight_for_app(app)

    burst_check = check_by_key(preflight, "burst_protection")
    assert burst_check["status"] == "warning"
    assert "关闭" in burst_check["message"]


def test_dashboard_shows_preflight_section(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "运行前检查" in page
    assert "通过：" in page
    assert "警告：" in page
    assert "阻塞：" in page
    assert "行情模式" in page
    assert "通知模式" in page
    assert_safe_text(page)


def test_healthz_contains_preflight_fields(client):
    response = client.get("/healthz")
    payload = response.get_json()
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "preflight_overall_status" in payload
    assert "preflight_ready_count" in payload
    assert "preflight_warning_count" in payload
    assert "preflight_blocked_count" in payload
    assert "preflight_checks" in payload
    assert isinstance(payload["preflight_checks"], list)
    assert_safe_text(body)
