import pytest
from sqlalchemy.exc import IntegrityError

from app.db import AlertLog, AppSetting, MonitorTask, db
from app.monitor_worker import _current_monitor_poll_interval_seconds, monitor_loop
from app.settings import (
    MONITOR_POLL_INTERVAL_KEY,
    get_app_setting,
    get_monitor_poll_interval_seconds,
    set_app_setting,
    set_monitor_poll_interval_seconds,
)


def _enable_auth(app):
    app.config.update(
        DASHBOARD_AUTH_ENABLED=True,
        DASHBOARD_USERNAME="admin",
        DASHBOARD_PASSWORD="correct-password",
    )


def test_app_setting_model_saves_unique_key_and_updates_value(app):
    setting = set_app_setting("example_key", "one")

    assert setting.id is not None
    assert get_app_setting("example_key") == "one"

    set_app_setting("example_key", "two")
    assert AppSetting.query.filter_by(key="example_key").count() == 1
    assert get_app_setting("example_key") == "two"

    db.session.add(AppSetting(key="example_key", value="duplicate"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_monitor_poll_interval_defaults_to_30_and_creates_setting(app):
    assert get_monitor_poll_interval_seconds() == 30

    setting = AppSetting.query.filter_by(key=MONITOR_POLL_INTERVAL_KEY).one()
    assert setting.value == "30"


def test_monitor_poll_interval_persists_after_update(app):
    set_monitor_poll_interval_seconds(60)

    db.session.remove()

    assert get_monitor_poll_interval_seconds() == 60
    assert AppSetting.query.filter_by(key=MONITOR_POLL_INTERVAL_KEY).one().value == "60"


def test_dashboard_shows_monitor_poll_interval_form(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前轮询间隔：30 秒" in page
    assert 'action="/settings/monitor-poll-interval"' in page
    assert 'method="post"' in page
    assert 'name="poll_interval_seconds"' in page
    for option in [10, 15, 30, 60, 120, 300]:
        assert f'value="{option}"' in page


def test_post_monitor_poll_interval_updates_database(client):
    response = client.post(
        "/settings/monitor-poll-interval",
        data={"poll_interval_seconds": "60"},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "轮询间隔已更新为 60 秒" in page
    assert get_monitor_poll_interval_seconds() == 60
    assert AppSetting.query.filter_by(key=MONITOR_POLL_INTERVAL_KEY).one().value == "60"


@pytest.mark.parametrize(
    ("raw_value", "message"),
    [
        ("9", "轮询间隔不能小于 10 秒"),
        ("301", "轮询间隔不能大于 300 秒"),
        ("abc", "轮询间隔必须是整数秒"),
        ("", "轮询间隔不能为空"),
    ],
)
def test_invalid_monitor_poll_interval_keeps_existing_value(client, raw_value, message, make_task):
    task = make_task()
    original_base_price = task.base_price
    original_active = task.is_active
    set_monitor_poll_interval_seconds(30)

    response = client.post(
        "/settings/monitor-poll-interval",
        data={"poll_interval_seconds": raw_value},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert message in page
    assert get_monitor_poll_interval_seconds() == 30
    assert AlertLog.query.count() == 0
    assert MonitorTask.query.count() == 1
    assert task.base_price == original_base_price
    assert task.is_active == original_active


def test_auth_protects_monitor_poll_interval_update(app, client):
    _enable_auth(app)

    blocked_response = client.post(
        "/settings/monitor-poll-interval",
        data={"poll_interval_seconds": "60"},
    )
    assert blocked_response.status_code == 302
    assert "/login" in blocked_response.headers["Location"]
    assert get_monitor_poll_interval_seconds() == 30

    client.post("/login", data={"username": "admin", "password": "correct-password"})
    allowed_response = client.post(
        "/settings/monitor-poll-interval",
        data={"poll_interval_seconds": "60"},
        follow_redirects=True,
    )
    page = allowed_response.get_data(as_text=True)

    assert allowed_response.status_code == 200
    assert "轮询间隔已更新为 60 秒" in page
    assert get_monitor_poll_interval_seconds() == 60


def test_healthz_contains_monitor_poll_interval(client):
    set_monitor_poll_interval_seconds(120)

    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert data["monitor_poll_interval_seconds"] == 120


def test_worker_interval_helper_reads_database_setting(app):
    set_monitor_poll_interval_seconds(120)

    assert _current_monitor_poll_interval_seconds(app, 30) == 120


def test_monitor_loop_reads_latest_interval_each_round(monkeypatch, app):
    class Provider:
        provider_status = "test_provider"
        auto_monitoring_enabled = False

    sleep_calls = []

    monkeypatch.setattr("app.monitor_worker.get_market_data_provider", lambda mode, config: Provider())

    def fake_run_monitor_cycle(*args, **kwargs):
        set_monitor_poll_interval_seconds(60)

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise RuntimeError("stop loop")

    monkeypatch.setattr("app.monitor_worker.run_monitor_cycle", fake_run_monitor_cycle)
    monkeypatch.setattr("app.monitor_worker.time.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop loop"):
        monitor_loop(app, interval_seconds=30)

    assert sleep_calls == [60]
