from datetime import datetime, timezone

from app.db import AlertLog, db
from app.monitor.market_provider import MarketPrice
from app.monitor.status import get_monitor_status, record_alert_dedup_status, record_burst_alert_status


SENSITIVE_ACCESS_TOKEN = "YOUR_IFIND_ACCESS_TOKEN"
SENSITIVE_REFRESH_TOKEN = "YOUR_IFIND_REFRESH_TOKEN"
SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_SECRET = "YOUR_SECRET"


class StaticProvider:
    def __init__(self, price=None, exc=None):
        self.price = price
        self.exc = exc
        self.calls = []

    def get_price(self, code, base_price=None, threshold=None):
        self.calls.append({"code": code, "base_price": base_price, "threshold": threshold})
        if self.exc:
            raise self.exc
        return self.price


def assert_safe_text(text: str):
    assert SENSITIVE_ACCESS_TOKEN not in text
    assert SENSITIVE_REFRESH_TOKEN not in text
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_SECRET not in text
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text
    assert "Authorization" not in text
    assert ".env" not in text
    assert "headers" not in text


def test_task_row_shows_ifind_quote_test_button(client, make_task):
    task = make_task(code="CU2608")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "测试 iFind 行情" in page
    assert f'action="/tasks/{task.id}/test-ifind-quote"' in page
    assert_safe_text(page)


def test_ifind_quote_task_not_found_returns_404(client):
    response = client.post("/tasks/999/test-ifind-quote")

    assert response.status_code == 404


def test_manual_only_does_not_request_ifind_or_mutate(monkeypatch, app, client, make_task):
    def fail_factory(*args, **kwargs):
        raise AssertionError("manual_only should not create provider")

    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", fail_factory)
    app.config.update(MARKET_DATA_MODE="manual_only")
    task = make_task()
    original_base_price = task.base_price

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "当前行情模式为 manual_only" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_ifind_quote_success_uses_provider_without_alert_or_notification(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=78650,
            timestamp=datetime(2026, 6, 30, 8, 45, 0, tzinfo=timezone.utc),
            source="ifind",
            success=True,
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
        IFIND_ACCESS_TOKEN=SENSITIVE_ACCESS_TOKEN,
        IFIND_REFRESH_TOKEN=SENSITIVE_REFRESH_TOKEN,
    )
    task = make_task()
    original_base_price = task.base_price
    before_status = get_monitor_status()

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)
    after_status = get_monitor_status()

    db.session.refresh(task)
    assert response.status_code == 200
    assert "iFind 行情测试成功：CU2608 latest=78650" in page
    assert provider.calls == [{"code": "CU2608", "base_price": None, "threshold": None}]
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert after_status["last_notification_status"] == before_status["last_notification_status"]
    assert_safe_text(page)


def test_ifind_quote_success_displays_resolved_code(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="Y2609",
            current_price=7865,
            timestamp=None,
            source="ifind",
            success=True,
            raw={"ifind_code": "Y2609.DCE"},
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind")
    task = make_task(code="Y2609")
    original_base_price = task.base_price

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "iFind 行情测试成功：Y2609 → Y2609.DCE latest=7865" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_ifind_quote_empty_price_is_safe(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=None,
            timestamp=None,
            source="ifind",
            success=False,
            error_message="iFind 实时行情返回中未找到 latest 价格",
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind")
    task = make_task()
    original_base_price = task.base_price

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "iFind 行情测试失败" in page
    assert "未找到 latest" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_ifind_quote_failure_displays_contract_and_resolved_code(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="Y2609",
            current_price=None,
            timestamp=None,
            source="ifind",
            success=False,
            error_message="接口返回参数错误",
            raw={"ifind_code": "Y2609.SHF"},
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind")
    task = make_task(code="Y2609")
    original_base_price = task.base_price

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "iFind 行情测试失败：Y2609 → Y2609.SHF" in page
    assert "接口返回参数错误" in page
    assert "IFIND_CODE_MAP" in page
    assert "IFIND_EXCHANGE_SUFFIX_MAP" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_ifind_quote_exception_is_safe(monkeypatch, app, client, make_task):
    provider = StaticProvider(exc=RuntimeError(f"Authorization failed {SENSITIVE_ACCESS_TOKEN}"))
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind", IFIND_ACCESS_TOKEN=SENSITIVE_ACCESS_TOKEN)
    task = make_task()
    original_base_price = task.base_price

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "iFind 行情测试失败" in page
    assert "[已隐藏]" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_ifind_quote_missing_token_is_safe(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=None,
            timestamp=None,
            source="ifind",
            success=False,
            error_message="iFind access token 未配置或已失效",
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind", IFIND_REFRESH_TOKEN=SENSITIVE_REFRESH_TOKEN)
    task = make_task()

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "iFind 行情测试失败" in page
    assert "token 未配置" in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_ifind_quote_does_not_change_dedup_or_burst(monkeypatch, app, client, make_task):
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=78650,
            timestamp=None,
            source="ifind",
            success=True,
        )
    )
    monkeypatch.setattr("app.routes.dashboard.get_market_data_provider", lambda mode, config=None: provider)
    app.config.update(MARKET_DATA_MODE="ifind")
    task = make_task()
    record_alert_dedup_status("duplicate_skipped", True)
    record_burst_alert_status(True, True, 9, 300, "高频报警")
    before_status = get_monitor_status()

    response = client.post(f"/tasks/{task.id}/test-ifind-quote", follow_redirects=True)
    after_status = get_monitor_status()

    assert response.status_code == 200
    assert after_status["last_duplicate_notification_skipped"] == before_status["last_duplicate_notification_skipped"]
    assert after_status["last_burst_alert_count"] == before_status["last_burst_alert_count"]
    assert after_status["last_burst_alert_detected"] == before_status["last_burst_alert_detected"]
    assert AlertLog.query.count() == 0


def test_ifind_quote_keeps_healthz_and_dashboard_layout(client, make_task):
    make_task()

    page_response = client.get("/")
    page = page_response.get_data(as_text=True)
    health_response = client.get("/healthz")
    data = health_response.get_json()

    assert page_response.status_code == 200
    assert health_response.status_code == 200
    assert "当前运行模式" in page
    assert "测试飞书通知" in page or "预览测试通知" in page
    assert "测试 iFind 行情" in page
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert "preflight-compact-body" in page
    assert "导出 CSV" in page
    assert 'id="alert-history"' in page
    assert "last_poll_status" in data
    assert_safe_text(page)
    assert_safe_text(health_response.get_data(as_text=True))
