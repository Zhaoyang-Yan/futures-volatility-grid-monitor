import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import create_app
from app.db import MonitorTask, db
from app.monitor.status import configure_auto_monitoring


@pytest.fixture()
def app():
    app = create_app("testing")
    app.config.update(MARKET_DATA_MODE="manual_only", NOTIFICATION_MODE="dry_run")
    configure_auto_monitoring(app.config["AUTO_MONITORING_ENABLED"])

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_task(app):
    def _make_task(
        code: str = "CU2608",
        base_price: float = 105600,
        threshold: float = 1000,
        is_active: bool = True,
    ) -> MonitorTask:
        task = MonitorTask(
            code=code,
            base_price=base_price,
            threshold=threshold,
            is_active=is_active,
        )
        db.session.add(task)
        db.session.commit()
        return task

    return _make_task
