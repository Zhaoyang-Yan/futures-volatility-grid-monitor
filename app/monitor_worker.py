import logging
import os
import sys
import threading
import time
from time import perf_counter

from app.db import db
from app import settings as app_settings
from app.monitor.engine import MonitoringEngine
from app.monitor.market_provider import get_market_data_provider
from app.monitor.session_scheduler import apply_auto_session_schedule
from app.monitor.status import (
    get_monitor_status,
    is_auto_monitoring_runtime_enabled,
    mark_worker_started,
    record_poll_error,
    record_poll_skipped,
    record_poll_success,
    set_auto_monitoring_effective,
    update_ifind_token_status,
)
from app.notify.delivery import process_due_notification_deliveries


logger = logging.getLogger(__name__)
_worker_started = False
_worker_lock = threading.Lock()


def monitor_loop(app, interval_seconds: int = 30) -> None:
    market_data_mode = app.config["MARKET_DATA_MODE"]
    market_provider = get_market_data_provider(market_data_mode, app.config)
    provider_auto_enabled = _auto_monitoring_enabled(market_provider)
    provider_status = _provider_status(market_data_mode, market_provider)
    with app.app_context():
        try:
            apply_auto_session_schedule()
        finally:
            db.session.remove()
    auto_monitoring_enabled = is_auto_monitoring_runtime_enabled(provider_auto_enabled)
    logger.info(
        "Auto monitor configured: mode=%s provider_status=%s provider_auto_enabled=%s runtime_enabled=%s interval_seconds=%s",
        market_data_mode,
        provider_status,
        provider_auto_enabled,
        auto_monitoring_enabled,
        interval_seconds,
    )
    if auto_monitoring_enabled:
        _refresh_ifind_auth_status(market_provider)
    mark_worker_started(market_data_mode, auto_monitoring_enabled, provider_status)

    if not auto_monitoring_enabled:
        logger.info("当前行情源模式为 %s，后台自动行情监控已跳过。", market_data_mode)

    while True:
        run_monitor_cycle(
            app,
            market_provider,
            market_data_mode,
            provider_status,
            provider_auto_enabled,
        )
        time.sleep(_current_monitor_poll_interval_seconds(app, interval_seconds))


def run_monitor_cycle(
    app,
    market_provider,
    market_data_mode: str,
    provider_status: str,
    provider_auto_enabled: bool,
) -> None:
    started_at = perf_counter()
    with app.app_context():
        try:
            # This is deliberately part of the existing worker tick: no second
            # scheduler thread/process is created for the Web + Worker setup.
            apply_auto_session_schedule()
            auto_monitoring_enabled = is_auto_monitoring_runtime_enabled(provider_auto_enabled)
            set_auto_monitoring_effective(auto_monitoring_enabled)
            logger.info(
                "Monitor tick started: mode=%s provider_status=%s auto_monitoring_enabled=%s",
                market_data_mode,
                provider_status,
                auto_monitoring_enabled,
            )
            if not auto_monitoring_enabled:
                duration_ms = _duration_ms(started_at)
                record_poll_skipped(
                    _runtime_skipped_poll_message(market_data_mode, provider_status, market_provider),
                    duration_ms,
                )
                retry_count = process_due_notification_deliveries(app.config)
                logger.info("Monitor tick finished: status=skipped duration_ms=%s", duration_ms)
                if retry_count:
                    logger.info("Notification retry tick finished: processed=%s", retry_count)
                return

            _refresh_ifind_auth_status(market_provider)
            result = MonitoringEngine(
                market_provider,
                notification_mode=app.config["NOTIFICATION_MODE"],
                notification_config=app.config,
            ).run_once()
            if result.poll_status == "skipped":
                record_poll_skipped(
                    result.message,
                    _duration_ms(started_at),
                    enabled_task_count=result.enabled_task_count,
                    alert_count=result.alert_count,
                )
            else:
                record_poll_success(
                    result.enabled_task_count,
                    result.alert_count,
                    result.message,
                    _duration_ms(started_at),
                )
            retry_count = process_due_notification_deliveries(app.config)
            logger.info(
                "Monitor tick finished: status=%s enabled_tasks=%s alerts=%s duration_ms=%s",
                result.poll_status,
                result.enabled_task_count,
                result.alert_count,
                _duration_ms(started_at),
            )
            if retry_count:
                logger.info("Notification retry tick finished: processed=%s", retry_count)
        except Exception as exc:
            db.session.rollback()
            record_poll_error(exc, _duration_ms(started_at))
            logger.exception("Background monitor worker failed during polling.")
        finally:
            db.session.remove()


