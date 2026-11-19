from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.status import get_monitor_status
from app.notify.notifier import FeishuNotifier, build_alert_notification


SENSITIVE_TERMS = [
    "access_token",
    "refresh_token",
    "Bearer",
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCESS_TOKEN",
    ".env",
    "headers",
]


class CountingNotifier:
    mode = "counting_test"

    def __init__(self):
        self.calls = []

    def notify_alert(self, alert_context):
        self.calls.append(alert_context)


class FakeResponse:
    status_code = 200

    def json(self):
        return {"code": 0}


class FakeHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()


def make_counting_engine(monkeypatch, config=None):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    engine = MonitoringEngine(notification_mode="dry_run", notification_config=config or {})
    return engine, notifier


def trigger_price(engine, task, price):
    result = engine.process_task_price(task, price, source="manual_test")
    db.session.commit()
    db.session.refresh(task)
    return result


def assert_safe_text(text: str):
    for term in SENSITIVE_TERMS:
        assert term not in text


def test_high_frequency_alert_warning_is_added_to_third_alert(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(
        monkeypatch,
        {
            "NOTIFICATION_BURST_ENABLED": "true",
            "NOTIFICATION_BURST_WINDOW_SECONDS": "300",
            "NOTIFICATION_BURST_THRESHOLD": "3",
        },
    )

    trigger_price(engine, task, 101000)
    trigger_price(engine, task, 102000)
    trigger_price(engine, task, 103000)

    assert AlertLog.query.count() == 3
    assert len(notifier.calls) == 3
    third_context = notifier.calls[-1]
    content = build_alert_notification(third_context)

    assert third_context["burst_alert_detected"] is True
    assert third_context["burst_alert_count"] == 3
    assert "高频报警" in content
    assert "最近 5 分钟内该合约已触发 3 次报警" in content
    assert_safe_text(content)


def test_new_grid_breaks_are_not_blocked_by_burst_warning(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(
        monkeypatch,
        {"NOTIFICATION_BURST_THRESHOLD": "2"},
    )

    first = trigger_price(engine, task, 102000)
    second = trigger_price(engine, task, 103000)

    assert first.old_base_price == 100000
    assert first.new_base_price == 102000
    assert second.old_base_price == 102000
    assert second.new_base_price == 103000
    assert AlertLog.query.count() == 2
    assert len(notifier.calls) == 2
    assert "高频报警" in build_alert_notification(notifier.calls[-1])


def test_duplicate_alert_skips_duplicate_notification(monkeypatch, make_task):
    task = make_task(base_price=103600, threshold=1000)
    engine, notifier = make_counting_engine(monkeypatch)

    trigger_price(engine, task, 105800)
    task.base_price = 103600
    trigger_price(engine, task, 105820)

    status = get_monitor_status()
    assert AlertLog.query.count() == 1
    assert len(notifier.calls) == 1
    assert status["last_alert_dedup_status"] == "duplicate_skipped"
    assert status["last_duplicate_notification_skipped"] is True


def test_volatility_and_high_frequency_warnings_can_coexist(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(
        monkeypatch,
        {"NOTIFICATION_BURST_THRESHOLD": "3"},
    )

    trigger_price(engine, task, 101000)
    trigger_price(engine, task, 102000)
    trigger_price(engine, task, 104000)

    content = build_alert_notification(notifier.calls[-1])
    assert "剧烈波动" in content
    assert "高频报警" in content
    assert_safe_text(content)


def test_single_alert_has_no_high_frequency_warning(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(monkeypatch)

    trigger_price(engine, task, 101000)

    content = build_alert_notification(notifier.calls[-1])
    assert "高频报警" not in content


def test_notification_burst_can_be_disabled(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(
        monkeypatch,
        {
            "NOTIFICATION_BURST_ENABLED": "false",
            "NOTIFICATION_BURST_THRESHOLD": "2",
        },
    )

    trigger_price(engine, task, 101000)
    trigger_price(engine, task, 102000)
    trigger_price(engine, task, 103000)

    content = build_alert_notification(notifier.calls[-1])
    assert notifier.calls[-1]["burst_alert_enabled"] is False
    assert "高频报警" not in content


def test_invalid_burst_config_uses_safe_defaults(monkeypatch, make_task):
    task = make_task(base_price=100000, threshold=1000)
    engine, notifier = make_counting_engine(
        monkeypatch,
        {
            "NOTIFICATION_BURST_ENABLED": "maybe",
            "NOTIFICATION_BURST_WINDOW_SECONDS": "bad",
            "NOTIFICATION_BURST_THRESHOLD": "0",
        },
    )

    trigger_price(engine, task, 101000)
    trigger_price(engine, task, 102000)
    trigger_price(engine, task, 103000)

    assert notifier.calls[-1]["burst_alert_detected"] is True
    assert notifier.calls[-1]["burst_alert_window_seconds"] == 300
    assert notifier.calls[-1]["burst_alert_threshold"] == 3


def test_dashboard_preview_shows_high_frequency_warning(client, make_task):
    task = make_task(base_price=100000, threshold=1000)

    client.post(f"/tasks/{task.id}/test-price", data={"current_price": "101000"})
    client.post(f"/tasks/{task.id}/test-price", data={"current_price": "102000"})
    client.post(f"/tasks/{task.id}/test-price", data={"current_price": "103000"})
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "高频报警" in body
    assert "复制通知" in body
    assert_safe_text(body)


def test_dashboard_status_and_healthz_include_burst_fields(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "高频报警监控" in body
    assert "最近高频报警" in body
    assert "重复通知跳过" in body
    assert_safe_text(body)

    health_response = client.get("/healthz")
    data = health_response.get_json()
    assert "notification_burst_enabled" in data
    assert "last_burst_alert_detected" in data
    assert "last_burst_alert_count" in data
    assert "last_burst_alert_window_seconds" in data
    assert "last_duplicate_notification_skipped" in data
    assert_safe_text(health_response.get_data(as_text=True))


def test_feishu_payload_includes_high_frequency_warning():
    http_client = FakeHttpClient()
    notifier = FeishuNotifier(
        config={
            "FEISHU_WEBHOOK_URL": "https://example.invalid/feishu-webhook",
        },
        http_client=http_client,
    )

    result = notifier.notify_alert(
        {
            "code": "CU2608",
            "direction": "UP",
            "current_price": 103000,
            "old_base_price": 102000,
            "new_base_price": 103000,
            "grid_steps": 1,
            "threshold": 1000,
            "source_label": "iFind 实时行情",
            "created_at": "2026-06-30 21:00:00",
            "burst_alert_message": "⚠️ 高频报警：最近 5 分钟内该合约已触发 3 次报警，请重点关注。",
        }
    )

    payload_text = http_client.calls[0]["json"]["content"]["text"]
    assert result.success is True
    assert "高频报警" in payload_text
    assert_safe_text(payload_text)
