from datetime import datetime, timezone
from pathlib import Path

from app.config import positive_int
from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.market_provider import MarketPrice


def enable_auth(app):
    app.config.update(
        DASHBOARD_AUTH_ENABLED=True,
        DASHBOARD_USERNAME="admin",
        DASHBOARD_PASSWORD="correct-password",
    )


def test_auth_disabled_by_default_keeps_dashboard_accessible(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "网格监控中心" in response.get_data(as_text=True)


def test_auth_enabled_redirects_dashboard_to_login(app, client):
    enable_auth(app)

    response = client.get("/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_is_available_when_auth_enabled(app, client):
    enable_auth(app)

    response = client.get("/login")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "网格监控中心" in page
    assert "用户名" in page
    assert "密码" in page
    assert "记住登录状态" in page
    assert 'name="remember_me"' in page
    assert "公共设备请勿勾选" in page
    assert "登录" in page
    assert "correct-password" not in page


def test_wrong_credentials_do_not_login(app, client):
    enable_auth(app)

    response = client.post(
        "/login",
        data={"username": "admin", "password": "wrong-password"},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "用户名或密码错误" in page
    assert "dashboard-primary-tasks" not in page


def test_missing_credentials_refuse_login(app, client):
    app.config.update(DASHBOARD_AUTH_ENABLED=True, DASHBOARD_USERNAME="", DASHBOARD_PASSWORD="")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "anything"},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "登录配置未完成" in page


def test_correct_credentials_login_and_logout(app, client):
    enable_auth(app)

    login_response = client.post(
        "/login",
        data={"username": "admin", "password": "correct-password"},
        follow_redirects=True,
    )
    logged_in_page = login_response.get_data(as_text=True)

    assert login_response.status_code == 200
    assert "dashboard-primary-tasks" in logged_in_page
    assert "退出登录" in logged_in_page

    logout_response = client.get("/logout", follow_redirects=True)
    logout_page = logout_response.get_data(as_text=True)

    assert logout_response.status_code == 200
    assert "已退出登录" in logout_page
    assert "登录" in logout_page

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 302
    assert "/login" in dashboard_response.headers["Location"]


def test_login_without_remember_me_uses_browser_session(app, client):
    enable_auth(app)

    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 302
    with client.session_transaction() as stored_session:
        assert stored_session.get("dashboard_authenticated") is True
        assert stored_session.permanent is False


def test_login_with_remember_me_uses_permanent_session(app, client):
    enable_auth(app)

    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct-password", "remember_me": "on"},
    )

    assert response.status_code == 302
    with client.session_transaction() as stored_session:
        assert stored_session.get("dashboard_authenticated") is True
        assert stored_session.permanent is True


def test_remember_me_days_default_and_invalid_value_fallback(app):
    assert app.config["DASHBOARD_REMEMBER_ME_DAYS"] == 7
    assert app.permanent_session_lifetime.days == 7
    assert positive_int("bad", 7) == 7
    assert positive_int("0", 7) == 7
    assert positive_int("-3", 7) == 7
    assert positive_int("14", 7) == 14


def test_dashboard_logout_button_is_aligned_with_header_actions(app, client):
    enable_auth(app)
    client.post("/login", data={"username": "admin", "password": "correct-password"})

    response = client.get("/")
    page = response.get_data(as_text=True)
    css = Path("app/static/css/app.css").read_text()

    assert response.status_code == 200
    assert "dashboard-logout-button" in page
    assert "退出登录" in page
    assert ".dashboard-header .action-button" in css
    header_button_css = css.split(".dashboard-header .action-button", 1)[1].split(".dashboard-logout-button", 1)[0]
    assert "display: inline-flex" in header_button_css
    assert "align-items: center" in header_button_css
    assert "justify-content: center" in header_button_css
    assert "min-height: 50px" in header_button_css


def test_unauthenticated_post_does_not_execute_notification_or_mutate_task(monkeypatch, app, client, make_task):
    def fail_post(*args, **kwargs):
        raise AssertionError("unauthenticated request must not send notification")

    monkeypatch.setattr("app.notify.notifier.requests.post", fail_post)
    enable_auth(app)
    app.config.update(NOTIFICATION_MODE="feishu", FEISHU_WEBHOOK_URL="https://example.invalid/webhook")
    task = make_task()
    original_base_price = task.base_price

    notification_response = client.post("/notifications/test")
    price_response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "106720"},
    )

    db.session.refresh(task)
    assert notification_response.status_code == 302
    assert "/login" in notification_response.headers["Location"]
    assert price_response.status_code == 302
    assert "/login" in price_response.headers["Location"]
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price


