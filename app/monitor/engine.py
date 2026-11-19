import logging
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter

from app.db import AlertLog, MonitorTask, db
from app.monitor.burst import build_burst_context, parse_burst_config
from app.monitor.checker import CheckResult, ThresholdChecker
from app.monitor.market_provider import MarketDataProvider
from app.monitor.status import (
    record_alert_dedup_status,
    record_alert_status,
    record_burst_alert_status,
    record_market_attempt,
    record_market_request,
    record_notification_failure,
    utc_now,
)
from app.time_utils import CHINA_TZ
from app.notify.notifier import get_notifier
from app.notify.delivery import create_and_attempt_alert_delivery


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorRunResult:
    enabled_task_count: int
    checked_task_count: int
    alert_count: int
    skipped_count: int
    message: str
    poll_status: str = "success"


class MonitoringEngine:
    def __init__(
        self,
        market_provider: MarketDataProvider | None = None,
        checker: ThresholdChecker | None = None,
        notification_mode: str = "dry_run",
        notification_config=None,
    ):
        self.market_provider = market_provider
        self.checker = checker or ThresholdChecker()
        self.notification_config = notification_config or {}
        self.notifier = get_notifier(notification_mode, notification_config)
        self.last_alert_created = False

    def run_once(self) -> MonitorRunResult:
        if self.market_provider is None:
            raise RuntimeError("MonitoringEngine requires a market provider for background polling.")

        tasks = self._load_active_tasks()
        alert_created = False
        alert_count = 0
        skipped_count = 0
        failure_messages = []
        market_prices = self._fetch_market_prices_for_tasks(tasks)

        for task in tasks:
            task_code = task.code.strip().upper()
            market_price = market_prices.get(task_code)
            if not market_price.success or market_price.current_price is None:
                skipped_count += 1
                if market_price.error_message:
                    failure_messages.append(market_price.error_message)
                logger.warning(
                    "Market price skipped: code=%s source=%s error=%s",
                    market_price.code,
                    market_price.source,
                    market_price.error_message,
                )
                continue

            result = self.process_task_price(
                task,
                market_price.current_price,
                source=market_price.source,
            )
            if result.triggered and self.last_alert_created:
                alert_created = True
                alert_count += 1

        if alert_created:
            db.session.commit()

        checked_task_count = len(tasks)
        poll_status = "skipped" if tasks and skipped_count == len(tasks) else "success"
        if skipped_count:
            reason = f"最近失败原因：{failure_messages[0]}" if failure_messages else "部分任务未取得有效行情。"
            message = (
                f"自动监控完成，检查 {checked_task_count} 个任务，"
                f"跳过 {skipped_count} 个任务，触发 {alert_count} 条报警。{reason}"
            )
        else:
            message = f"自动监控完成，检查 {checked_task_count} 个任务，触发 {alert_count} 条报警。"

        return MonitorRunResult(
            enabled_task_count=len(tasks),
            checked_task_count=checked_task_count,
            alert_count=alert_count,
            skipped_count=skipped_count,
            message=message,
            poll_status=poll_status,
        )

    def process_task_price(
        self,
        task: MonitorTask,
        current_price: float,
        message_prefix: str = "",
        source: str = "manual_test",
    ) -> CheckResult:
        self.last_alert_created = False
        result = self.checker.check(task, current_price)

        if not result.triggered:
            record_alert_status(
                "no_alert",
                f"{task.code} 当前价格未突破当前网格。",
                code=task.code,
                created=False,
            )
            return result

        message = self._build_alert_message(result, message_prefix)
        logger.warning(
            "Dynamic grid rebase: code=%s direction=%s current_price=%.2f "
            "old_base_price=%.2f new_base_price=%.2f threshold=%.2f grid_steps=%s",
            result.code,
            result.direction,
            result.current_price,
            result.old_base_price,
            result.new_base_price,
            result.threshold,
            result.grid_steps,
        )
        if self._is_duplicate_alert(task, result):
            record_alert_dedup_status("duplicate_skipped", True)
            record_alert_status(
                "duplicate_skipped",
                "重复报警已跳过，未创建重复 AlertLog，未重复发送通知。",
                code=result.code,
                direction=result.direction,
                grid_steps=result.grid_steps,
                created=False,
            )
            logger.info(
                "Duplicate alert skipped: task_id=%s code=%s direction=%s old_base_price=%.2f "
                "new_base_price=%.2f threshold=%.2f grid_steps=%s",
                task.id,
                result.code,
                result.direction,
                result.old_base_price,
                result.new_base_price,
                result.threshold,
                result.grid_steps,
            )
            task.base_price = result.new_base_price
            return result

        burst_context = self._current_burst_context(task, result)
        alert = self._save_alert(task, result, message)
        # Persist the grid event (and its rebase) before any webhook I/O. A
        # Feishu outage must never roll back the alert or base-price update.
        task.base_price = result.new_base_price
        db.session.flush()
        db.session.commit()
        self.last_alert_created = True
        record_alert_dedup_status("created", False)
        record_alert_status(
            "created",
            message,
            code=result.code,
            direction=result.direction,
            grid_steps=result.grid_steps,
            created=True,
        )
        record_burst_alert_status(
            burst_context["burst_alert_enabled"],
            burst_context["burst_alert_detected"],
            burst_context["burst_alert_count"],
            burst_context["burst_alert_window_seconds"],
            burst_context["burst_alert_message"],
        )
        self._safe_notify(alert, result, source, burst_context)
        return result

    @staticmethod
    def _build_alert_message(result: CheckResult, message_prefix: str = "") -> str:
        direction_label = "向上" if result.direction == "UP" else "向下"
        prefix = f"{message_prefix} " if message_prefix else ""
        return (
            f"{prefix}{result.code} {direction_label}突破 {result.grid_steps} 个网格："
            f"当前价格 {result.current_price:.2f}，"
            f"旧基准价 {result.old_base_price:.2f}，"
            f"新基准价 {result.new_base_price:.2f}，"
            f"网格间距 {result.threshold:.2f}"
        )

    @staticmethod
    def _save_alert(task: MonitorTask, result: CheckResult, message: str) -> AlertLog:
        alert = AlertLog(
            task_id=task.id,
            code=result.code,
            base_price=result.base_price,
            old_base_price=result.old_base_price,
            new_base_price=result.new_base_price,
            current_price=result.current_price,
            diff=result.diff,
            threshold=result.threshold,
            grid_steps=result.grid_steps,
            direction=result.direction,
            message=message,
        )
        db.session.add(alert)
        return alert

    @staticmethod
    def _is_duplicate_alert(task: MonitorTask, result: CheckResult) -> bool:
        event_key = _alert_event_key_from_result(task, result)
        recent_alerts = (
            db.session.query(AlertLog)
            .filter(
                AlertLog.task_id == task.id,
                AlertLog.code == result.code,
            )
            .order_by(AlertLog.id.desc())
            .limit(20)
            .all()
        )
        return any(_alert_event_key_from_alert(alert) == event_key for alert in recent_alerts)

    def _current_burst_context(self, task: MonitorTask, result: CheckResult) -> dict:
        burst_config = parse_burst_config(self.notification_config)
        if not burst_config.enabled:
            return build_burst_context(0, self.notification_config)

        since = utc_now().replace(tzinfo=None) - timedelta(seconds=burst_config.window_seconds)
        recent_alert_count = (
            db.session.query(AlertLog.id)
            .filter(
                AlertLog.task_id == task.id,
                AlertLog.code == result.code,
                AlertLog.created_at >= since,
            )
            .count()
        )
        return build_burst_context(recent_alert_count + 1, self.notification_config)

    @staticmethod
    def _notification_context(result: CheckResult, source: str, burst_context: dict | None = None) -> dict:
        context = {
            "code": result.code,
            "direction": result.direction,
            "current_price": result.current_price,
            "old_base_price": result.old_base_price,
            "new_base_price": result.new_base_price,
            "grid_steps": result.grid_steps,
            "threshold": result.threshold,
            "source": source,
            "source_label": source_label(source),
            "created_at": utc_now().astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        if burst_context:
            context.update(burst_context)
        return context

    def _safe_notify(
        self,
        alert: AlertLog,
        result: CheckResult,
        source: str,
        burst_context: dict | None = None,
    ) -> None:
        try:
            if getattr(self.notifier, "mode", "") == "feishu":
                create_and_attempt_alert_delivery(
                    alert,
                    self.notification_config,
                    source,
                    burst_context=burst_context,
                )
                return
            self.notifier.notify_alert(self._notification_context(result, source, burst_context))
        except Exception as exc:
            notification_mode = getattr(self.notifier, "mode", "unknown")
            record_notification_failure(notification_mode, exc)
            logger.warning(
                "Alert notification failed safely: mode=%s error=%s",
                notification_mode,
                exc.__class__.__name__,
            )

    def _fetch_market_prices_for_tasks(self, tasks: list[MonitorTask]) -> dict[str, object]:
        unique_codes = list(dict.fromkeys(task.code.strip().upper() for task in tasks if task.code))
        if not unique_codes:
            return {}

        if self._provider_supports_batch():
            return self._fetch_market_prices_in_batches(unique_codes)

        market_prices = {}
        first_task_by_code = {}
        for task in tasks:
            code = task.code.strip().upper()
            first_task_by_code.setdefault(code, task)
        for code in unique_codes:
            task = first_task_by_code[code]
            market_prices[code] = self._fetch_single_market_price(code, task.base_price, task.threshold)
        return market_prices

    def _fetch_market_prices_in_batches(self, unique_codes: list[str]) -> dict[str, object]:
        market_prices = {}
        batch_size = self._ifind_batch_size()
        for start in range(0, len(unique_codes), batch_size):
            batch_codes = unique_codes[start : start + batch_size]
            market_prices.update(self._fetch_market_price_batch(batch_codes))
        return market_prices

    def _fetch_market_price_batch(self, batch_codes: list[str]) -> dict[str, object]:
        for code in batch_codes:
            record_market_attempt(code)
        request_started_at = perf_counter()
        try:
            batch_result = self.market_provider.get_prices(batch_codes)
        except Exception as exc:
            logger.warning(
                "Batch market price request failed safely: count=%s error=%s",
                len(batch_codes),
                str(exc) or exc.__class__.__name__,
            )
            return {code: self._fetch_single_market_price(code) for code in batch_codes}

        duration_ms = int((perf_counter() - request_started_at) * 1000)
        market_prices = {}
        for code in batch_codes:
            market_price = batch_result.get(code)
            if market_price is None:
                market_price = self._fetch_single_market_price(code)
            else:
                self._record_market_price(code, market_price, duration_ms)
            market_prices[code] = market_price
        return market_prices

    def _fetch_single_market_price(
        self,
        code: str,
        base_price: float | None = None,
        threshold: float | None = None,
    ):
        request_started_at = perf_counter()
        record_market_attempt(code)
        try:
            market_price = self.market_provider.get_price(code, base_price, threshold)
        except Exception as exc:
            request_duration_ms = int((perf_counter() - request_started_at) * 1000)
            error_message = str(exc) or exc.__class__.__name__
            market_price = _failed_market_price(code, getattr(self.market_provider, "provider_status", "unknown"), error_message)
            record_market_request(
                provider=getattr(self.market_provider, "provider_status", "unknown"),
                status="error",
                message="行情请求异常。",
                duration_ms=request_duration_ms,
                error_message=error_message,
                contract_code=code,
            )
            logger.warning(
                "Market price failed safely: code=%s error=%s",
                code,
                error_message,
            )
            return market_price

        request_duration_ms = int((perf_counter() - request_started_at) * 1000)
        self._record_market_price(code, market_price, request_duration_ms)
        return market_price

    @staticmethod
    def _record_market_price(code: str, market_price, request_duration_ms: int) -> None:
        resolved_code = _market_resolved_code(market_price)
        record_market_request(
            provider=market_price.source,
            status="success" if market_price.success else "failed",
            message=(
                f"{market_price.source} 行情请求成功。"
                if market_price.success
                else market_price.error_message or f"{market_price.source} 行情请求失败。"
            ),
            duration_ms=request_duration_ms,
            error_message=None if market_price.success else market_price.error_message,
            contract_code=code,
            resolved_code=resolved_code,
            price=market_price.current_price if market_price.success else None,
        )

    def _provider_supports_batch(self) -> bool:
        return bool(getattr(self.market_provider, "batch_enabled", False) and hasattr(self.market_provider, "get_prices"))

    def _ifind_batch_size(self) -> int:
        try:
            batch_size = int(self.notification_config.get("IFIND_BATCH_SIZE", 5))
        except (TypeError, ValueError):
            return 5
        return batch_size if batch_size > 0 else 5

    @staticmethod
    def _load_active_tasks() -> list[MonitorTask]:
        return (
            db.session.query(MonitorTask)
            .filter(MonitorTask.is_active.is_(True))
            .order_by(MonitorTask.id.asc())
            .all()
        )


def source_label(source: str) -> str:
    labels = {
        "ifind": "iFind 实时行情",
        "manual_test": "manual_test",
        "simulated": "simulated",
    }
    return labels.get(source, source)


def _alert_event_key_from_result(task: MonitorTask, result: CheckResult) -> tuple:
    return (
        task.id,
        result.code,
        result.direction,
        _price_key(result.old_base_price),
        _price_key(result.new_base_price),
        result.grid_steps,
        _price_key(result.threshold),
    )


def _alert_event_key_from_alert(alert: AlertLog) -> tuple:
    return (
        alert.task_id,
        alert.code,
        alert.direction,
        _price_key(alert.old_base_price),
        _price_key(alert.new_base_price),
        alert.grid_steps,
        _price_key(alert.threshold),
    )


def _price_key(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _market_resolved_code(market_price) -> str | None:
    raw = getattr(market_price, "raw", None) or {}
    return raw.get("ifind_code") or getattr(market_price, "code", None)


def _failed_market_price(code: str, source: str, error_message: str):
    from app.monitor.market_provider import MarketPrice

    return MarketPrice(
        code=code,
        current_price=None,
        timestamp=utc_now(),
        source=source,
        success=False,
        error_message=error_message,
        raw={"ifind_code": code},
    )
