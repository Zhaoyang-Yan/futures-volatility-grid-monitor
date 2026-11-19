from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.status import get_monitor_status
from app.notify.notifier import (
    FeishuNotifier,
    build_feishu_payload,
    get_notification_channel_status,
    get_notifier,
    sign_feishu_message,
)


SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_FEISHU_SECRET = "YOUR_FEISHU_WEBHOOK_SECRET"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"code": 0}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("invalid json")
        return self.payload


class FakeHttpClient:
    def __init__(self, response=None, exc=None):
        self.response = response or FakeResponse()
        self.exc = exc
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


def alert_context():
    return {
        "code": "CU2608",
        "direction": "UP",
        "current_price": 108720,
        "old_base_price": 107600,
        "new_base_price": 108600,
        "grid_steps": 1,
        "threshold": 1000,
        "source_label": "iFind 实时行情",
        "created_at": "2026-06-30 21:00:00",
    }


def assert_safe_text(text: str):
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text
    assert "IFIND_REFRESH_TOKEN" not in text
    assert "IFIND_ACCESS_TOKEN" not in text
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_FEISHU_SECRET not in text
    assert "FEISHU_WEBHOOK_URL" not in text
    assert "FEISHU_WEBHOOK_SECRET" not in text
    assert ".env" not in text
    assert "headers" not in text


def test_feishu_missing_webhook_does_not_request_network():
    http_client = FakeHttpClient()
    notifier = FeishuNotifier(config={}, http_client=http_client)

    result = notifier.notify_alert(alert_context())

    assert result.success is False
    assert result.status == "missing_config"
    assert result.delivery_status == "missing_config"
    assert result.channel == "feishu"
    assert result.duration_ms is not None
    assert result.safe_error_summary == "飞书 webhook 未配置"
    assert result.can_retry is False
    assert "webhook 未配置" in result.message
    assert http_client.calls == []
    assert_safe_text(result.message)


def test_feishu_configured_sends_text_payload():
    http_client = FakeHttpClient(FakeResponse(payload={"code": 0}))
    notifier = FeishuNotifier(
        config={
            "FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL,
            "FEISHU_REQUEST_TIMEOUT_SECONDS": 7,
        },
        http_client=http_client,
    )

    result = notifier.notify_alert(alert_context())

    assert result.success is True
    assert result.status == "sent"
    assert result.delivery_status == "sent"
    assert result.channel == "feishu"
    assert result.duration_ms is not None
    assert result.safe_error_summary is None
    assert result.can_retry is False
    assert result.message == "飞书通知发送成功。"
    assert http_client.calls
    call = http_client.calls[0]
    assert call["url"] == SENSITIVE_FEISHU_URL
    assert call["timeout"] == 7
    assert call["json"]["msg_type"] == "text"
    assert "CU2608" in call["json"]["content"]["text"]
    assert "方向：上涨" in call["json"]["content"]["text"]
    assert "sign" not in call["json"]
    assert_safe_text(result.message)


def test_feishu_secret_adds_timestamp_and_sign():
    http_client = FakeHttpClient(FakeResponse(payload={"code": 0}))
    notifier = FeishuNotifier(
        config={
            "FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL,
            "FEISHU_WEBHOOK_SECRET": SENSITIVE_FEISHU_SECRET,
        },
        http_client=http_client,
        timestamp_func=lambda: 1710000000,
    )

    result = notifier.notify_alert(alert_context())
    payload = http_client.calls[0]["json"]

    assert result.success is True
    assert payload["timestamp"] == "1710000000"
    assert payload["sign"] == sign_feishu_message("1710000000", SENSITIVE_FEISHU_SECRET)
    assert payload["sign"] != SENSITIVE_FEISHU_SECRET
    assert SENSITIVE_FEISHU_SECRET not in str(payload)


def test_feishu_payload_without_secret_has_no_sign():
    payload = build_feishu_payload("hello", secret="")

    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "hello"
    assert "timestamp" not in payload
    assert "sign" not in payload


