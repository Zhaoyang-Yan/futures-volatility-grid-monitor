from pathlib import Path

from app.monitor.ifind_token_manager import IfindTokenManager


SENSITIVE_REFRESH_TOKEN = "YOUR_IFIND_REFRESH_TOKEN"
YOUR_IFIND_REFRESH_TOKEN = "old-sensitive-refresh-token-value"


def enable_auth(app, env_path: Path):
    app.config.update(
        DASHBOARD_AUTH_ENABLED=True,
        DASHBOARD_USERNAME="admin",
        DASHBOARD_PASSWORD="correct-password",
        ENV_FILE_PATH=env_path,
        IFIND_REFRESH_TOKEN=YOUR_IFIND_REFRESH_TOKEN,
        IFIND_ACCOUNT_EXPIRE_DATE="2026-08-01",
        IFIND_TOKEN_RENEW_DAY="22",
    )


def login(client):
    return client.post("/login", data={"username": "admin", "password": "correct-password"})


def test_dashboard_ifind_auth_settings_do_not_render_refresh_token(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"IFIND_REFRESH_TOKEN={SENSITIVE_REFRESH_TOKEN}\n", encoding="utf-8")
    enable_auth(app, env_path)
    app.config["IFIND_REFRESH_TOKEN"] = SENSITIVE_REFRESH_TOKEN
    login(client)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "iFind 授权设置" in page
    assert "iFind refresh token" in page
    assert "已配置" in page
    assert "保存授权设置" in page
    assert SENSITIVE_REFRESH_TOKEN not in page
    assert "IFIND_REFRESH_TOKEN" not in page
    assert "refresh_token" not in page
    assert ".env" not in page


def test_update_ifind_auth_settings_updates_env_and_creates_backup(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "MARKET_DATA_MODE=ifind",
                f"IFIND_REFRESH_TOKEN={YOUR_IFIND_REFRESH_TOKEN}",
                "IFIND_ACCOUNT_EXPIRE_DATE=2026-08-01",
                "IFIND_TOKEN_RENEW_DAY=22",
                "",
            ]
        ),
        encoding="utf-8",
    )
    enable_auth(app, env_path)
    login(client)

    response = client.post(
        "/settings/ifind-auth",
        data={
            "ifind_refresh_value": SENSITIVE_REFRESH_TOKEN,
            "ifind_account_expire_date": "2026-09-30",
            "ifind_token_renew_day": "25",
            "dashboard_password": "correct-password",
        },
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)
    env_text = env_path.read_text(encoding="utf-8")
    backups = list((tmp_path / "backups").glob("env_before_ifind_update_*"))

    assert response.status_code == 200
    assert "iFind 授权设置已保存，请测试 iFind 行情确认授权。" in page
    assert f"IFIND_REFRESH_TOKEN={SENSITIVE_REFRESH_TOKEN}" in env_text
    assert "IFIND_ACCOUNT_EXPIRE_DATE=2026-09-30" in env_text
    assert "IFIND_TOKEN_RENEW_DAY=25" in env_text
    assert backups
    assert YOUR_IFIND_REFRESH_TOKEN in backups[0].read_text(encoding="utf-8")
    assert app.config["IFIND_REFRESH_TOKEN"] == SENSITIVE_REFRESH_TOKEN
    assert app.config["IFIND_ACCESS_TOKEN"] == ""
    assert SENSITIVE_REFRESH_TOKEN not in page


def test_update_ifind_auth_settings_appends_missing_keys(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("MARKET_DATA_MODE=ifind\n", encoding="utf-8")
    enable_auth(app, env_path)
    login(client)

    client.post(
        "/settings/ifind-auth",
        data={
            "ifind_refresh_value": SENSITIVE_REFRESH_TOKEN,
            "ifind_account_expire_date": "2026-10-31",
            "ifind_token_renew_day": "26",
            "dashboard_password": "correct-password",
        },
    )
    env_text = env_path.read_text(encoding="utf-8")

    assert f"IFIND_REFRESH_TOKEN={SENSITIVE_REFRESH_TOKEN}" in env_text
    assert "IFIND_ACCOUNT_EXPIRE_DATE=2026-10-31" in env_text
    assert "IFIND_TOKEN_RENEW_DAY=26" in env_text


def test_update_ifind_auth_settings_rejects_wrong_password(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"IFIND_REFRESH_TOKEN={YOUR_IFIND_REFRESH_TOKEN}\n", encoding="utf-8")
    enable_auth(app, env_path)
    login(client)

    response = client.post(
        "/settings/ifind-auth",
        data={
            "ifind_refresh_value": SENSITIVE_REFRESH_TOKEN,
            "dashboard_password": "wrong-password",
        },
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)
    env_text = env_path.read_text(encoding="utf-8")

    assert "Dashboard 密码确认失败" in page
    assert YOUR_IFIND_REFRESH_TOKEN in env_text
    assert SENSITIVE_REFRESH_TOKEN not in env_text
    assert SENSITIVE_REFRESH_TOKEN not in page


def test_unauthenticated_ifind_auth_update_is_blocked(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"IFIND_REFRESH_TOKEN={YOUR_IFIND_REFRESH_TOKEN}\n", encoding="utf-8")
    enable_auth(app, env_path)

    response = client.post(
        "/settings/ifind-auth",
        data={
            "ifind_refresh_value": SENSITIVE_REFRESH_TOKEN,
            "dashboard_password": "correct-password",
        },
    )
    env_text = env_path.read_text(encoding="utf-8")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert YOUR_IFIND_REFRESH_TOKEN in env_text
    assert SENSITIVE_REFRESH_TOKEN not in env_text


def test_update_ifind_auth_settings_resets_existing_token_manager(app, client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"IFIND_REFRESH_TOKEN={YOUR_IFIND_REFRESH_TOKEN}\n", encoding="utf-8")
    enable_auth(app, env_path)
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        refresh_token=YOUR_IFIND_REFRESH_TOKEN,
        access_token="cached-access-token",
    )
    login(client)

    client.post(
        "/settings/ifind-auth",
        data={
            "ifind_refresh_value": SENSITIVE_REFRESH_TOKEN,
            "dashboard_password": "correct-password",
        },
    )
    status = manager.get_status()

    assert manager.refresh_token == SENSITIVE_REFRESH_TOKEN
    assert status.has_access_token is False
    assert status.has_refresh_token is True
