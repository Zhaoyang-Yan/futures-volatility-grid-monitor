from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.market_provider import MarketPrice
from app.monitor.status import (
    configure_auto_monitoring,
    get_runtime_diagnostics,
    record_market_request,
    reset_monitor_status,
)
from app.notify.notifier import FeishuNotifier


SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_FEISHU_SECRET = "YOUR_FEISHU_WEBHOOK_SECRET"
SENSITIVE_TERMS = [
    "access_token",
    "Bearer",
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCESS_TOKEN",
    SENSITIVE_FEISHU_URL,
    SENSITIVE_FEISHU_SECRET,
    "FEISHU_WEBHOOK_URL",
    "FEISHU_WEBHOOK_SECRET",
    "WECOM_WEBHOOK_URL",
    ".env",
    "headers",
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"code": 0}

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class CountingNotifier:
    mode = "counting_test"

    def __init__(self):
        self.calls = []

    def notify_alert(self, alert_context):
        self.calls.append(alert_context)


class StaticMarketProvider:
    provider_status = "ifind"
    auto_monitoring_enabled = True

    def __init__(self, price):
        self.price = price

    def get_price(self, code, base_price=None, threshold=None):
        return self.price


class ScriptedMarketProvider:
    provider_status = "ifind"
    auto_monitoring_enabled = True

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_price(self, code, base_price=None, threshold=None):
        self.calls.append(code)
        response = self.responses[code]
        if isinstance(response, Exception):
            raise response
        return response


class BatchMarketProvider:
    provider_status = "ifind"
    auto_monitoring_enabled = True
    batch_enabled = True

    def __init__(self, batch_responses=None, single_responses=None, batch_exc=None):
        self.batch_responses = batch_responses or {}
        self.single_responses = single_responses or {}
        self.batch_exc = batch_exc
        self.batch_calls = []
        self.single_calls = []

    def get_prices(self, codes):
        self.batch_calls.append(list(codes))
        if self.batch_exc:
            raise self.batch_exc
        return {code: self.batch_responses[code] for code in codes if code in self.batch_responses}

    def get_price(self, code, base_price=None, threshold=None):
        self.single_calls.append(code)
        response = self.single_responses[code]
        if isinstance(response, Exception):
            raise response
        return response


def market_price(code, price, success=True, error_message=None):
    return MarketPrice(
        code=code,
        current_price=price if success else None,
        timestamp=None,
        source="ifind",
        success=success,
        error_message=error_message,
        raw={"ifind_code": code},
    )


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
    for term in SENSITIVE_TERMS:
        assert term not in text


def task_row(page: str, code: str) -> str:
    task_section = page.split("dashboard-primary-tasks", 1)[1].split("</section>", 1)[0]
    for row in task_section.split("<tr"):
        if code in row:
            return "<tr" + row.split("</tr>", 1)[0] + "</tr>"
    return ""


