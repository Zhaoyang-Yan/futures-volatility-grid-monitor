from app.db import AlertLog, MonitorTask, db
from app.monitor.engine import MonitoringEngine


def _process_and_commit(task: MonitorTask, current_price: float):
    result = MonitoringEngine().process_task_price(task, current_price)
    if result.triggered:
        db.session.commit()
    return result


def test_price_inside_grid_does_not_rebase(make_task):
    task = make_task()

    result = _process_and_commit(task, 105900)

    assert not result.triggered
    assert result.grid_steps == 0
    assert task.base_price == 105600
    assert AlertLog.query.count() == 0


def test_upward_break_rebases_one_grid(make_task):
    task = make_task()

    result = _process_and_commit(task, 106720)

    assert result.triggered
    assert result.direction == "UP"
    assert result.old_base_price == 105600
    assert result.new_base_price == 106600
    assert result.grid_steps == 1
    assert task.base_price == 106600

    alert = AlertLog.query.one()
    assert alert.direction == "UP"
    assert alert.old_base_price == 105600
    assert alert.new_base_price == 106600
    assert alert.grid_steps == 1


def test_downward_break_rebases_one_grid(make_task):
    task = make_task()

    result = _process_and_commit(task, 104480)

    assert result.triggered
    assert result.direction == "DOWN"
    assert result.old_base_price == 105600
    assert result.new_base_price == 104600
    assert result.grid_steps == 1
    assert task.base_price == 104600

    alert = AlertLog.query.one()
    assert alert.direction == "DOWN"
    assert alert.old_base_price == 105600
    assert alert.new_base_price == 104600
    assert alert.grid_steps == 1


def test_upward_break_multiple_grids_creates_one_alert(make_task):
    task = make_task()

    result = _process_and_commit(task, 108750)

    assert result.triggered
    assert result.direction == "UP"
    assert result.grid_steps == 3
    assert result.new_base_price == 108600
    assert task.base_price == 108600
    assert AlertLog.query.count() == 1


def test_downward_break_multiple_grids_creates_one_alert(make_task):
    task = make_task()

    result = _process_and_commit(task, 102420)

    assert result.triggered
    assert result.direction == "DOWN"
    assert result.grid_steps == 3
    assert result.new_base_price == 102600
    assert task.base_price == 102600
    assert AlertLog.query.count() == 1
