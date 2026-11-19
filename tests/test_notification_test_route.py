from app.db import AlertLog, db
from app.monitor.status import get_monitor_status, record_alert_dedup_status, record_burst_alert_status


SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_FEISHU_SECRET = "YOUR_FEISHU_WEBHOOK_SECRET"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"code": 0}

    def json(self):
        return self.payload


def assert_safe_text(text: str):
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_FEISHU_SECRET not in text
    assert "FEISHU_WEBHOOK_URL" not in text
    assert "FEISHU_WEBHOOK_SECRET" not in text
    assert ".env" not in text
    assert "headers" not in text


def test_dashboard_shows_feishu_test_button(app, client):
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "测试飞书通知" in page
    assert 'action="/notifications/test"' in page
    assert_safe_text(page)


def test_dashboard_shows_dry_run_test_preview_button(app, client):
    app.config.update(NOTIFICATION_MODE="dry_run")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "预览测试通知" in page
    assert_safe_text(page)


def test_test_notification_dry_run_does_not_touch_alert_or_task(monkeypatch, app, client, make_task):
    def fail_post(*args, **kwargs):
        raise AssertionError("dry_run should not request network")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_post)
    task = make_task()
    original_base_price = task.base_price

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "测试通知预览已生成" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_test_notification_feishu_missing_webhook_is_safe(monkeypatch, app, client):
    def fail_post(*args, **kwargs):
        raise AssertionError("missing webhook should not request network")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_post)
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL="")

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "飞书 webhook 未配置" in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_test_notification_feishu_success_uses_mocked_post(monkeypatch, app, client, make_task):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("app.notify.notifier.requests.post", fake_post)
    app.config.update(
        NOTIFICATION_MODE="feishu",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        FEISHU_WEBHOOK_SECRET=SENSITIVE_FEISHU_SECRET,
    )
    task = make_task()
    original_base_price = task.base_price

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "测试通知已发送到飞书" in page
    assert len(calls) == 1
    assert calls[0]["url"] == SENSITIVE_FEISHU_URL
    assert "【网格监控测试】" in calls[0]["json"]["content"]["text"]
    assert "不会创建报警记录" in calls[0]["json"]["content"]["text"]
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_test_notification_feishu_http_failure_is_safe(monkeypatch, app, client):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse(status_code=500, payload={"code": 999})

    monkeypatch.setattr("app.notify.notifier.requests.post", fake_post)
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL)

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "测试通知发送失败" in page
    assert "HTTP 500" in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_test_notification_off_does_not_request_network(monkeypatch, app, client):
    def fail_post(*args, **kwargs):
        raise AssertionError("off mode should not request network")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_post)
    app.config.update(NOTIFICATION_MODE="off")

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "通知已关闭" in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_test_notification_wecom_placeholder_does_not_request_network(monkeypatch, app, client):
    def fail_post(*args, **kwargs):
        raise AssertionError("wecom placeholder should not request network")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_post)
    app.config.update(NOTIFICATION_MODE="wecom_placeholder")

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "企业微信真实发送尚未启用" in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_test_notification_does_not_change_dedup_or_burst_state(client):
    record_alert_dedup_status("duplicate_skipped", True)
    record_burst_alert_status(True, True, 7, 300, "高频报警")
    before = get_monitor_status()

    response = client.post("/notifications/test", follow_redirects=True)
    after = get_monitor_status()

    assert response.status_code == 200
    assert after["last_duplicate_notification_skipped"] == before["last_duplicate_notification_skipped"]
    assert after["last_burst_alert_count"] == before["last_burst_alert_count"]
    assert after["last_burst_alert_detected"] == before["last_burst_alert_detected"]
    assert AlertLog.query.count() == 0


def test_healthz_and_dashboard_layout_survive_notification_test_button(client):
    page_response = client.get("/")
    page = page_response.get_data(as_text=True)
    health_response = client.get("/healthz")
    data = health_response.get_json()

    assert page_response.status_code == 200
    assert health_response.status_code == 200
    assert "当前运行模式" in page
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert "preflight-compact-body" in page
    assert "导出 CSV" in page
    assert 'id="alert-history"' in page
    assert "展开运行诊断详情" in page
    assert "last_poll_status" in data
    assert "notification_mode" in data
    assert_safe_text(page)
    assert_safe_text(health_response.get_data(as_text=True))
