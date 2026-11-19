from app.db import MonitorTask, db
from app.monitor.status import configure_auto_monitoring, pause_auto_monitoring, resume_auto_monitoring


SENSITIVE_TERMS = [
    "access_token",
    "refresh_token",
    "Bearer",
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCESS_TOKEN",
    "FEISHU_WEBHOOK_URL",
    "FEISHU_WEBHOOK_SECRET",
    "WECOM_WEBHOOK_URL",
    ".env",
    "headers",
    "https://example.invalid/feishu-webhook",
    "YOUR_IFIND_REFRESH_TOKEN",
    "YOUR_IFIND_ACCESS_TOKEN",
    "YOUR_FEISHU_WEBHOOK_SECRET",
]


def assert_safe_text(text: str):
    for term in SENSITIVE_TERMS:
        assert term not in text


def make_enabled_task():
    task = MonitorTask(code="CU2608", base_price=105600, threshold=1000, is_active=True)
    db.session.add(task)
    db.session.commit()
    return task


def configure_live_ready(app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN="YOUR_IFIND_REFRESH_TOKEN",
        IFIND_ACCESS_TOKEN="YOUR_IFIND_ACCESS_TOKEN",
        FEISHU_WEBHOOK_URL="https://example.invalid/feishu-webhook",
        FEISHU_WEBHOOK_SECRET="YOUR_FEISHU_WEBHOOK_SECRET",
        AUTO_MONITORING_ENABLED=True,
    )
    configure_auto_monitoring(True)
    resume_auto_monitoring()
    make_enabled_task()


def test_manual_only_dashboard_shows_test_safe_mode(client, app):
    app.config.update(MARKET_DATA_MODE="manual_only")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前状态" in page
    assert "manual_only" in page
    assert "查看运行建议与检查顺序" in page
    assert_safe_text(page)


def test_simulated_dashboard_warns_not_for_live_trading(client, app):
    app.config.update(MARKET_DATA_MODE="simulated")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前状态" in page
    assert "查看运行建议与检查顺序" in page
    assert_safe_text(page)


def test_ifind_missing_refresh_token_blocks_live_mode(client, app):
    app.config.update(MARKET_DATA_MODE="ifind", IFIND_REFRESH_TOKEN="", IFIND_ACCESS_TOKEN="")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前有" in page
    assert "查看原因" in page
    assert "iFind 授权" in page
    assert "未配置" in page
    assert_safe_text(page)


def test_ifind_access_token_not_ready_blocks_live_mode(client, app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        IFIND_REFRESH_TOKEN="YOUR_IFIND_REFRESH_TOKEN",
        IFIND_ACCESS_TOKEN="",
    )

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前有" in page
    assert "查看原因" in page
    assert "iFind 访问令牌" in page
    assert "未就绪" in page
    assert_safe_text(page)


def test_auto_monitoring_disabled_blocks_live_mode(client, app):
    configure_live_ready(app)
    configure_auto_monitoring(False)

    response = client.get("/")
    page = response.get_data(as_text=True)
    data = client.get("/healthz").get_json()

    assert response.status_code == 200
    assert "当前有" in page
    assert "自动监控已关闭" in page
    assert data["live_mode_status"] == "blocked"
    assert data["is_auto_monitoring_active"] is False
    assert_safe_text(page)


def test_auto_monitoring_paused_blocks_live_mode(client, app):
    configure_live_ready(app)
    pause_auto_monitoring()

    response = client.get("/")
    page = response.get_data(as_text=True)
    data = client.get("/healthz").get_json()

    assert response.status_code == 200
    assert "当前有" in page
    assert "自动监控已暂停" in page
    assert data["live_mode_status"] == "blocked"
    assert data["is_auto_monitoring_active"] is False
    assert_safe_text(page)


def test_no_enabled_tasks_blocks_live_mode(client, app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN="YOUR_IFIND_REFRESH_TOKEN",
        IFIND_ACCESS_TOKEN="YOUR_IFIND_ACCESS_TOKEN",
        FEISHU_WEBHOOK_URL="https://example.invalid/feishu-webhook",
    )
    configure_auto_monitoring(True)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前有" in page
    assert "当前没有启用中的监控任务" in page
    assert_safe_text(page)


def test_notification_off_blocks_or_warns_live_mode(client, app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="off",
        IFIND_REFRESH_TOKEN="YOUR_IFIND_REFRESH_TOKEN",
        IFIND_ACCESS_TOKEN="YOUR_IFIND_ACCESS_TOKEN",
    )
    configure_auto_monitoring(True)
    make_enabled_task()

    response = client.get("/")
    page = response.get_data(as_text=True)
    data = client.get("/healthz").get_json()

    assert response.status_code == 200
    assert data["live_mode_status"] in {"blocked", "warning"}
    assert "通知已关闭" in page
    assert_safe_text(page)


def test_feishu_without_webhook_blocks_live_mode(client, app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN="YOUR_IFIND_REFRESH_TOKEN",
        IFIND_ACCESS_TOKEN="YOUR_IFIND_ACCESS_TOKEN",
        FEISHU_WEBHOOK_URL="",
    )
    configure_auto_monitoring(True)
    make_enabled_task()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前有" in page
    assert "飞书 webhook 未配置" in page
    assert_safe_text(page)


def test_ifind_feishu_ready_shows_ready_or_warning(client, app):
    configure_live_ready(app)

    response = client.get("/")
    page = response.get_data(as_text=True)
    data = client.get("/healthz").get_json()

    assert response.status_code == 200
    assert data["live_mode_status"] in {"ready", "warning"}
    assert "当前状态" in page
    assert "当前有" not in page or "项警告" in page
    assert data["is_live_market_enabled"] is True
    assert data["is_real_notification_enabled"] is True
    assert data["is_auto_monitoring_active"] is True
    assert_safe_text(page)


def test_live_mode_config_guidance_is_collapsed_and_healthz_has_fields(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    health_response = client.get("/healthz")
    data = health_response.get_json()

    assert response.status_code == 200
    assert "当前状态" in page
    assert "runtime-overview-panel" in page
    assert "查看运行建议与检查顺序" in page
    assert "正式运行配置建议" in page
    assert "正式盯盘推荐配置" in page
    assert "live-config-title" in page
    assert "live-config-list" in page
    assert "live-config-note" in page
    assert "行情模式：" in page
    assert "自动监控：" in page
    assert "通知模式：" in page
    assert "合约后缀：" in page
    assert "实时行情指标：" in page
    assert "<details class=\"diagnostic-details runtime-details status-detail\">" in page
    assert "live-config-details" in page
    for field in [
        "live_mode_status",
        "live_mode_title",
        "live_mode_message",
        "live_mode_suggestions",
        "is_live_market_enabled",
        "is_real_notification_enabled",
        "is_auto_monitoring_active",
        "live_mode_blockers_count",
        "live_mode_warnings_count",
        "preflight_overall_status",
        "preflight_checks",
        "last_poll_status",
        "notification_mode",
        "market_data_mode",
        "worker_started",
    ]:
        assert field in data
    assert_safe_text(page)
    assert_safe_text(health_response.get_data(as_text=True))


def test_dashboard_hierarchy_remains_after_live_mode_panel(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.index("runtime-overview-panel") < page.index("dashboard-primary-tasks")
    assert page.index("查看运行建议与检查顺序") < page.index("dashboard-primary-tasks")
    assert page.index("dashboard-primary-tasks") < page.index("preflight-panel")
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert 'id="alert-history"' in page
    assert "导出 CSV" in page
    assert "preflight-details" in page
    assert "展开运行诊断详情" in page
    assert_safe_text(page)