def test_dashboard_manual_only_runtime_diagnostics(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "运行诊断" in body
    assert "自动监控诊断" in body
    assert "行情源诊断" in body
    assert "iFind 诊断" in body
    assert "报警诊断" in body
    assert "通知诊断" in body
    assert "高频报警诊断" in body
    assert "manual_only 安全模式" in body
    assert "不请求真实行情" in body
    assert "自动监控：已启用" in body
    assert "轮询状态：已跳过" in body
    assert "iFind refresh token 未配置" in body or "Refresh Token：未配置" in body
    assert_safe_text(body)


def test_healthz_contains_unified_runtime_diagnostics(client):
    response = client.get("/healthz")
    data = response.get_json()
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    for key in [
        "last_poll_status",
        "last_poll_message",
        "last_poll_duration_ms",
        "last_market_request_status",
        "last_market_request_message",
        "last_market_error",
        "market_last_attempt_at",
        "market_last_success_at",
        "market_last_failure_at",
        "market_last_contract_code",
        "market_last_resolved_code",
        "market_last_price",
        "market_last_source",
        "market_last_error_summary",
        "market_consecutive_failures",
        "market_poll_status_by_contract",
        "market_poll_success_count",
        "market_poll_failure_count",
        "market_poll_waiting_count",
        "ifind_batch_size",
        "market_poll_batch_enabled",
        "last_alert_status",
        "last_alert_message",
        "last_notification_delivery_status",
        "last_burst_alert_detected",
    ]:
        assert key in data
    for removed_key in [
        "market_data_freshness_status",
        "market_data_freshness_title",
        "market_data_stale_seconds",
        "market_data_age_seconds",
    ]:
        assert removed_key not in data
    assert data["ifind_last_error"] == "iFind refresh token 未配置"
    assert_safe_text(body)


def test_empty_runtime_diagnostics_are_safe(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    diagnostics = get_runtime_diagnostics()

    assert response.status_code == 200
    assert diagnostics["last_alert_status"] == "暂无"
    assert diagnostics["last_market_request_status"] == "暂无"
    assert "暂无" in body
    assert_safe_text(body)


def test_market_poll_success_updates_runtime_diagnostics(make_task, client):
    reset_monitor_status()
    configure_auto_monitoring(True)
    client.application.config.update(MARKET_DATA_MODE="ifind")
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    provider = StaticMarketProvider(
        MarketPrice(
            code="CU2608.SHF",
            current_price=105900,
            timestamp=None,
            source="ifind",
            success=True,
            raw={"ifind_code": "CU2608.SHF"},
        )
    )

    result = MonitoringEngine(provider).run_once()
    diagnostics = get_runtime_diagnostics()
    page = client.get("/").get_data(as_text=True)
    health = client.get("/healthz").get_json()

    assert result.skipped_count == 0
    assert diagnostics["last_market_attempt_at"] is not None
    assert diagnostics["last_market_success_at"] is not None
    assert diagnostics["last_market_failure_at"] is None
    assert diagnostics["last_market_contract_code"] == "CU2608.SHF"
    assert diagnostics["last_market_resolved_code"] == "CU2608.SHF"
    assert diagnostics["last_market_price"] == 105900
    assert diagnostics["last_market_source"] == "ifind"
    assert diagnostics["consecutive_market_failures"] == 0
    assert diagnostics["market_poll_status_by_contract"]["CU2608.SHF"]["success"] is True
    assert diagnostics["market_poll_status_by_contract"]["CU2608.SHF"]["consecutive_failures"] == 0
    assert "最近行情轮询" in page
    assert "行情正常" in page
    assert "全部正常" in page
    assert "CU2608.SHF" in page
    assert health["market_last_contract_code"] == "CU2608.SHF"
    assert health["market_last_resolved_code"] == "CU2608.SHF"
    assert health["market_last_price"] == 105900
    assert health["market_last_source"] == "ifind"
    assert health["market_consecutive_failures"] == 0
    assert health["market_poll_status_by_contract"]["CU2608.SHF"]["success"] is True
    assert health["market_poll_success_count"] == 1
    assert health["market_poll_failure_count"] == 0
    assert health["market_poll_waiting_count"] == 0
    assert "行情更新正常" not in page
    assert "行情可能停滞" not in page
    assert "尚无成功行情" not in page
    assert_safe_text(page)


def test_market_poll_failure_updates_runtime_diagnostics_safely(make_task, client):
    reset_monitor_status()
    configure_auto_monitoring(True)
    client.application.config.update(MARKET_DATA_MODE="ifind")
    make_task(code="Y2609", base_price=8000, threshold=100)
    provider = StaticMarketProvider(
        MarketPrice(
            code="Y2609",
            current_price=None,
            timestamp=None,
            source="ifind",
            success=False,
            error_message="接口参数错误 access_token Bearer Authorization headers webhook secret .env",
            raw={"ifind_code": "Y2609.DCE"},
        )
    )

    result = MonitoringEngine(provider).run_once()
    diagnostics = get_runtime_diagnostics()
    page = client.get("/").get_data(as_text=True)
    health_body = client.get("/healthz").get_data(as_text=True)

    assert result.skipped_count == 1
    assert diagnostics["last_market_attempt_at"] is not None
    assert diagnostics["last_market_success_at"] is None
    assert diagnostics["last_market_failure_at"] is not None
    assert diagnostics["last_market_contract_code"] == "Y2609"
    assert diagnostics["last_market_resolved_code"] == "Y2609.DCE"
    assert diagnostics["last_market_price"] is None
    assert diagnostics["last_market_error_summary"] is not None
    for term in ["access_token", "Bearer", "Authorization", "headers", "webhook", "secret", ".env"]:
        assert term not in diagnostics["last_market_error_summary"]
    assert diagnostics["consecutive_market_failures"] == 1
    assert diagnostics["market_poll_status_by_contract"]["Y2609"]["success"] is False
    assert diagnostics["market_poll_status_by_contract"]["Y2609"]["consecutive_failures"] == 1
    assert diagnostics["market_poll_status_by_contract"]["Y2609"]["error_category"] == "unknown_error"
    assert "最近行情轮询" in page
    assert "行情失败" in page
    assert "全部失败" in page
    assert "Y2609" in page
    assert "Y2609.DCE" in page
    assert "连续失败：1" in page
    assert "market_last_failure_at" in health_body
    assert "market_last_error_summary" in health_body
    assert "market_poll_status_by_contract" in health_body
    assert "market_data_freshness_status" not in health_body
    assert "行情更新正常" not in page
    assert "行情可能停滞" not in page
    assert "尚无成功行情" not in page
    assert_safe_text(page)
    assert_safe_text(health_body)


def test_market_poll_failure_isolated_by_contract_and_dashboard_shows_status(monkeypatch, make_task, client):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    reset_monitor_status()
    configure_auto_monitoring(True)
    client.application.config.update(MARKET_DATA_MODE="ifind")
    cu_task = make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    y_task = make_task(code="Y2609.DCE", base_price=8000, threshold=100)
    lc_task = make_task(code="LC2609.GFE", base_price=9000, threshold=100)
    provider = ScriptedMarketProvider(
        {
            "CU2608.SHF": MarketPrice(
                code="CU2608.SHF",
                current_price=105900,
                timestamp=None,
                source="ifind",
                success=True,
                raw={"ifind_code": "CU2608.SHF"},
            ),
            "Y2609.DCE": RuntimeError("Permission denied by DCE security"),
            "LC2609.GFE": MarketPrice(
                code="LC2609.GFE",
                current_price=9200,
                timestamp=None,
                source="ifind",
                success=True,
                raw={"ifind_code": "LC2609.GFE"},
            ),
        }
    )

    result = MonitoringEngine(provider, notification_mode="dry_run").run_once()
    diagnostics = get_runtime_diagnostics()
    page = client.get("/").get_data(as_text=True)
    health = client.get("/healthz").get_json()

    db.session.refresh(cu_task)
    db.session.refresh(y_task)
    db.session.refresh(lc_task)
    assert provider.calls == ["CU2608.SHF", "Y2609.DCE", "LC2609.GFE"]
    assert result.checked_task_count == 3
    assert result.skipped_count == 1
    assert diagnostics["market_poll_status_by_contract"]["CU2608.SHF"]["success"] is True
    assert diagnostics["market_poll_status_by_contract"]["Y2609.DCE"]["success"] is False
    assert diagnostics["market_poll_status_by_contract"]["Y2609.DCE"]["error_category"] == "permission_denied"
    assert diagnostics["market_poll_status_by_contract"]["LC2609.GFE"]["success"] is True
    assert "部分失败" in page
    assert "成功 2 / 失败 1 / 等待 0" in page
    assert "行情正常" in page
    assert "行情失败" in page
    assert "权限被拒绝" in page
    assert health["market_poll_success_count"] == 2
    assert health["market_poll_failure_count"] == 1
    assert health["market_poll_waiting_count"] == 0
    assert AlertLog.query.count() == 1
    assert len(notifier.calls) == 1
    assert cu_task.base_price == 105600
    assert y_task.base_price == 8000
    assert lc_task.base_price == 9200
    assert y_task.is_active is True
    assert_safe_text(page)


def test_market_poll_failure_does_not_create_alert_or_notify(monkeypatch, make_task):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    reset_monitor_status()
    task = make_task(code="Y2609.DCE", base_price=8000, threshold=100)
    provider = ScriptedMarketProvider({"Y2609.DCE": RuntimeError("Permission denied by DCE security")})

    result = MonitoringEngine(provider, notification_mode="dry_run").run_once()

    db.session.refresh(task)
    assert result.skipped_count == 1
    assert AlertLog.query.count() == 0
    assert len(notifier.calls) == 0
    assert task.base_price == 8000
    assert task.is_active is True


def test_market_poll_failure_recovers_to_success(make_task):
    reset_monitor_status()
    task = make_task(code="Y2609.DCE", base_price=8000, threshold=100)
    failing_provider = ScriptedMarketProvider({"Y2609.DCE": RuntimeError("Permission denied by DCE security")})

    first = MonitoringEngine(failing_provider, notification_mode="off").run_once()
    first_status = get_runtime_diagnostics()["market_poll_status_by_contract"]["Y2609.DCE"]

    success_provider = ScriptedMarketProvider(
        {
            "Y2609.DCE": MarketPrice(
                code="Y2609.DCE",
                current_price=8050,
                timestamp=None,
                source="ifind",
                success=True,
                raw={"ifind_code": "Y2609.DCE"},
            )
        }
    )
    second = MonitoringEngine(success_provider, notification_mode="off").run_once()
    second_status = get_runtime_diagnostics()["market_poll_status_by_contract"]["Y2609.DCE"]

    db.session.refresh(task)
    assert first.skipped_count == 1
    assert first_status["success"] is False
    assert first_status["consecutive_failures"] == 1
    assert second.skipped_count == 0
    assert second_status["success"] is True
    assert second_status["consecutive_failures"] == 0
    assert AlertLog.query.count() == 0
    assert task.base_price == 8000


def test_batch_market_poll_success_uses_get_prices_and_checks_each_task(make_task):
    reset_monitor_status()
    cu_task = make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    lc_task = make_task(code="LC2609.GFE", base_price=9000, threshold=100)
    nr_task = make_task(code="NR2608.INE", base_price=12000, threshold=100)
    provider = BatchMarketProvider(
        batch_responses={
            "CU2608.SHF": market_price("CU2608.SHF", 106700),
            "LC2609.GFE": market_price("LC2609.GFE", 9200),
            "NR2608.INE": market_price("NR2608.INE", 12120),
        }
    )

    result = MonitoringEngine(provider, notification_mode="off", notification_config={"IFIND_BATCH_SIZE": 5}).run_once()

    db.session.refresh(cu_task)
    db.session.refresh(lc_task)
    db.session.refresh(nr_task)
    assert provider.batch_calls == [["CU2608.SHF", "LC2609.GFE", "NR2608.INE"]]
    assert provider.single_calls == []
    assert result.alert_count == 3
    assert AlertLog.query.count() == 3
    assert cu_task.base_price == 106600
    assert lc_task.base_price == 9200
    assert nr_task.base_price == 12100


def test_batch_failure_falls_back_to_single_contracts_and_isolates_failures(monkeypatch, make_task):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    reset_monitor_status()
    cu_task = make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    y_task = make_task(code="Y2609.DCE", base_price=8000, threshold=100)
    lc_task = make_task(code="LC2609.GFE", base_price=9000, threshold=100)
    provider = BatchMarketProvider(
        batch_exc=RuntimeError("iFind batch HTTP 502"),
        single_responses={
            "CU2608.SHF": market_price("CU2608.SHF", 105900),
            "Y2609.DCE": RuntimeError("Permission denied by DCE security"),
            "LC2609.GFE": market_price("LC2609.GFE", 9200),
        },
    )

    result = MonitoringEngine(provider, notification_mode="dry_run").run_once()
    diagnostics = get_runtime_diagnostics()

    db.session.refresh(cu_task)
    db.session.refresh(y_task)
    db.session.refresh(lc_task)
    assert provider.batch_calls == [["CU2608.SHF", "Y2609.DCE", "LC2609.GFE"]]
    assert provider.single_calls == ["CU2608.SHF", "Y2609.DCE", "LC2609.GFE"]
    assert result.skipped_count == 1
    assert AlertLog.query.count() == 1
    assert len(notifier.calls) == 1
    assert cu_task.base_price == 105600
    assert y_task.base_price == 8000
    assert lc_task.base_price == 9200
    assert y_task.is_active is True
    assert diagnostics["market_poll_status_by_contract"]["Y2609.DCE"]["success"] is False


def test_batch_partial_missing_falls_back_for_missing_contract(make_task):
    reset_monitor_status()
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    lc_task = make_task(code="LC2609.GFE", base_price=9000, threshold=100)
    provider = BatchMarketProvider(
        batch_responses={"CU2608.SHF": market_price("CU2608.SHF", 105900)},
        single_responses={"LC2609.GFE": market_price("LC2609.GFE", 9200)},
    )

    result = MonitoringEngine(provider, notification_mode="off").run_once()

    db.session.refresh(lc_task)
    assert provider.batch_calls == [["CU2608.SHF", "LC2609.GFE"]]
    assert provider.single_calls == ["LC2609.GFE"]
    assert result.skipped_count == 0
    assert AlertLog.query.count() == 1
    assert lc_task.base_price == 9200


def test_same_contract_is_requested_once_per_round_but_not_cached_across_rounds(make_task):
    reset_monitor_status()
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000)
    provider = BatchMarketProvider(batch_responses={"CU2608.SHF": market_price("CU2608.SHF", 106700)})

    first = MonitoringEngine(provider, notification_mode="off").run_once()
    second = MonitoringEngine(provider, notification_mode="off").run_once()

    assert provider.batch_calls == [["CU2608.SHF"], ["CU2608.SHF"]]
    assert provider.single_calls == []
    assert first.checked_task_count == 2
    assert second.checked_task_count == 2


def test_ifind_batch_size_splits_batches_and_invalid_value_falls_back(make_task):
    reset_monitor_status()
    responses = {}
    for index in range(12):
        code = f"T{index:02d}.SHF"
        make_task(code=code, base_price=1000, threshold=100)
        responses[code] = market_price(code, 1001)
    provider = BatchMarketProvider(batch_responses=responses)

    MonitoringEngine(provider, notification_mode="off", notification_config={"IFIND_BATCH_SIZE": 5}).run_once()
    assert [len(batch) for batch in provider.batch_calls] == [5, 5, 2]

    reset_monitor_status()
    provider_invalid = BatchMarketProvider(batch_responses=responses)
    MonitoringEngine(provider_invalid, notification_mode="off", notification_config={"IFIND_BATCH_SIZE": "bad"}).run_once()
    assert [len(batch) for batch in provider_invalid.batch_calls] == [5, 5, 2]


def test_disabled_task_is_not_requested_and_does_not_show_stale_failure(make_task, client):
    reset_monitor_status()
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000, is_active=True)
    disabled_task = make_task(code="CF2609.CZC", base_price=12000, threshold=100, is_active=False)
    record_market_request(
        provider="ifind",
        status="failed",
        message="iFind 参数错误",
        error_message="CF2609.CZC 参数错误",
        contract_code="CF2609.CZC",
        resolved_code="CF2609.CZC",
    )
    provider = BatchMarketProvider(batch_responses={"CU2608.SHF": market_price("CU2608.SHF", 105900)})

    result = MonitoringEngine(provider, notification_mode="off").run_once()
    page = client.get("/").get_data(as_text=True)
    row = task_row(page, "CF2609.CZC")

    db.session.refresh(disabled_task)
    assert provider.batch_calls == [["CU2608.SHF"]]
    assert "CF2609.CZC" not in provider.batch_calls[0]
    assert result.enabled_task_count == 1
    assert disabled_task.base_price == 12000
    assert disabled_task.is_active is False
    assert "已停用" in row
    assert "不参与行情轮询" in row
    assert "行情失败" not in row
    assert "参数错误" not in row
    assert "连续失败" not in row
    assert AlertLog.query.count() == 0