def should_start_monitor_worker(app) -> bool:
    if app.config.get("TESTING"):
        return False

    if _is_debug_reloader_parent_process(app):
        return False

    return _is_flask_run_command() or _is_gunicorn_command() or _worker_start_forced()


def start_monitor_worker(app) -> None:
    global _worker_started

    if not should_start_monitor_worker(app):
        logger.info("Background monitor worker not started for this process.")
        return

    with _worker_lock:
        if _worker_started:
            logger.info("Background monitor worker already started in this process.")
            return

        worker = threading.Thread(
            target=monitor_loop,
            args=(app, app.config["MONITOR_INTERVAL_SECONDS"]),
            name="grid-monitor-worker",
            daemon=True,
        )
        worker.start()
        _worker_started = True
        logger.info("Auto monitor worker started.")


def _is_flask_run_command() -> bool:
    return "run" in sys.argv[1:]


def _is_gunicorn_command() -> bool:
    command = os.path.basename(sys.argv[0] or "")
    return command.startswith("gunicorn")


def _worker_start_forced() -> bool:
    return os.environ.get("GRID_MONITOR_START_WORKER", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_debug_reloader_parent_process(app) -> bool:
    debug_enabled = app.debug or os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true"}
    return debug_enabled and os.environ.get("WERKZEUG_RUN_MAIN") != "true"


def _duration_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def _current_monitor_poll_interval_seconds(app, fallback_seconds: int = 30) -> int:
    with app.app_context():
        try:
            return app_settings.get_monitor_poll_interval_seconds(fallback_seconds)
        except Exception:
            db.session.rollback()
            logger.exception("Failed to load monitor polling interval; using fallback.")
            return fallback_seconds
        finally:
            db.session.remove()


def _auto_monitoring_enabled(market_provider) -> bool:
    return bool(market_provider and getattr(market_provider, "auto_monitoring_enabled", True))


def _provider_status(mode: str, market_provider) -> str:
    if market_provider is not None:
        return getattr(market_provider, "provider_status", "unknown")
    if mode == "real":
        return "real_not_configured"
    if mode == "ifind":
        return "real_not_configured"
    if mode == "manual_only":
        return "manual_only"
    return "unknown_mode"


def _skipped_poll_message(mode: str, provider_status: str, market_provider=None) -> str:
    status = get_monitor_status()
    if not status.get("auto_monitoring_config_enabled"):
        return "后台自动行情监控已关闭，仅支持手动测试价格。"
    if provider_status == "ifind_not_implemented":
        token_status = _ifind_token_status(market_provider)
        if token_status and token_status.auth_state == "access_token_ready":
            return "iFind access token 已就绪，实时行情接口尚未实现。"
        return "iFind 行情接口尚未实现，自动行情监控已跳过。"
    if mode in {"real", "ifind"}:
        token_status = _ifind_token_status(market_provider)
        if token_status and token_status.last_error:
            return token_status.last_error
        return "iFind 行情源尚未配置，自动行情监控已跳过。"
    if mode == "manual_only":
        return "当前为 manual_only 安全模式，已跳过自动行情轮询，不请求真实行情。"
    return "未知行情源模式，已回退到手动测试模式。"


def _runtime_skipped_poll_message(mode: str, provider_status: str, market_provider=None) -> str:
    status = get_monitor_status()
    if not status.get("auto_monitoring_config_enabled"):
        return "后台自动行情监控已关闭，仅支持手动测试价格。"
    if status.get("auto_monitoring_paused"):
        pause_reason = status.get("auto_monitoring_pause_reason") or "自动监控已暂停"
        if "跳过轮询" in pause_reason:
            return pause_reason
        return f"{pause_reason}，跳过轮询。"
    return _skipped_poll_message(mode, provider_status, market_provider)


def _refresh_ifind_auth_status(market_provider) -> None:
    if not hasattr(market_provider, "token_manager"):
        return
    market_provider.token_manager.get_valid_access_token()
    update_ifind_token_status(market_provider.token_manager.get_status())


def _ifind_token_status(market_provider):
    if not hasattr(market_provider, "token_manager"):
        return None
    return market_provider.token_manager.get_status()
