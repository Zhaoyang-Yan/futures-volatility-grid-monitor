from app import create_app
from app.db import AlertLog
from app.monitor.market_provider import (
    IfindMarketDataProvider,
    SimulatedMarketDataProvider,
    get_market_data_provider,
)
import app.monitor_worker as monitor_worker
from app.monitor_worker import should_start_monitor_worker


def test_testing_app_does_not_start_background_worker():
    app = create_app("testing")

    assert app.config["TESTING"] is True
    assert should_start_monitor_worker(app) is False


def test_gunicorn_command_starts_background_worker_guard(monkeypatch, app):
    app.config["TESTING"] = False
    app.debug = False
    monkeypatch.setattr("sys.argv", ["gunicorn", "-c", "gunicorn.conf.py", "main:app"])

    assert should_start_monitor_worker(app) is True


def test_non_server_command_does_not_start_background_worker(monkeypatch, app):
    app.config["TESTING"] = False
    app.debug = False
    monkeypatch.setattr("sys.argv", ["pytest"])

    assert should_start_monitor_worker(app) is False


def test_start_monitor_worker_is_idempotent_in_same_process(monkeypatch, app):
    started_threads = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            started_threads.append(self)

    app.config["TESTING"] = False
    app.debug = False
    monkeypatch.setattr(monitor_worker, "_worker_started", False)
    monkeypatch.setattr(monitor_worker, "should_start_monitor_worker", lambda app: True)
    monkeypatch.setattr(monitor_worker.threading, "Thread", FakeThread)

    monitor_worker.start_monitor_worker(app)
    monitor_worker.start_monitor_worker(app)

    assert len(started_threads) == 1


def test_manual_only_provider_is_disabled(app, make_task):
    task = make_task()
    app.config["MARKET_DATA_MODE"] = "manual_only"

    provider = get_market_data_provider(app.config["MARKET_DATA_MODE"])

    assert provider is None
    assert task.base_price == 105600
    assert AlertLog.query.count() == 0


def test_simulated_provider_is_selected():
    provider = get_market_data_provider("simulated")

    assert isinstance(provider, SimulatedMarketDataProvider)


def test_real_provider_is_ifind_realtime_provider_and_fails_safely_without_token():
    provider = get_market_data_provider("real")

    assert isinstance(provider, IfindMarketDataProvider)
    price = provider.get_price("CU2608", base_price=105600)
    assert price.success is False
    assert price.current_price is None
    assert "未配置" in price.error_message


def test_ifind_provider_is_ifind_realtime_provider_and_fails_safely_without_token():
    provider = get_market_data_provider("ifind")

    assert isinstance(provider, IfindMarketDataProvider)
    price = provider.get_price("CU2608", base_price=105600)
    assert price.success is False
    assert price.current_price is None
    assert "未配置" in price.error_message


def test_unknown_provider_mode_falls_back_safely():
    provider = get_market_data_provider("unknown_value")

    assert provider is None
