from datetime import datetime, timezone

from app.db import AlertLog, db
from app.monitor.market_provider import MarketPrice
from app.monitor.status import (
    configure_auto_monitoring,
    get_monitor_status,
    pause_auto_monitoring,
    resume_auto_monitoring,
)
from app.monitor_worker import run_monitor_cycle


class SpyMarketProvider:
    auto_monitoring_enabled = True
    provider_status = "spy"

    def __init__(self, current_price=106720):
        self.current_price = current_price
        self.calls = 0

    def get_price(self, code, base_price=None, threshold=None):
        self.calls += 1
        return MarketPrice(
            code=code,
            current_price=self.current_price,
            timestamp=datetime.now(timezone.utc),
            source="spy",
            success=True,
        )


def test_auto_monitoring_disabled_by_config_skips_provider(app, make_task):
    make_task()
    provider = SpyMarketProvider()
    configure_auto_monitoring(False)

    run_monitor_cycle(app, provider, "simulated", "simulated", provider.auto_monitoring_enabled)

    assert provider.calls == 0
    assert AlertLog.query.count() == 0
    status = get_monitor_status()
    assert status["auto_monitoring_enabled"] is False
    assert status["auto_monitoring_polling_active"] is False
    assert status["auto_monitoring_paused"] is True
    assert status["last_poll_status"] == "skipped"
    assert "自动行情监控已关闭" in status["last_poll_message"]


def test_pause_route_marks_auto_monitoring_paused(client):
    response = client.post("/monitor/pause")
    status = get_monitor_status()

    assert response.status_code == 302
    assert status["auto_monitoring_paused"] is True
    assert status["auto_monitoring_enabled"] is True
    assert "用户手动暂停" in status["auto_monitoring_pause_reason"]


def test_resume_route_marks_auto_monitoring_enabled(client):
    pause_auto_monitoring()

    response = client.post("/monitor/resume")
    status = get_monitor_status()

    assert response.status_code == 302
    assert status["auto_monitoring_paused"] is False
    assert status["auto_monitoring_enabled"] is True
    assert status["auto_monitoring_config_enabled"] is True
    assert status["auto_monitoring_pause_reason"] is None


def test_paused_auto_monitoring_skips_market_provider(app, make_task):
    make_task()
    provider = SpyMarketProvider()
    configure_auto_monitoring(True)
    pause_auto_monitoring()

    run_monitor_cycle(app, provider, "ifind", "ifind", provider.auto_monitoring_enabled)

    assert provider.calls == 0
    assert AlertLog.query.count() == 0
    status = get_monitor_status()
    assert status["last_poll_status"] == "skipped"
    assert "暂停" in status["last_poll_message"]
    assert "跳过轮询" in status["last_poll_message"]


def test_manual_price_still_works_when_auto_monitoring_paused(client, make_task):
    task = make_task()
    pause_auto_monitoring()

    response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "106720"},
    )
    db.session.refresh(task)

    assert response.status_code == 302
    assert task.base_price == 106600
    assert AlertLog.query.count() == 1
    status = get_monitor_status()
    assert status["last_notification_status"] == "Dry Run 已生成"


def test_ifind_dashboard_shows_real_market_warning(app, client):
    app.config["MARKET_DATA_MODE"] = "ifind"

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前为 iFind 真实行情模式" in page
    assert "自动监控启用时会请求真实 iFind 行情" in page
    assert "access_token" not in page
    assert "refresh_token" not in page
    assert "Bearer" not in page
    assert "IFIND_REFRESH_TOKEN" not in page
    assert ".env" not in page
    assert "headers" not in page


def test_manual_only_dashboard_shows_safe_mode_message(app, client):
    app.config["MARKET_DATA_MODE"] = "manual_only"

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前为 manual_only 安全模式" in page
    assert "自动监控不会请求真实行情" in page


def test_dashboard_shows_ifind_permission_reminder(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "iFind 权限更新提醒" in page
    assert "权限更新日：每月" in page
    assert "下次权限更新日" in page
    assert "权限提醒状态" in page
    assert "access_token" not in page
    assert "refresh_token" not in page
    assert "Bearer" not in page
    assert "IFIND_REFRESH_TOKEN" not in page
    assert "IFIND_ACCESS_TOKEN" not in page
    assert ".env" not in page
    assert "headers" not in page


def test_manual_only_enabled_poll_message_is_not_disabled(app, client, make_task):
    make_task()
    configure_auto_monitoring(True)

    run_monitor_cycle(app, None, "manual_only", "manual_only", False)
    response = client.get("/")
    page = response.get_data(as_text=True)
    status = get_monitor_status()

    assert response.status_code == 200
    assert "自动行情监控已关闭" not in status["last_poll_message"]
    assert "自动行情监控已关闭" not in page
    assert "manual_only 安全模式" in status["last_poll_message"]
    assert "不请求真实行情" in status["last_poll_message"]
    assert "manual_only 安全模式" in page
    assert "不请求真实行情" in page
    assert "access_token" not in page
    assert "refresh_token" not in page
    assert "Bearer" not in page
    assert "IFIND_REFRESH_TOKEN" not in page
    assert ".env" not in page
    assert "headers" not in page


def test_healthz_contains_auto_monitoring_control_fields(client):
    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert "auto_monitoring_enabled" in data
    assert "auto_monitoring_polling_active" in data
    assert "auto_monitoring_paused" in data
    assert "auto_monitoring_pause_reason" in data
    assert "auto_monitoring_updated_at" in data
    assert "market_data_mode" in data
    assert "last_poll_status" in data
    assert "last_poll_message" in data


def test_auto_monitoring_enabled_true_dashboard_shows_enabled(client):
    configure_auto_monitoring(True)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "自动监控" in page
    assert "已启用" in page
    assert "暂停状态：未暂停" in page
    assert "暂停自动监控" in page


def test_auto_monitoring_enabled_true_healthz_reports_enabled(client):
    configure_auto_monitoring(True)

    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert data["auto_monitoring_enabled"] is True
    assert data["auto_monitoring_paused"] is False


def test_auto_monitoring_enabled_false_dashboard_shows_disabled(client):
    configure_auto_monitoring(False)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "未启用" in page
    assert "暂停状态：已暂停" in page
