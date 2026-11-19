from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.status import get_monitor_status
from app.notify.notifier import build_alert_notification


class CountingNotifier:
    mode = "counting_test"

    def __init__(self):
        self.calls = []

    def notify_alert(self, alert_context):
        self.calls.append(alert_context)


def make_counting_engine(monkeypatch):
    notifier = CountingNotifier()
    monkeypatch.setattr("app.monitor.engine.get_notifier", lambda mode, config=None: notifier)
    return MonitoringEngine(notification_mode="dry_run"), notifier


def test_duplicate_alert_event_is_skipped(monkeypatch, make_task):
    task = make_task(base_price=103600, threshold=1000)
    engine, notifier = make_counting_engine(monkeypatch)

    first = engine.process_task_price(task, 105800, source="manual_test")
    db.session.commit()

    task.base_price = 103600
    duplicate = engine.process_task_price(task, 105820, source="manual_test")
    db.session.commit()

    assert first.triggered is True
    assert duplicate.triggered is True
    assert task.base_price == 105600
    assert AlertLog.query.count() == 1
    assert len(notifier.calls) == 1

    status = get_monitor_status()
    assert status["last_alert_dedup_status"] == "duplicate_skipped"
    assert status["last_duplicate_alert_skipped"] is True
    dedup_status = status["last_alert_dedup_status"]
    assert "access_token" not in dedup_status
    assert "refresh_token" not in dedup_status
    assert "Bearer" not in dedup_status
    assert "IFIND_REFRESH_TOKEN" not in dedup_status
    assert "headers" not in dedup_status
    assert ".env" not in dedup_status


def test_new_grid_break_after_rebase_is_not_deduped(monkeypatch, make_task):
    task = make_task(base_price=103600, threshold=1000)
    engine, notifier = make_counting_engine(monkeypatch)

    first = engine.process_task_price(task, 105800, source="manual_test")
    db.session.commit()
    second = engine.process_task_price(task, 106720, source="manual_test")
    db.session.commit()

    assert first.old_base_price == 103600
    assert first.new_base_price == 105600
    assert first.grid_steps == 2
    assert second.old_base_price == 105600
    assert second.new_base_price == 106600
    assert second.grid_steps == 1
    assert AlertLog.query.count() == 2
    assert len(notifier.calls) == 2


def test_new_grid_break_after_multistep_alert_is_not_deduplicated(client, make_task):
    task = make_task(code="CU2608", base_price=105600, threshold=1000)

    first_response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "107600"},
    )
    db.session.refresh(task)
    first_alert = AlertLog.query.order_by(AlertLog.id.asc()).first()

    second_response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "108600"},
    )
    db.session.refresh(task)
    alerts = AlertLog.query.order_by(AlertLog.id.asc()).all()
    status = get_monitor_status()

    assert first_response.status_code == 302
    assert second_response.status_code == 302
    assert first_alert.old_base_price == 105600
    assert first_alert.new_base_price == 107600
    assert first_alert.grid_steps == 2
    assert "剧烈波动" in build_alert_notification(
        {
            "code": first_alert.code,
            "direction": first_alert.direction,
            "current_price": first_alert.current_price,
            "old_base_price": first_alert.old_base_price,
            "new_base_price": first_alert.new_base_price,
            "grid_steps": first_alert.grid_steps,
            "threshold": first_alert.threshold,
            "source_label": "manual_test",
            "created_at": "2026-06-29 21:00:00",
        }
    )

    assert len(alerts) == 2
    assert alerts[1].old_base_price == 107600
    assert alerts[1].new_base_price == 108600
    assert alerts[1].grid_steps == 1
    assert task.base_price == 108600
    assert status["last_alert_dedup_status"] == "created"
    assert status["last_duplicate_alert_skipped"] is False


def test_direction_change_is_not_deduped(monkeypatch, make_task):
    task = make_task(base_price=105600, threshold=1000)
    engine, notifier = make_counting_engine(monkeypatch)

    up = engine.process_task_price(task, 106720, source="manual_test")
    db.session.commit()
    down = engine.process_task_price(task, 105400, source="manual_test")
    db.session.commit()

    assert up.direction == "UP"
    assert down.direction == "DOWN"
    assert AlertLog.query.count() == 2
    assert len(notifier.calls) == 2


def test_volatility_warning_for_multiple_grid_breaks():
    content = build_alert_notification(
        {
            "code": "CU2608",
            "direction": "UP",
            "current_price": 105800,
            "old_base_price": 103600,
            "new_base_price": 105600,
            "grid_steps": 2,
            "threshold": 1000,
            "source_label": "manual_test",
            "created_at": "2026-06-29 21:00:00",
        }
    )

    assert "剧烈波动" in content
    assert "一次性突破 2 个网格" in content


def test_strong_volatility_warning_for_three_or_more_grid_breaks():
    content = build_alert_notification(
        {
            "code": "CU2608",
            "direction": "DOWN",
            "current_price": 102420,
            "old_base_price": 105600,
            "new_base_price": 102600,
            "grid_steps": 3,
            "threshold": 1000,
            "source_label": "manual_test",
            "created_at": "2026-06-29 21:00:00",
        }
    )

    assert "强烈波动" in content
    assert "一次性突破 3 个网格" in content


def test_single_grid_break_has_no_volatility_warning():
    content = build_alert_notification(
        {
            "code": "CU2608",
            "direction": "UP",
            "current_price": 106720,
            "old_base_price": 105600,
            "new_base_price": 106600,
            "grid_steps": 1,
            "threshold": 1000,
            "source_label": "manual_test",
            "created_at": "2026-06-29 21:00:00",
        }
    )

    assert "剧烈波动" not in content
    assert "强烈波动" not in content