def test_feishu_http_500_returns_safe_error():
    notifier = FeishuNotifier(
        config={"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL},
        http_client=FakeHttpClient(FakeResponse(status_code=500, payload={"code": 999})),
    )

    result = notifier.notify_alert(alert_context())

    assert result.success is False
    assert result.status == "http_error"
    assert result.delivery_status == "http_error"
    assert result.can_retry is True
    assert "500" in result.error_message
    assert "HTTP 500" in result.safe_error_summary
    assert_safe_text(result.message)
    assert_safe_text(result.error_message)
    assert_safe_text(result.safe_error_summary)


def test_feishu_http_500_updates_runtime_diagnostics():
    notifier = FeishuNotifier(
        config={"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL},
        http_client=FakeHttpClient(FakeResponse(status_code=500, payload={"code": 999})),
    )

    notifier.notify_alert(alert_context())
    status = get_monitor_status()

    assert status["last_notification_channel"] == "feishu"
    assert status["last_notification_delivery_status"] == "http_error"
    assert status["last_notification_success"] is False
    assert status["last_notification_duration_ms"] is not None
    assert status["last_notification_can_retry"] is True
    assert status["last_notification_attempt_count"] == 1
    assert "HTTP 500" in status["last_notification_safe_error_summary"]
    assert_safe_text(status["last_notification_error"])
    assert_safe_text(status["last_notification_safe_error_summary"])


def test_feishu_business_error_returns_safe_error():
    notifier = FeishuNotifier(
        config={
            "FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL,
            "FEISHU_WEBHOOK_SECRET": SENSITIVE_FEISHU_SECRET,
        },
        http_client=FakeHttpClient(
            FakeResponse(payload={"code": 19024, "msg": f"bad {SENSITIVE_FEISHU_URL} {SENSITIVE_FEISHU_SECRET}"})
        ),
    )

    result = notifier.notify_alert(alert_context())

    assert result.success is False
    assert result.status == "feishu_error"
    assert result.delivery_status == "feishu_error"
    assert result.can_retry is True
    assert "19024" in result.error_message
    assert "19024" in result.safe_error_summary
    assert_safe_text(result.error_message)
    assert_safe_text(result.safe_error_summary)


def test_feishu_request_exception_is_isolated(make_task):
    class BrokenHttpClient:
        def post(self, url, json=None, timeout=None):
            raise TimeoutError("network down")

    notifier = FeishuNotifier(
        config={"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL},
        http_client=BrokenHttpClient(),
    )
    result = notifier.notify_alert(alert_context())

    assert result.success is False
    assert result.status == "request_error"
    assert result.delivery_status == "request_error"
    assert result.can_retry is True
    assert "TimeoutError" in result.error_message
    assert "TimeoutError" in result.safe_error_summary
    assert_safe_text(result.error_message)
    assert_safe_text(result.safe_error_summary)


def test_feishu_request_exception_does_not_break_alert_flow(monkeypatch, make_task):
    class BrokenHttpClient:
        def post(self, url, json=None, timeout=None):
            raise TimeoutError("network down")

    monkeypatch.setattr(
        "app.monitor.engine.get_notifier",
        lambda mode, config=None: FeishuNotifier(
            config={"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL},
            http_client=BrokenHttpClient(),
        ),
    )
    task = make_task()

    result = MonitoringEngine(notification_mode="feishu").process_task_price(
        task,
        106720,
        source="manual_test",
    )
    db.session.commit()

    db.session.refresh(task)
    assert result.triggered is True
    assert task.base_price == 106600
    assert AlertLog.query.count() == 1


def test_feishu_notifier_factory_and_channel_status():
    notifier = get_notifier("feishu", {"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL})
    status = get_notification_channel_status("feishu", {"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL})

    assert isinstance(notifier, FeishuNotifier)
    assert status.channel == "feishu"
    assert status.is_configured is True
    assert status.can_send_real_message is True
    assert "真实通知已启用" in status.message
    assert_safe_text(status.message)