def test_disabled_task_with_stale_success_shows_disabled_not_old_price(make_task, client):
    reset_monitor_status()
    disabled_task = make_task(code="CU2608.SHF", base_price=105600, threshold=1000, is_active=False)
    record_market_request(
        provider="ifind",
        status="success",
        message="行情获取成功",
        contract_code="CU2608.SHF",
        resolved_code="CU2608.SHF",
        price=106700,
    )

    page = client.get("/").get_data(as_text=True)
    row = task_row(page, "CU2608.SHF")

    db.session.refresh(disabled_task)
    assert disabled_task.is_active is False
    assert "已停用" in row
    assert "不参与行情轮询" in row
    assert "行情正常" not in row
    assert "价格 106700.00" not in row
    assert AlertLog.query.count() == 0


def test_disabled_task_stale_status_does_not_affect_market_summary(make_task, client):
    reset_monitor_status()
    make_task(code="CU2608.SHF", base_price=105600, threshold=1000, is_active=True)
    make_task(code="CF2609.CZC", base_price=12000, threshold=100, is_active=False)
    record_market_request(
        provider="ifind",
        status="success",
        message="行情获取成功",
        contract_code="CU2608.SHF",
        resolved_code="CU2608.SHF",
        price=105900,
    )
    record_market_request(
        provider="ifind",
        status="failed",
        message="iFind 参数错误",
        error_message="CF2609.CZC 参数错误",
        contract_code="CF2609.CZC",
        resolved_code="CF2609.CZC",
    )

    page = client.get("/").get_data(as_text=True)
    health = client.get("/healthz").get_json()

    assert "全部正常" in page
    assert "成功 1 / 失败 0 / 等待 0" in page
    assert health["market_poll_success_count"] == 1
    assert health["market_poll_failure_count"] == 0
    assert health["market_poll_waiting_count"] == 0
    assert AlertLog.query.count() == 0


