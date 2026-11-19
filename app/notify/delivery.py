"""Persistent, bounded retry handling for real Feishu alert deliveries."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from sqlalchemy import func

from app.db import AlertLog, NotificationDelivery, db, utc_now_naive
from app.notify.notifier import (
    NotificationResult,
    get_notifier,
    sanitize_notification_error,
)
from app.time_utils import as_china_time, format_china_time


logger = logging.getLogger(__name__)

PENDING = "pending"
RETRYING = "retrying"
SENT = "sent"
FAILED = "failed"
RETRYABLE_STATUSES = (PENDING, RETRYING)


def create_and_attempt_alert_delivery(
    alert: AlertLog,
    config: Mapping,
    source: str,
    burst_context: dict | None = None,
    notifier_factory: Callable | None = None,
) -> NotificationDelivery:
    """Persist a real-alert delivery before making its first webhook attempt."""
    delivery = _get_or_create_delivery(alert, config, source)
    # The alert + delivery must survive a network exception. Committing here also
    # persists the dynamic grid base price already set by the caller.
    db.session.commit()
    return attempt_delivery(delivery, config, burst_context=burst_context, notifier_factory=notifier_factory)


def process_due_notification_deliveries(
    config: Mapping,
    now: datetime | None = None,
    limit: int | None = None,
    notifier_factory: Callable | None = None,
) -> int:
    """Process a bounded number of persisted retries during the existing tick."""
    if not _retry_enabled(config) or str(config.get("NOTIFICATION_MODE", "")).lower() != "feishu":
        return 0

    now = now or utc_now_naive()
    limit = _positive_config_int(config.get("FEISHU_RETRY_BATCH_SIZE"), 20) if limit is None else max(limit, 0)
    if limit == 0:
        return 0
    due = (
        NotificationDelivery.query.filter(
            NotificationDelivery.channel == "feishu",
            NotificationDelivery.status.in_(RETRYABLE_STATUSES),
            NotificationDelivery.attempt_count < NotificationDelivery.max_attempts,
            NotificationDelivery.next_retry_at.isnot(None),
            NotificationDelivery.next_retry_at <= now,
        )
        .order_by(NotificationDelivery.next_retry_at.asc(), NotificationDelivery.id.asc())
        .limit(limit)
        .all()
    )

    processed = 0
    for delivery in due:
        try:
            attempt_delivery(delivery, config, notifier_factory=notifier_factory)
            processed += 1
        except Exception:
            db.session.rollback()
            logger.exception(
                "Notification retry isolated: delivery_id=%s alert_id=%s",
                delivery.id,
                delivery.alert_log_id,
            )
    return processed


def attempt_delivery(
    delivery: NotificationDelivery,
    config: Mapping,
    burst_context: dict | None = None,
    notifier_factory: Callable | None = None,
) -> NotificationDelivery:
    """Send one persisted delivery attempt and atomically record the result."""
    if delivery.status == SENT or delivery.attempt_count >= delivery.max_attempts:
        return delivery

    now = utc_now_naive()
    delivery.attempt_count += 1
    delivery.last_attempt_at = now
    delivery.status = RETRYING if delivery.attempt_count > 1 else PENDING
    alert = delivery.alert_log
    context = _alert_context(alert, delivery, burst_context)
    try:
        notifier = (notifier_factory or get_notifier)("feishu", config)
        result = notifier.notify_alert(context)
    except Exception as exc:  # defensive isolation for third-party notifier implementations
        result = NotificationResult(
            success=False,
            mode="feishu",
            status="request_error",
            message="飞书通知发送失败。",
            channel="feishu",
            safe_error_summary=f"飞书通知请求异常：{exc.__class__.__name__}",
            can_retry=True,
            delivery_status="request_error",
        )

    _apply_delivery_result(delivery, result, config, now)
    db.session.commit()
    logger.info(
        "Notification delivery attempt: delivery_id=%s alert_id=%s attempt=%s/%s status=%s",
        delivery.id,
        delivery.alert_log_id,
        delivery.attempt_count,
        delivery.max_attempts,
        delivery.status,
    )
    return delivery


def delivery_status_label(delivery: NotificationDelivery | None) -> tuple[str, str]:
    if delivery is None:
        return "无投递记录", "secondary"
    if delivery.status == SENT:
        return ("补发成功" if delivery.attempt_count > 1 else "已发送"), "success"
    if delivery.status == FAILED:
        return "最终失败", "danger"
    if delivery.status == RETRYING:
        return f"重试中 {delivery.attempt_count}/{delivery.max_attempts}", "warning"
    return f"等待重试 {delivery.attempt_count}/{delivery.max_attempts}", "warning"


def delivery_display_view(
    alert: AlertLog,
    delivery: NotificationDelivery | None,
    rollout_started_at: datetime | None,
) -> dict:
    """Return dashboard-only wording without mutating historical alerts."""
    if delivery is None:
        alert_created_at = alert.created_at
        if alert_created_at is not None and alert_created_at.tzinfo is not None:
            alert_created_at = alert_created_at.astimezone(timezone.utc).replace(tzinfo=None)
        is_historical = bool(
            rollout_started_at
            and alert_created_at
            and alert_created_at < rollout_started_at
        )
        if is_historical:
            return {
                "label": "历史记录",
                "class": "secondary",
                "title": "该报警产生于飞书可靠投递功能上线前，无可追踪的投递记录。",
                "delivery": None,
                "alert_number": f"ALERT-{alert.created_at.strftime('%Y%m%d')}-{alert.id:06d}",
            }
        return {
            "label": "无投递记录",
            "class": "secondary",
            "title": "该报警未找到对应的飞书投递记录，请检查运行日志。",
            "delivery": None,
            "alert_number": f"ALERT-{alert.created_at.strftime('%Y%m%d')}-{alert.id:06d}",
        }

    label, badge_class = delivery_status_label(delivery)
    return {
        "label": label,
        "class": badge_class,
        "title": "飞书投递状态。",
        "delivery": delivery,
        "alert_number": f"ALERT-{alert.created_at.strftime('%Y%m%d')}-{alert.id:06d}",
    }


def delivery_summary() -> dict:
    counts = {status: 0 for status in (PENDING, RETRYING, SENT, FAILED)}
    for status, count in db.session.query(NotificationDelivery.status, func.count(NotificationDelivery.id)).group_by(NotificationDelivery.status):
        counts[status] = count
    latest_success = (
        NotificationDelivery.query.filter(NotificationDelivery.status == SENT)
        .order_by(NotificationDelivery.sent_at.desc(), NotificationDelivery.id.desc())
        .first()
    )
    latest_issue = (
        NotificationDelivery.query.filter(NotificationDelivery.status.in_((PENDING, RETRYING, FAILED)))
        .order_by(NotificationDelivery.updated_at.desc(), NotificationDelivery.id.desc())
        .first()
    )
    return {
        "pending": counts[PENDING],
        "retrying": counts[RETRYING],
        "sent": counts[SENT],
        "failed": counts[FAILED],
        "latest_success_at": latest_success.sent_at if latest_success else None,
        "latest_error": latest_issue.last_error_message if latest_issue else None,
        "channel_status": "异常" if counts[FAILED] else ("重试中" if counts[PENDING] or counts[RETRYING] else "正常"),
    }


def _get_or_create_delivery(alert: AlertLog, config: Mapping, source: str) -> NotificationDelivery:
    existing = NotificationDelivery.query.filter_by(alert_log_id=alert.id, channel="feishu").first()
    if existing:
        return existing
    delivery = NotificationDelivery(
        alert_log_id=alert.id,
        channel="feishu",
        status=PENDING,
        max_attempts=_max_attempts(config),
        source=source,
        message_key=f"alert-{alert.id}-feishu",
    )
    db.session.add(delivery)
    return delivery


def _apply_delivery_result(delivery: NotificationDelivery, result: NotificationResult, config: Mapping, now: datetime) -> None:
    if result.success:
        delivery.status = SENT
        delivery.sent_at = result.sent_at.replace(tzinfo=None) if result.sent_at else now
        delivery.next_retry_at = None
        delivery.last_error_code = None
        delivery.last_error_message = None
        return

    delivery.last_error_code = _result_error_code(result)
    delivery.last_error_message = sanitize_notification_error(
        result.safe_error_summary or result.error_message,
        sensitive_values=(config.get("FEISHU_WEBHOOK_URL", ""), config.get("FEISHU_WEBHOOK_SECRET", "")),
    ) or "飞书通知发送失败"
    if _is_retryable_delivery_result(result) and _retry_enabled(config) and delivery.attempt_count < delivery.max_attempts:
        delivery.status = PENDING if delivery.attempt_count == 1 else RETRYING
        delay = _retry_delay_seconds(config, delivery.attempt_count)
        delivery.next_retry_at = now + timedelta(seconds=delay)
    else:
        delivery.status = FAILED
        delivery.next_retry_at = None


def _alert_context(alert: AlertLog, delivery: NotificationDelivery, burst_context: dict | None) -> dict:
    created_at = as_china_time(alert.created_at)
    created_at_text = format_china_time(alert.created_at)
    context = {
        "code": alert.code,
        "direction": alert.direction,
        "current_price": alert.current_price,
        "old_base_price": alert.old_base_price,
        "new_base_price": alert.new_base_price,
        "grid_steps": alert.grid_steps,
        "threshold": alert.threshold,
        "source": delivery.source,
        "source_label": _source_label(delivery.source or "自动监控"),
        "created_at": created_at_text,
        "alert_number": f"ALERT-{created_at.strftime('%Y%m%d')}-{alert.id:06d}",
        "is_retry": delivery.attempt_count > 1,
        "original_alert_time": created_at_text,
    }
    if burst_context:
        context.update(burst_context)
    return context


def _result_error_code(result: NotificationResult) -> str | None:
    if result.provider_error_code:
        return f"feishu_{result.provider_error_code}"
    if result.http_status_code:
        return f"http_{result.http_status_code}"
    return result.status or None


def _is_retryable_delivery_result(result: NotificationResult) -> bool:
    if result.http_status_code is not None:
        return result.http_status_code == 429 or result.http_status_code >= 500
    if result.provider_error_code is not None:
        return result.provider_error_code == "19006"
    return bool(result.can_retry)


def _retry_enabled(config: Mapping) -> bool:
    value = config.get("FEISHU_RETRY_ENABLED", True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _max_attempts(config: Mapping) -> int:
    return max(_positive_config_int(config.get("FEISHU_MAX_ATTEMPTS"), 4), 1)


def _retry_delay_seconds(config: Mapping, failed_attempt_count: int) -> int:
    raw_delays = config.get("FEISHU_RETRY_DELAYS_SECONDS", (30, 60, 120))
    if isinstance(raw_delays, str):
        try:
            raw_delays = tuple(int(item.strip()) for item in raw_delays.split(",") if item.strip())
        except ValueError:
            raw_delays = (30, 60, 120)
    delays = tuple(value for value in raw_delays if isinstance(value, int) and value > 0) or (30, 60, 120)
    return delays[min(max(failed_attempt_count - 1, 0), len(delays) - 1)]


def _positive_config_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _source_label(source: str) -> str:
    return {
        "ifind": "iFind 实时行情",
        "manual_test": "manual_test",
        "simulated": "simulated",
    }.get(source, source)
