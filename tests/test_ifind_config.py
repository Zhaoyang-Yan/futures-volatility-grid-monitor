from app import create_app
from app.monitor.ifind_permission import get_ifind_permission_status
from app.monitor.preflight import get_preflight_checks
from app.monitor.status import get_runtime_diagnostics
from app.notify.notifier import get_notification_channel_status


SENSITIVE_REFRESH_TOKEN = "YOUR_IFIND_REFRESH_TOKEN"
SENSITIVE_ACCESS_TOKEN = "YOUR_IFIND_ACCESS_TOKEN"


def _preflight_for(app):
    return get_preflight_checks(
        app.config,
        get_runtime_diagnostics(),
        get_ifind_permission_status(config=app.config),
        get_notification_channel_status(app.config["NOTIFICATION_MODE"], app.config),
        enabled_tasks_count=0,
    )


def test_create_app_maps_ifind_environment_variables_to_config(monkeypatch):
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", SENSITIVE_REFRESH_TOKEN)
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", SENSITIVE_ACCESS_TOKEN)
    monkeypatch.setenv("IFIND_USERNAME", "ifind-user")
    monkeypatch.setenv("IFIND_PASSWORD", "ifind-password")
    monkeypatch.setenv("IFIND_ACCOUNT_EXPIRE_DATE", "2026-12-31")
    monkeypatch.setenv("IFIND_TOKEN_RENEW_DAY", "22")
    monkeypatch.setenv("IFIND_BATCH_SIZE", "7")

    app = create_app("testing")

    assert app.config["IFIND_REFRESH_TOKEN"] == SENSITIVE_REFRESH_TOKEN
    assert app.config["IFIND_ACCESS_TOKEN"] == SENSITIVE_ACCESS_TOKEN
    assert app.config["IFIND_USERNAME"] == "ifind-user"
    assert app.config["IFIND_PASSWORD"] == "ifind-password"
    assert app.config["IFIND_ACCOUNT_EXPIRE_DATE"] == "2026-12-31"
    assert app.config["IFIND_TOKEN_RENEW_DAY"] == "22"
    assert app.config["IFIND_BATCH_SIZE"] == 7


def test_refresh_token_without_access_token_is_waiting_not_refresh_missing(app):
    app.config.update(
        MARKET_DATA_MODE="ifind",
        IFIND_REFRESH_TOKEN=SENSITIVE_REFRESH_TOKEN,
        IFIND_ACCESS_TOKEN="",
    )

    preflight = _preflight_for(app)
    checks = {check["key"]: check for check in preflight["checks"]}
    text = str(preflight)

    assert checks["ifind_refresh_ready"]["status"] == "pass"
    assert checks["ifind_access_ready"]["status"] == "warning"
    assert "尚未生成" in checks["ifind_access_ready"]["message"]
    assert "iFind refresh token 未配置" not in text
    assert SENSITIVE_REFRESH_TOKEN not in text
    assert SENSITIVE_ACCESS_TOKEN not in text