def test_reenabled_task_waits_for_next_poll_before_showing_market_status(make_task, client):
    reset_monitor_status()
    task = make_task(code="CF2609.CZC", base_price=12000, threshold=100, is_active=True)
    record_market_request(
        provider="ifind",
        status="failed",
        message="iFind 参数错误",
        error_message="CF2609.CZC 参数错误",
        contract_code="CF2609.CZC",
        resolved_code="CF2609.CZC",
    )
    task.is_active = False
    db.session.commit()
    task.is_active = True
    db.session.commit()

    page_before_poll = client.get("/").get_data(as_text=True)
    row_before_poll = task_row(page_before_poll, "CF2609.CZC")

    assert "等待轮询" in row_before_poll
    assert "行情失败" not in row_before_poll
    assert "参数错误" not in row_before_poll

    provider = BatchMarketProvider(batch_responses={"CF2609.CZC": market_price("CF2609.CZC", 12020)})
    result = MonitoringEngine(provider, notification_mode="off").run_once()
    page_after_poll = client.get("/").get_data(as_text=True)
    row_after_poll = task_row(page_after_poll, "CF2609.CZC")

    db.session.refresh(task)
    assert provider.batch_calls == [["CF2609.CZC"]]
    assert result.skipped_count == 0
    assert "行情正常" in row_after_poll
    assert "价格 12020.00" in row_after_poll
    assert AlertLog.query.count() == 0
    assert task.base_price == 12000
    assert task.is_active is True


