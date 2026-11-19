from pathlib import Path

from app.db import AlertLog, db
from app.monitor.status import configure_auto_monitoring, update_monitor_status


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
]


def assert_safe_text(text: str):
    for term in SENSITIVE_TERMS:
        assert term not in text


def test_task_list_is_before_preflight_and_diagnostics(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.index("runtime-overview-panel") < page.index("dashboard-primary-tasks")
    assert page.index("dashboard-primary-tasks") < page.index("preflight-panel")
    assert page.index("dashboard-primary-tasks") < page.index("runtime-diagnostics-panel")
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert "当前基准价" in page
    assert "网格间距" in page
    assert "下沿价格" in page
    assert "上沿价格" in page
    assert_safe_text(page)


def test_task_actions_are_compact_without_losing_routes(client, make_task):
    task = make_task()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "查看报警" in page
    assert "编辑" in page
    assert "测试价格" in page
    assert "测试 iFind 行情" in page
    assert "删除" in page
    assert "task-action-menu" in page
    assert "data-task-action-menu" in page
    assert "<summary>测试</summary>" in page
    task_menu = page.split('<div class="task-action-menu-content">', 1)[1].split("</div>", 1)[0]
    assert "测试价格" in task_menu
    assert "测试 iFind 行情" in task_menu
    assert "测试飞书通知" not in task_menu
    assert 'action="/notifications/test"' not in task_menu
    assert f'action="/tasks/{task.id}/test-ifind-quote"' in page
    assert 'action="/notifications/test"' in page
    assert f'action="/tasks/{task.id}/delete"' in page
    assert f'href="/?contract_code={task.code}#alert-history"' in page
    assert "导出 CSV" in page
    assert 'id="alert-history"' in page
    assert "预览测试通知" in page or "测试飞书通知" in page
    assert_safe_text(page)


def test_recent_alert_card_uses_real_alert_log_semantics(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "最近报警" in page
    assert "暂无网格报警" in page
    assert "合约 暂无" in page
    assert "no_alert" not in page
    assert "null" not in page
    assert "undefined" not in page
    assert "none" not in page
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_notification_test_flash_does_not_become_recent_alert(client, make_task):
    task = make_task()
    original_base_price = task.base_price

    response = client.post("/notifications/test", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "测试通知预览已生成" in page
    assert "最近报警" in page
    assert "暂无网格报警" in page
    assert "no_alert" not in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_recent_alert_card_shows_latest_real_grid_alert(client, make_task):
    task = make_task(code="CU2608")
    db.session.add(
        AlertLog(
            task_id=task.id,
            code=task.code,
            old_base_price=105600,
            base_price=106600,
            new_base_price=106600,
            current_price=106720,
            diff=1120,
            threshold=1000,
            grid_steps=1,
            direction="UP",
            message="CU2608 向上突破 1 个网格",
        )
    )
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "最近报警" in page
    assert "向上突破" in page
    assert "合约 CU2608" in page
    assert "no_alert" not in page
    assert_safe_text(page)


def test_dashboard_final_ui_keeps_core_sections_and_compact_styles(client, make_task):
    task = make_task(code="CU2608.SHF")
    response = client.get("/")
    page = response.get_data(as_text=True)
    css = Path("app/static/css/app.css").read_text()
    js = Path("app/static/js/app.js").read_text()
    health = client.get("/healthz").get_json()

    assert response.status_code == 200
    assert "dashboard-shell" in page
    assert "dashboard-header" in page
    assert "网格监控中心" in page
    assert "Grid Trading Alert System & Diagnostic Tool" in page
    assert "北京时间刷新辅助" not in page
    assert "data-dashboard-clock" in page
    assert 'id="dashboardBeijingClock"' in page
    assert "北京时间（GMT+8）" in page
    assert "initDashboardClock" in js
    assert "formatBeijingDateTime" in js
    assert 'timeZone: "Asia/Shanghai"' in js
    assert "当前行情源" in page
    assert "market-mode-strip" not in page
    assert "当前状态" in page
    assert "最近行情轮询" in page
    assert "上次刷新" in page
    assert "立即刷新" in page
    assert "自动刷新" in page
    assert "dashboard-primary-tasks" in page
    assert "dashboard-card-grid" in page
    assert "summary-card" in page
    assert "summary-card-icon" not in page
    assert 'aria-hidden="true">监</span>' not in page
    assert 'aria-hidden="true">警</span>' not in page
    assert 'aria-hidden="true">数</span>' not in page
    assert 'aria-hidden="true">诊</span>' not in page
    assert "status-pill" in page
    assert "dashboard-tabs" in page
    assert "监控大盘总览" in page
    assert "历史报警日志" in page
    assert "系统状态与诊断" in page
    assert "正在监测" not in page
    assert "监测合约" not in page
    assert "monitoring-contracts-strip" not in page
    assert "monitoring-contract-chip" not in page
    assert "CU2608.SHF" in page
    assert "监控任务列表" in page
    assert "行情状态" in page
    assert "等待轮询" in page
    assert "task-updated-at" in page
    assert "task-market-error" in page or "等待轮询" in page
    assert "查看报警" in page
    assert "编辑" in page
    assert "<summary>测试</summary>" in page
    assert "测试 iFind 行情" in page
    assert "测试飞书通知" in page or "预览测试通知" in page
    assert "删除" in page
    assert "报警历史" in page
    assert "导出 CSV" in page
    assert page.count("导出 CSV") == 1
    assert "filter-card" in page
    assert "alert-filter-actions" in page
    assert "table-card" in page
    assert "alert-card" in page
    assert "action-button" in page
    assert "notification-preview-dialog" in page or "暂无符合条件的报警记录" in page
    assert "完整 iFind 代码" in page
    assert "CU2608.SHF、Y2609.DCE、LC2609.GFE" in page
    assert "不要填写无后缀代码，例如 CU2608" in page
    assert "例如 CU2608 或 CU2608.SHF" not in page
    assert ".dashboard-shell" in css
    assert ".dashboard-header" in css
    assert ".dashboard-card-grid" in css
    assert ".summary-card" in css
    assert ".summary-card-icon" not in css
    assert ".status-pill" in css
    assert ".dashboard-clock-card" in css
    assert ".dashboard-tabs" in css
    assert ".monitoring-contracts-strip" not in css
    assert ".monitoring-contract-chip" not in css
    assert ".action-button" in css
    assert ".filter-card" in css
    assert ".alert-filter-actions" in css
    assert ".alert-actions-header" in css
    assert ".alert-direction-badge" in css
    assert ".alert-diff-positive" in css
    assert ".alert-diff-negative" in css
    assert ".grid-step-badge" in css
    assert ".task-code-help" in css
    assert ".task-actions .btn" in css
    assert "tailwind" not in css.lower()
    assert "lucide" not in css.lower()
    assert "motion" not in css.lower()
    assert page.index("runtime-overview-panel") < page.index("dashboard-primary-tasks")
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert "last_poll_status" in health
    assert AlertLog.query.count() == 0
    db.session.refresh(task)
    assert task.base_price == 105600
    assert_safe_text(page)


def test_dashboard_declares_inline_favicon_and_keeps_core_page_safe(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    health_response = client.get("/healthz")

    assert response.status_code == 200
    assert 'rel="icon"' in page
    assert "data:image/svg+xml" in page
    assert "%E7%BD%91" in page
    assert "当前行情源" in page
    assert "运行状态" in page
    assert "dashboard-primary-tasks" in page
    assert "报警历史" in page
    assert "导出 CSV" in page
    assert "通知预览" in page or "暂无符合条件的报警记录" in page
    assert health_response.status_code == 200
    assert AlertLog.query.count() == 0
    assert_safe_text(page)


def test_readme_documents_public_showcase_flow_without_real_secrets():
    readme = Path("README.md").read_text()

    for term in [
        "Futures Grid Monitor & Volatility Diagnostics",
        "manual_only",
        "dry_run",
        "Volatility cones",
        "Dynamic Grid Rebase",
        "demo_showcase/screenshots/01_market_overview.png",
        "MARKET_DATA_MODE=manual_only NOTIFICATION_MODE=dry_run",
        "pytest -q",
    ]:
        assert term in readme

    for forbidden in ["example.invalid/feishu-webhook", "YOUR_IFIND_REFRESH_TOKEN", "YOUR_FEISHU_WEBHOOK_URL"]:
        assert forbidden not in readme

def test_version_snapshot_documents_local_stable_release():
    changelog = Path("CHANGELOG.md").read_text()
    version = Path("VERSION").read_text().strip()

    assert version == "v0.1.0-local-stable"
    assert "v0.1.0-local-stable" in changelog
    assert "本地可运行稳定版" in changelog
    assert "支持 iFind 行情模式" in changelog
    assert "支持飞书通知" in changelog
    assert "支持动态网格更新" in changelog
    assert "支持报警历史与 CSV 导出" in changelog
    assert "pytest warnings 已清理" in changelog
    assert "iFind + Feishu 真实模式长时间观察 30-60 分钟" in changelog
    assert "真实突破时 AlertLog / base_price / Feishu 三者一致性" in changelog
    assert "xoxb-" not in changelog
    assert "Bearer " not in changelog
    assert "https://example.invalid/feishu-webhook" not in changelog


def test_task_action_menu_uses_floating_content_and_single_open_script():
    css = Path("app/static/css/app.css").read_text()
    js = Path("app/static/js/app.js").read_text()

    assert ".dashboard-primary-tasks" in css
    assert ".dashboard-primary-tasks .table-responsive" in css
    assert "overflow: visible" in css
    assert ".task-actions" in css
    assert ".task-action-menu-content" in css
    menu_content_css = css.split(".task-action-menu-content", 1)[1].split(".task-action-menu-item", 1)[0]
    assert "position: fixed" in menu_content_css
    assert "z-index: 4000" in menu_content_css
    assert ".task-action-menu.is-dropup" in css
    assert "data-task-action-menu" in Path("app/templates/dashboard/index.html").read_text()
    assert "details.task-action-menu" in js
    assert "otherMenu.open = false" in js
    assert 'event.target.closest("details.task-action-menu")' in js
    assert "menu.open = false" in js
    assert "positionTaskActionMenu" in js
    assert "resetTaskActionMenuPosition" in js
    assert "shouldDropUp" in js
    assert "window.addEventListener(\"resize\"" in js
    assert "React" not in js
    assert "Vue" not in js


def test_alert_history_table_uses_two_level_details_and_preview_styles():
    css = Path("app/static/css/app.css").read_text()
    js = Path("app/static/js/app.js").read_text()

    assert ".alert-history-table td" in css
    assert ".alert-details-row" in css
    assert ".alert-detail-grid" in css
    assert ".alert-detail-toggle-btn" in css
    assert ".alert-preview-open-btn" in css
    assert ".notification-preview-dialog" in css
    assert ".notification-preview-dialog-footer" in css
    assert "padding: 1rem 1.25rem 1.25rem" in css
    assert ".alert-actions-cell" in css
    assert ".alert-preview-open-btn" in js
    assert ".alert-detail-toggle-btn" in js
    assert "showModal" in js
    assert ".notification-preview-close-btn" in js
    assert "React" not in js
    assert "Vue" not in js


def test_compact_status_summary_is_visible(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前状态" in page
    assert "飞书投递" in page
    assert "行情" in page
    assert "通知" in page
    assert "自动监控" in page
    assert "阻塞" in page
    assert "警告" in page
    assert "待发送" in page
    assert "最终失败" in page
    assert "查看诊断" in page
    assert "status-summary-layout" not in page
    assert_safe_text(page)


def test_runtime_controls_and_preflight_are_compact_and_collapsed(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "实时盘前演练" not in page
    assert "真实盯盘前演练" not in page
    assert ">blocked<" not in page
    assert "runtime-overview-line" not in page
    assert "runtime-overview-meta" not in page
    assert "轮询与交易时段设置" in page
    assert "查看运行建议与检查顺序" in page
    assert "<details class=\"diagnostic-details runtime-details status-detail\">" in page
    assert "正式运行配置建议" in page
    assert "检查顺序" in page
    assert "live-config-details" in page
    assert "rehearsal-details" in page
    assert "<details class=\"diagnostic-details preflight-details\">" in page
    assert page.index("runtime-overview-panel") < page.index("dashboard-primary-tasks")
    assert_safe_text(page)


def test_dashboard_status_and_alert_filters_use_compact_layout(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    css = Path("app/static/css/app.css").read_text()

    assert response.status_code == 200
    assert "compact-status-bar" in page
    assert "compact-status-summary" in page
    assert "compact-status-actions" in page
    assert "aria-label=\"监控操作\"" in page
    assert "status-summary-layout" not in page
    assert "实时盘前演练" not in page
    assert ">blocked<" not in page
    assert "查看原因" in page
    assert "alert-history-header" in page
    assert "alert-clear-history-form" in page
    assert page.count("清空全部历史") == 1
    assert 'onsubmit="return confirm(' in page
    assert "alert-filter-fields" in page
    assert "alert-filter-actions" in page
    assert "alert-filter-summary" in page
    assert page.index("alert-filter-fields") < page.index("alert-filter-actions")
    assert page.index("alert-filter-actions") < page.index("alert-filter-summary")
    assert ">筛选</button>" in page
    assert "清除筛选" in page
    assert "导出 CSV" in page
    assert ".compact-status-bar" in css
    assert "grid-template-columns: minmax(0, 1fr) auto" in css
    assert ".compact-status-summary" in css
    assert ".compact-status-blocker" in css
    assert ".compact-blocker-details" in css
    assert ".compact-blocker-reasons" in css
    assert "position: static" in css
    assert "overflow: visible" in css
    assert "@media (max-width: 1200px)" in css
    assert "grid-template-columns: 1fr" in css
    assert ".alert-filter-fields" in css
    assert ".alert-filter-summary" in css
    assert ".alert-clear-history-form" in css
    assert "min-height: 36px" in css
    assert "overflow-wrap: anywhere" in css
    assert_safe_text(page)


def test_compact_status_blocker_detail_is_full_width_and_not_clipped(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    css = Path("app/static/css/app.css").read_text()

    assert response.status_code == 200
    assert "compact-status-blocker" in page
    assert "<details class=\"compact-blocker-details\">" in page
    assert "compact-blocker-reasons" in page
    assert "<span>当前有" in page
    assert "<span class=\"compact-blocker-link\">查看原因</span>" in page
    assert "grid-column: 1 / -1" in css
    assert "width: 100%" in css
    assert "max-width: 100%" in css
    assert "position: absolute" not in css[css.index(".compact-blocker-reasons") : css.index(".dashboard-refresh-controls")]
    assert "max-height" not in css[css.index(".compact-blocker-reasons") : css.index(".dashboard-refresh-controls")]
    assert "overflow: hidden" not in css[css.index(".compact-status") : css.index(".runtime-overview-panel")]
    assert_safe_text(page)


def test_ifind_auto_monitoring_uses_running_copy_not_rehearsal_key(app, client, make_task):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN="layout-refresh-token",
        FEISHU_WEBHOOK_URL="https://example.invalid/feishu-webhook",
    )
    configure_auto_monitoring(True)
    update_monitor_status(
        market_data_mode="ifind",
        auto_monitoring_enabled=True,
        auto_monitoring_paused=False,
        ifind_has_refresh_token=True,
        notification_mode="feishu",
    )
    task = make_task()
    original_base_price = task.base_price

    response = client.get("/")
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "运行中" in page
    assert "short_rehearsal" not in page
    assert "真实盯盘前演练" not in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(page)


def test_dashboard_has_lightweight_refresh_controls(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    js = Path("app/static/js/app.js").read_text()
    css = Path("app/static/css/app.css").read_text()

    assert response.status_code == 200
    assert "上次刷新" in page
    assert "立即刷新" in page
    assert "自动刷新" in page
    assert "当前轮询间隔" in page
    assert "更新间隔" in page
    assert "更新轮询间隔" not in page
    assert 'action="/settings/monitor-poll-interval"' in page
    assert "下次刷新" in page
    assert "dashboard-refresh-controls" in page
    assert 'data-auto-refresh-interval="60"' in page
    assert 'id="dashboardLastRefresh"' in page
    assert 'id="dashboardRefreshNow"' in page
    assert 'id="dashboardAutoRefreshToggle"' in page
    assert 'id="dashboardAutoRefreshCountdown"' in page
    assert "initDashboardRefreshControls" in js
    assert "gridMonitorDashboardAutoRefresh" in js
    assert "refreshDashboardWithoutHash" in js
    assert "dashboardAutoRefreshScrollKey" in js
    assert "restoreDashboardScrollAfterRefresh" in js
    assert "window.location.replace(refreshUrl" in js
    assert "window.location.reload()" not in js
    assert "window.location.hash" in js
    assert "document.hidden" in js
    assert ".notification-preview-dialog[open]" in js
    assert "details.task-action-menu[open]" in js
    assert ".dashboard-refresh-controls" in css
    assert "当前状态" in page
    assert "dashboard-primary-tasks" in page
    assert "测试 iFind 行情" in page
    assert "预览测试通知" in page or "测试飞书通知" in page
    assert "notification-preview-dialog" in page or "暂无符合条件的报警记录" in page
    assert "导出 CSV" in page
    assert_safe_text(page)


def test_preflight_shows_issue_summary_and_collapsed_full_table(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "运行前检查" in page
    assert "preflight-compact-body" in page
    assert "preflight-issue-line" in page
    assert "通过：<span" in page
    assert "警告：<span" in page
    assert "阻塞：<span" in page
    assert ">阻塞</span>" in page
    assert ">警告</span>" in page
    assert "展开全部检查" in page
    assert "<details class=\"diagnostic-details preflight-details\">" in page
    assert "类别" in page
    assert "检查项" in page
    assert_safe_text(page)


def test_alert_history_before_collapsed_runtime_diagnostics(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.index("报警历史") < page.index("runtime-diagnostics-panel")
    assert "展开运行诊断详情" in page
    assert "自动监控诊断" in page
    assert "行情源诊断" in page
    assert "通知诊断" in page
    assert_safe_text(page)


def test_notification_preview_and_copy_button_remain(client, make_task):
    up_task = make_task(code="CU2608", base_price=105600, threshold=1000)
    down_task = make_task(code="RB2608", base_price=105600, threshold=1000)
    client.post(f"/tasks/{up_task.id}/test-price", data={"current_price": "106720"})
    client.post(f"/tasks/{down_task.id}/test-price", data={"current_price": "104480"})
    db.session.expire_all()
    alert = AlertLog.query.first()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert alert is not None
    assert "通知预览" in page
    assert "复制通知" in page
    assert "notificationPreview" in page
    assert "alert-direction-badge" in page
    assert "↗ 上涨" in page
    assert "↘ 下跌" in page
    assert "alert-direction-up" in page
    assert "alert-direction-down" in page
    assert "alert-diff-positive" in page
    assert "alert-diff-negative" in page
    assert ".alert-diff-neutral" in Path("app/static/css/app.css").read_text()
    assert "grid-step-badge" in page
    assert_safe_text(page)


def test_healthz_keeps_preflight_and_diagnostic_fields(client):
    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert "preflight_overall_status" in data
    assert "preflight_checks" in data
    assert "last_poll_status" in data
    assert "monitor_poll_interval_seconds" in data
    assert "last_market_request_status" in data
    assert "last_alert_status" in data
    assert "last_notification_delivery_status" in data
    assert "last_burst_alert_detected" in data
    assert_safe_text(response.get_data(as_text=True))