def test_auth_does_not_block_monitoring_engine_internal_execution(app, make_task):
    class StaticProvider:
        provider_status = "test_provider"

        def get_price(self, code, base_price=None, threshold=None):
            return MarketPrice(
                code=code,
                current_price=106720,
                timestamp=datetime.now(timezone.utc),
                source="test_provider",
                success=True,
            )

    enable_auth(app)
    task = make_task()

    result = MonitoringEngine(
        StaticProvider(),
        notification_mode="off",
        notification_config=app.config,
    ).run_once()

    db.session.refresh(task)
    assert result.alert_count == 1
    assert AlertLog.query.count() == 1
    assert task.base_price == 106600


def test_login_required_for_healthz_and_csv_export(app, client):
    enable_auth(app)

    health_response = client.get("/healthz")
    export_response = client.get("/alerts/export.csv")

    assert health_response.status_code == 302
    assert "/login" in health_response.headers["Location"]
    assert export_response.status_code == 302
    assert "/login" in export_response.headers["Location"]


def test_unauthenticated_clear_alerts_is_blocked_and_preserves_data(app, client, make_task):
    enable_auth(app)
    task = make_task()
    original_base_price = task.base_price
    db.session.add(
        AlertLog(
            task_id=task.id,
            code=task.code,
            base_price=task.base_price,
            old_base_price=task.base_price,
            new_base_price=task.base_price + task.threshold,
            current_price=task.base_price + task.threshold,
            diff=task.threshold,
            threshold=task.threshold,
            grid_steps=1,
            direction="UP",
            message="AUTH-CLEAR-BLOCKED",
        )
    )
    db.session.commit()

    response = client.post("/alerts/clear")

    db.session.refresh(task)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert AlertLog.query.count() == 1
    assert task.base_price == original_base_price


def test_logged_in_admin_can_clear_alert_history_without_touching_tasks(app, client, make_task):
    enable_auth(app)
    task = make_task(is_active=True)
    original_base_price = task.base_price
    original_active = task.is_active
    db.session.add_all(
        [
            AlertLog(
                task_id=task.id,
                code=task.code,
                base_price=task.base_price,
                old_base_price=task.base_price,
                new_base_price=task.base_price + task.threshold,
                current_price=task.base_price + task.threshold,
                diff=task.threshold,
                threshold=task.threshold,
                grid_steps=1,
                direction="UP",
                message="AUTH-CLEAR-ONE",
            ),
            AlertLog(
                task_id=task.id,
                code=task.code,
                base_price=task.base_price,
                old_base_price=task.base_price,
                new_base_price=task.base_price - task.threshold,
                current_price=task.base_price - task.threshold,
                diff=-task.threshold,
                threshold=task.threshold,
                grid_steps=1,
                direction="DOWN",
                message="AUTH-CLEAR-TWO",
            ),
        ]
    )
    db.session.commit()
    client.post("/login", data={"username": "admin", "password": "correct-password"})

    response = client.post("/alerts/clear", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "已清空全部报警历史记录" in page
    assert AlertLog.query.count() == 0
    assert db.session.get(type(task), task.id) is not None
    assert task.base_price == original_base_price
    assert task.is_active == original_active


def test_logged_in_post_route_still_reaches_existing_validation(app, client, make_task):
    enable_auth(app)
    task = make_task()
    original_base_price = task.base_price
    client.post("/login", data={"username": "admin", "password": "correct-password"})

    response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": ""},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "请输入有效的模拟价格" in page
    assert AlertLog.query.count() == 0
    assert task.base_price == original_base_price