def test_consecutive_failures_do_not_skip_or_backoff(make_task):
    reset_monitor_status()
    task = make_task(code="CF2609.CZC", base_price=12000, threshold=100)
    provider = BatchMarketProvider(
        batch_exc=RuntimeError("batch failed"),
        single_responses={"CF2609.CZC": RuntimeError("iFind 实时行情请求超时")},
    )

    for _ in range(5):
        MonitoringEngine(provider, notification_mode="off").run_once()

    db.session.refresh(task)
    status = get_runtime_diagnostics()["market_poll_status_by_contract"]["CF2609.CZC"]
    assert provider.batch_calls == [["CF2609.CZC"]] * 5
    assert provider.single_calls == ["CF2609.CZC"] * 5
    assert status["consecutive_failures"] == 5
    assert AlertLog.query.count() == 0
    assert task.base_price == 12000
    assert task.is_active is True


def test_feishu_success_diagnostics_show_sent(app, client):
    http_client = FakeHttpClient()
    notifier = FeishuNotifier(
        config={"FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL},
        http_client=http_client,
    )

    result = notifier.notify_alert(alert_context())
    response = client.get("/")
    body = response.get_data(as_text=True)
    health = client.get("/healthz").get_json()

    assert result.success is True
    assert result.delivery_status == "sent"
    assert http_client.calls
    assert "sent" in body or "飞书通知发送成功" in body
    assert health["last_notification_success"] is True
    assert health["last_notification_delivery_status"] == "sent"
    assert health["last_notification_duration_ms"] is not None
    assert_safe_text(body)


def test_feishu_failure_diagnostics_are_safe(app, client):
    http_client = FakeHttpClient(FakeResponse(status_code=500, payload={"code": 999}))
    notifier = FeishuNotifier(
        config={
            "FEISHU_WEBHOOK_URL": SENSITIVE_FEISHU_URL,
            "FEISHU_WEBHOOK_SECRET": SENSITIVE_FEISHU_SECRET,
        },
        http_client=http_client,
    )

    result = notifier.notify_alert(alert_context())
    response = client.get("/")
    body = response.get_data(as_text=True)
    health_body = client.get("/healthz").get_data(as_text=True)

    assert result.success is False
    assert result.delivery_status == "http_error"
    assert "HTTP 500" in body
    assert "HTTP 500" in health_body
    assert_safe_text(body)
    assert_safe_text(health_body)


def test_duplicate_skipped_diagnostics_and_no_duplicate_notification(monkeypatch, make_task, client):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    task = make_task(base_price=103600, threshold=1000)
    engine = MonitoringEngine(notification_mode="dry_run")

    engine.process_task_price(task, 105800, source="manual_test")
    db.session.commit()
    task.base_price = 103600
    engine.process_task_price(task, 105820, source="manual_test")
    db.session.commit()

    response = client.get("/")
    body = response.get_data(as_text=True)
    health = client.get("/healthz").get_json()

    assert AlertLog.query.count() == 1
    assert len(notifier.calls) == 1
    assert "duplicate_skipped" in body
    assert "重复报警已跳过" in body
    assert health["last_duplicate_notification_skipped"] is True
    assert health["last_alert_status"] == "duplicate_skipped"
    assert_safe_text(body)


def test_high_frequency_diagnostics_show_detected(monkeypatch, make_task, client):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    task = make_task(base_price=100000, threshold=1000)
    engine = MonitoringEngine(
        notification_mode="dry_run",
        notification_config={
            "NOTIFICATION_BURST_ENABLED": "true",
            "NOTIFICATION_BURST_WINDOW_SECONDS": "300",
            "NOTIFICATION_BURST_THRESHOLD": "3",
        },
    )

    for price in [101000, 102000, 103000]:
        engine.process_task_price(task, price, source="manual_test")
        db.session.commit()
        db.session.refresh(task)

    response = client.get("/")
    body = response.get_data(as_text=True)
    health = client.get("/healthz").get_json()

    assert len(notifier.calls) == 3
    assert "高频报警诊断" in body
    assert "最近高频报警：已触发" in body
    assert "高频报警" in body
    assert health["last_burst_alert_detected"] is True
    assert health["last_burst_alert_count"] == 3
    assert_safe_text(body)
