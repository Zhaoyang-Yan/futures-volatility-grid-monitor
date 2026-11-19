from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.notify.notifier import (
    FeishuPlaceholderNotifier,
    WeComPlaceholderNotifier,
    get_notification_channel_status,
    get_notifier,
)


SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_FEISHU_SECRET = "YOUR_FEISHU_WEBHOOK_SECRET"
SENSITIVE_WECOM_URL = "https://example.invalid/wecom-webhook"


def assert_safe_text(text: str):
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text
    assert "IFIND_REFRESH_TOKEN" not in text
    assert "IFIND_ACCESS_TOKEN" not in text
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_FEISHU_SECRET not in text
    assert SENSITIVE_WECOM_URL not in text
    assert ".env" not in text
    assert "headers" not in text


def test_dry_run_channel_status():
    status = get_notification_channel_status("dry_run")

    assert status.channel == "dry_run"
    assert status.is_configured is True
    assert status.can_send_real_message is False


def test_off_channel_status():
    status = get_notification_channel_status("off")

    assert status.channel == "off"
    assert status.is_configured is False
    assert status.can_send_real_message is False


def test_feishu_placeholder_missing_config():
    status = get_notification_channel_status("feishu_placeholder", {})
    notifier = get_notifier("feishu_placeholder", {})
    result = notifier.notify_alert({})

    assert isinstance(notifier, FeishuPlaceholderNotifier)
    assert status.channel == "feishu"
    assert status.is_configured is False
    assert status.is_placeholder is True
    assert status.can_send_real_message is False
    assert "未配置" in status.message
    assert result.success is False
    assert result.status == "missing_config"
    assert_safe_text(status.message)
    assert_safe_text(result.message)


def test_feishu_placeholder_configured_without_exposing_secret():
    config = {
        "FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL,
        "FEISHU_WEBHOOK_SECRET": SENSITIVE_FEISHU_SECRET,
    }
    status = get_notification_channel_status("feishu_placeholder", config)
    result = get_notifier("feishu_placeholder", config).notify_alert({})

    assert status.channel == "feishu"
    assert status.is_configured is True
    assert status.is_placeholder is True
    assert status.can_send_real_message is False
    assert "不发送真实通知" in status.message
    assert result.success is True
    assert result.status == "configured_placeholder"
    assert_safe_text(status.message)
    assert_safe_text(result.message)


def test_wecom_placeholder_missing_config():
    status = get_notification_channel_status("wecom_placeholder", {})
    notifier = get_notifier("wecom_placeholder", {})
    result = notifier.notify_alert({})

    assert isinstance(notifier, WeComPlaceholderNotifier)
    assert status.channel == "wecom"
    assert status.is_configured is False
    assert status.is_placeholder is True
    assert status.can_send_real_message is False
    assert "未配置" in status.message
    assert result.success is False
    assert result.status == "missing_config"
    assert_safe_text(status.message)
    assert_safe_text(result.message)


def test_wecom_placeholder_configured_without_exposing_url():
    config = {"WECOM_WEBHOOK_URL": SENSITIVE_WECOM_URL}
    status = get_notification_channel_status("wecom_placeholder", config)
    result = get_notifier("wecom_placeholder", config).notify_alert({})

    assert status.channel == "wecom"
    assert status.is_configured is True
    assert status.is_placeholder is True
    assert status.can_send_real_message is False
    assert "不发送真实通知" in status.message
    assert result.success is True
    assert result.status == "configured_placeholder"
    assert_safe_text(status.message)
    assert_safe_text(result.message)


def test_unknown_channel_status_is_safe():
    status = get_notification_channel_status("unknown_mode")

    assert status.channel == "unsupported"
    assert status.can_send_real_message is False
    assert status.error == "unsupported_mode"


def test_placeholder_notification_does_not_break_alert_flow(make_task):
    task = make_task()

    result = MonitoringEngine(
        notification_mode="feishu_placeholder",
        notification_config={},
    ).process_task_price(task, 106720, source="manual_test")
    db.session.commit()

    db.session.refresh(task)
    assert result.triggered is True
    assert task.base_price == 106600
    assert AlertLog.query.count() == 1


def test_dashboard_shows_feishu_placeholder_channel_without_secret(app, client):
    app.config.update(
        NOTIFICATION_MODE="feishu_placeholder",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "通知通道" in page
    assert "通道配置" in page
    assert "真实发送" in page
    assert "最近发送结果" in page
    assert "最近发送耗时" in page
    assert "可重试" in page
    assert "安全错误摘要" in page
    assert "飞书" in page
    assert "已配置" in page
    assert "不发送真实通知" in page
    assert_safe_text(page)


def test_dashboard_shows_wecom_placeholder_channel_without_url(app, client):
    app.config.update(
        NOTIFICATION_MODE="wecom_placeholder",
        WECOM_WEBHOOK_URL=SENSITIVE_WECOM_URL,
    )

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "企业微信" in page
    assert "已配置" in page
    assert "不发送真实通知" in page
    assert_safe_text(page)


def test_healthz_contains_notification_channel_fields(app, client):
    app.config.update(
        NOTIFICATION_MODE="feishu_placeholder",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )

    response = client.get("/healthz")
    payload = response.get_json()
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert payload["notification_channel"] == "feishu"
    assert payload["notification_channel_configured"] is True
    assert payload["notification_channel_placeholder"] is True
    assert payload["notification_can_send_real_message"] is False
    assert "notification_channel_message" in payload
    assert "notification_channel_error" in payload
    assert "last_notification_channel" in payload
    assert "last_notification_delivery_status" in payload
    assert "last_notification_duration_ms" in payload
    assert "last_notification_safe_error_summary" in payload
    assert "last_notification_can_retry" in payload
    assert "last_notification_attempt_count" in payload
    assert_safe_text(response_text)


def test_dashboard_shows_feishu_real_channel_and_signature_status(app, client):
    app.config.update(
        NOTIFICATION_MODE="feishu",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "通知通道" in page
    assert "飞书" in page
    assert "通道配置：已配置" in page
    assert "真实发送：已启用" in page
    assert "签名校验：已配置" in page
    assert "飞书真实通知已启用" in page
    assert_safe_text(page)


def test_dashboard_shows_feishu_real_channel_missing_webhook(app, client):
    app.config.update(
        NOTIFICATION_MODE="feishu",
        FEISHU_WEBHOOK_URL="",
        FEISHU_WEBHOOK_SECRET="",
    )

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "通知通道" in page
    assert "飞书" in page
    assert "通道配置：未配置" in page
    assert "真实发送：未启用" in page
    assert "签名校验：未配置" in page
    assert "飞书 webhook 未配置，无法发送真实通知" in page


def test_healthz_contains_feishu_config_fields(app, client):
    app.config.update(
        NOTIFICATION_MODE="feishu",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )

    response = client.get("/healthz")
    payload = response.get_json()
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert payload["notification_channel"] == "feishu"
    assert payload["notification_channel_configured"] is True
    assert payload["notification_channel_placeholder"] is False
    assert payload["notification_can_send_real_message"] is True
    assert payload["feishu_webhook_configured"] is True
    assert payload["feishu_secret_configured"] is True
    assert_safe_text(response_text)
