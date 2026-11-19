from types import SimpleNamespace

from app.db import AlertLog, db
from app.monitor.rehearsal import get_rehearsal_status
from app.monitor.status import configure_auto_monitoring, get_runtime_diagnostics
from app.notify.notifier import get_notification_channel_status


SENSITIVE_FEISHU_URL = "https://example.invalid/feishu-webhook"
SENSITIVE_REFRESH_TOKEN = "YOUR_IFIND_REFRESH_TOKEN"
SENSITIVE_ACCESS_TOKEN = "YOUR_IFIND_ACCESS_TOKEN"


def assert_safe_text(text: str):
    assert SENSITIVE_FEISHU_URL not in text
    assert SENSITIVE_REFRESH_TOKEN not in text
    assert SENSITIVE_ACCESS_TOKEN not in text
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text
    assert "Authorization" not in text
    assert "headers" not in text
    assert ".env" not in text


def rehearsal_for_app(app, enabled_tasks=None):
    channel_status = get_notification_channel_status(app.config["NOTIFICATION_MODE"], app.config)
    return get_rehearsal_status(
        app.config,
        get_runtime_diagnostics({"notification_mode": app.config["NOTIFICATION_MODE"]}),
        enabled_tasks or [],
        channel_status,
    )


def test_manual_only_blocks_rehearsal_without_network(monkeypatch, app):
    def fail_request(*args, **kwargs):
        raise AssertionError("rehearsal must not request external services")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_request)
    app.config.update(MARKET_DATA_MODE="manual_only", NOTIFICATION_MODE="dry_run")

    status = rehearsal_for_app(app)

    assert status["status_key"] == "blocked"
    assert "真实行情演练" in status["title"]
    assert any("不是 ifind" in blocker for blocker in status["blockers"])
    assert_safe_text(str(status))


def test_non_feishu_notification_blocks_rehearsal(app):
    app.config.update(MARKET_DATA_MODE="ifind", NOTIFICATION_MODE="off")

    status = rehearsal_for_app(app)

    assert status["status_key"] == "blocked"
    assert any("通知模式不是 feishu" in blocker for blocker in status["blockers"])
    assert_safe_text(str(status))


def test_no_enabled_tasks_blocks_rehearsal(app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_ACCESS_TOKEN=SENSITIVE_ACCESS_TOKEN,
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
    )

    status = rehearsal_for_app(app)

    assert status["status_key"] == "blocked"
    assert any("没有启用" in blocker for blocker in status["blockers"])
    assert_safe_text(str(status))


def test_missing_base_price_blocks_rehearsal(app):
    app.config.update(MARKET_DATA_MODE="ifind", NOTIFICATION_MODE="feishu", IFIND_ACCESS_TOKEN="x", FEISHU_WEBHOOK_URL="x")
    task = SimpleNamespace(code="CU2608", base_price=None, threshold=1000)

    status = rehearsal_for_app(app, [task])

    assert status["status_key"] == "blocked"
    assert any("基准价缺失" in blocker for blocker in status["blockers"])


def test_invalid_grid_size_blocks_rehearsal(app):
    app.config.update(MARKET_DATA_MODE="ifind", NOTIFICATION_MODE="feishu", IFIND_ACCESS_TOKEN="x", FEISHU_WEBHOOK_URL="x")
    task = SimpleNamespace(code="CU2608", base_price=105600, threshold=0)

    status = rehearsal_for_app(app, [task])

    assert status["status_key"] == "blocked"
    assert any("网格间距无效" in blocker for blocker in status["blockers"])


def test_ifind_feishu_complete_task_allows_short_rehearsal_without_side_effects(app, make_task):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        NOTIFICATION_MODE="feishu",
        IFIND_REFRESH_TOKEN=SENSITIVE_REFRESH_TOKEN,
        IFIND_ACCESS_TOKEN=SENSITIVE_ACCESS_TOKEN,
        FEISHU_WEBHOOK_URL=SENSITIVE_FEISHU_URL,
    )
    configure_auto_monitoring(True)
    task = make_task()
    original_base_price = task.base_price

    status = rehearsal_for_app(app, [task])

    db.session.refresh(task)
    assert status["status_key"] in {"short_rehearsal", "near_live"}
    assert status["blockers"] == []
    assert "短时" in status["title"] or "正式盯盘" in status["title"]
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
    assert_safe_text(str(status))


def test_dashboard_shows_compact_runtime_controls(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前状态" in page
    assert "runtime-overview-panel" in page
    assert "轮询与交易时段设置" in page
    assert "阻塞" in page
    assert "查看运行建议与检查顺序" in page
    assert "检查顺序" in page
    assert "实时盘前演练" not in page
    assert "真实盯盘前演练" not in page
    assert "rehearsal-details" in page
    assert "测试 iFind 行情" in page
    assert "预览测试通知" in page or "测试飞书通知" in page
    assert "导出 CSV" in page
    assert "notification-preview-dialog" in page or "暂无符合条件的报警记录" in page
    assert 'id="alert-history"' in page
    assert_safe_text(page)


def test_healthz_contains_rehearsal_fields(client):
    response = client.get("/healthz")
    data = response.get_json()
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "rehearsal_status" in data
    assert "rehearsal_title" in data
    assert "rehearsal_blockers_count" in data
    assert "rehearsal_warnings_count" in data
    assert "rehearsal_suggestions" in data
    assert "preflight_overall_status" in data
    assert "last_poll_status" in data
    assert_safe_text(text)
