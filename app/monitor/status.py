from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock


_status_lock = Lock()
_DEFAULT_STATUS = {
    "worker_started": False,
    "worker_started_at": None,
    "market_data_mode": "manual_only",
    "auto_monitoring_enabled": True,
    "auto_monitoring_polling_active": False,
    "auto_monitoring_config_enabled": True,
    "auto_monitoring_paused": False,
    "auto_monitoring_pause_reason": None,
    "auto_monitoring_updated_at": None,
    "auto_session_scheduler_enabled": False,
    "auto_monitoring_control_source": "manual",
    "manual_override_until": None,
    "session_scheduler_next_action": None,
    "session_scheduler_last_evaluated_at": None,
    "provider_status": "manual_only",
    "last_poll_at": None,
    "last_poll_status": "skipped",
    "last_poll_message": "暂无轮询记录",
    "last_enabled_task_count": 0,
    "last_alert_count": 0,
    "last_error": None,
    "last_error_at": None,
    "last_run_duration_ms": None,
    "last_market_provider": None,
    "last_market_request_status": "暂无",
    "last_market_request_message": "暂无行情请求",
    "last_market_request_at": None,
    "last_market_request_duration_ms": None,
    "last_market_error": None,
    "last_market_attempt_at": None,
    "last_market_success_at": None,
    "last_market_failure_at": None,
    "last_market_contract_code": None,
    "last_market_resolved_code": None,
    "last_market_price": None,
    "last_market_source": None,
    "last_market_error_summary": None,
    "consecutive_market_failures": 0,
    "market_poll_status_by_contract": {},
    "ifind_auth_state": "not_configured",
    "ifind_has_refresh_token": False,
    "ifind_has_access_token": False,
    "ifind_access_token_expires_at": None,
    "ifind_last_refresh_at": None,
    "ifind_last_refresh_success": False,
    "ifind_last_error": "iFind refresh token 未配置",
    "notification_mode": "dry_run",
    "last_notification_status": "暂无报警",
    "last_notification_content": None,
    "last_notification_error": None,
    "last_notification_at": None,
    "last_notification_success": None,
    "last_notification_channel": None,
    "last_notification_delivery_status": None,
    "last_notification_duration_ms": None,
    "last_notification_safe_error_summary": None,
    "last_notification_can_retry": False,
    "last_notification_attempt_count": 0,
    "last_alert_dedup_status": "暂无重复报警",
    "last_alert_status": "暂无",
    "last_alert_message": "暂无报警判断",
    "last_alert_code": None,
    "last_alert_direction": None,
    "last_alert_grid_steps": None,
    "last_alert_created": False,
    "last_duplicate_alert_skipped": False,
    "last_duplicate_notification_skipped": False,
    "notification_burst_enabled": True,
    "last_burst_alert_detected": False,
    "last_burst_alert_count": 0,
    "last_burst_alert_window_seconds": 300,
    "last_burst_alert_message": None,
}
_status = deepcopy(_DEFAULT_STATUS)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_monitor_status() -> dict:
    with _status_lock:
        return deepcopy(_status)


def reset_monitor_status() -> None:
    with _status_lock:
        _status.clear()
        _status.update(deepcopy(_DEFAULT_STATUS))


def get_runtime_diagnostics(extra: dict | None = None) -> dict:
    diagnostics = get_monitor_status()
    diagnostics["last_poll_duration_ms"] = diagnostics.get("last_run_duration_ms")
    diagnostics.setdefault("last_poll_error", diagnostics.get("last_error"))
    if extra:
        diagnostics.update(extra)
    return diagnostics


def mark_worker_started(
    market_data_mode: str,
    auto_monitoring_polling_active: bool,
    provider_status: str,
) -> None:
    update_monitor_status(
        worker_started=True,
        worker_started_at=utc_now(),
        market_data_mode=market_data_mode,
        auto_monitoring_polling_active=auto_monitoring_polling_active,
        provider_status=provider_status,
    )


def configure_auto_monitoring(enabled: bool, reason: str | None = None) -> None:
    update_monitor_status(
        auto_monitoring_enabled=enabled,
        auto_monitoring_config_enabled=enabled,
        auto_monitoring_paused=not enabled,
        auto_monitoring_pause_reason=None if enabled else reason or "启动配置关闭自动监控",
        auto_monitoring_updated_at=utc_now(),
    )


def pause_auto_monitoring(reason: str = "用户手动暂停自动监控") -> None:
    update_monitor_status(
        auto_monitoring_paused=True,
        auto_monitoring_pause_reason=reason,
        auto_monitoring_updated_at=utc_now(),
    )


def resume_auto_monitoring() -> None:
    update_monitor_status(
        auto_monitoring_enabled=True,
        auto_monitoring_config_enabled=True,
        auto_monitoring_paused=False,
        auto_monitoring_pause_reason=None,
        auto_monitoring_updated_at=utc_now(),
    )


def is_auto_monitoring_runtime_enabled(provider_auto_enabled: bool) -> bool:
    status = get_monitor_status()
    return bool(
        provider_auto_enabled
        and status.get("auto_monitoring_config_enabled")
        and not status.get("auto_monitoring_paused")
    )


def set_auto_monitoring_effective(enabled: bool) -> None:
    update_monitor_status(auto_monitoring_polling_active=enabled)


def update_session_scheduler_status(
    *,
    enabled: bool,
    control_source: str,
    manual_override_until: datetime | None,
    next_action: str | None,
) -> None:
    update_monitor_status(
        auto_session_scheduler_enabled=enabled,
        auto_monitoring_control_source=control_source,
        manual_override_until=manual_override_until,
        session_scheduler_next_action=next_action,
        session_scheduler_last_evaluated_at=utc_now(),
    )


def record_poll_skipped(
    message: str,
    duration_ms: int = 0,
    enabled_task_count: int = 0,
    alert_count: int = 0,
) -> None:
    update_monitor_status(
        last_poll_at=utc_now(),
        last_poll_status="skipped",
        last_poll_message=message,
        last_enabled_task_count=enabled_task_count,
        last_alert_count=alert_count,
        last_error=None,
        last_run_duration_ms=duration_ms,
    )
    record_market_request(
        provider=None,
        status="skipped",
        message=message,
        duration_ms=duration_ms,
    )


def record_poll_success(
    enabled_task_count: int,
    alert_count: int,
    message: str,
    duration_ms: int,
) -> None:
    update_monitor_status(
        last_poll_at=utc_now(),
        last_poll_status="success",
        last_poll_message=message,
        last_enabled_task_count=enabled_task_count,
        last_alert_count=alert_count,
        last_error=None,
        last_run_duration_ms=duration_ms,
    )


def record_poll_error(error: Exception, duration_ms: int) -> None:
    error_message = str(error) or error.__class__.__name__
    update_monitor_status(
        last_poll_at=utc_now(),
        last_poll_status="error",
        last_poll_message="后台自动监控执行异常。",
        last_error=error_message,
        last_error_at=utc_now(),
        last_run_duration_ms=duration_ms,
    )


def record_market_request(
    provider: str | None,
    status: str,
    message: str,
    duration_ms: int | None = None,
    error_message: str | None = None,
    contract_code: str | None = None,
    resolved_code: str | None = None,
    price: float | None = None,
) -> None:
    safe_message = _safe_status_message(message) or "暂无"
    safe_error = _safe_status_message(error_message)
    now = utc_now()
    changes = {
        "last_market_provider": provider,
        "last_market_request_status": status,
        "last_market_request_message": safe_message,
        "last_market_request_at": now,
        "last_market_request_duration_ms": duration_ms,
        "last_market_error": safe_error,
    }
    if contract_code is not None:
        changes["last_market_contract_code"] = contract_code
    if resolved_code is not None:
        changes["last_market_resolved_code"] = resolved_code
    if provider is not None:
        changes["last_market_source"] = provider

    if status == "success" and price is not None:
        changes.update(
            last_market_success_at=now,
            last_market_price=price,
            last_market_error_summary=None,
            consecutive_market_failures=0,
        )
    elif status in {"failed", "error"}:
        current = get_monitor_status()
        changes.update(
            last_market_failure_at=now,
            last_market_error_summary=safe_error or safe_message,
            consecutive_market_failures=int(current.get("consecutive_market_failures") or 0) + 1,
        )
    elif status == "skipped" and contract_code is not None:
        current = get_monitor_status()
        changes.update(
            last_market_failure_at=now,
            last_market_error_summary=safe_error or safe_message,
            consecutive_market_failures=int(current.get("consecutive_market_failures") or 0) + 1,
        )

    if contract_code is not None:
        changes["market_poll_status_by_contract"] = _updated_contract_market_status(
            contract_code=contract_code,
            resolved_code=resolved_code,
            status=status,
            provider=provider,
            price=price,
            checked_at=now,
            error_message=safe_error or (None if status == "success" else safe_message),
        )

    update_monitor_status(**changes)


def record_market_attempt(contract_code: str | None = None, resolved_code: str | None = None) -> None:
    update_monitor_status(
        last_market_attempt_at=utc_now(),
        last_market_contract_code=contract_code,
        last_market_resolved_code=resolved_code,
    )


def _updated_contract_market_status(
    contract_code: str,
    resolved_code: str | None,
    status: str,
    provider: str | None,
    price: float | None,
    checked_at: datetime,
    error_message: str | None,
) -> dict:
    current = get_monitor_status().get("market_poll_status_by_contract") or {}
    updated = deepcopy(current)
    previous = updated.get(contract_code, {})
    consecutive_failures = int(previous.get("consecutive_failures") or 0)
    is_success = status == "success" and price is not None

    if is_success:
        entry = {
            "contract_code": contract_code,
            "resolved_code": resolved_code or contract_code,
            "status": "success",
            "success": True,
            "price": price,
            "source": provider,
            "checked_at": checked_at,
            "last_success_at": checked_at,
            "last_failure_at": previous.get("last_failure_at"),
            "error": None,
            "error_category": None,
            "consecutive_failures": 0,
        }
    else:
        safe_error = _safe_status_message(error_message) or "行情请求失败"
        entry = {
            "contract_code": contract_code,
            "resolved_code": resolved_code or contract_code,
            "status": "failed" if status in {"failed", "error", "skipped"} else status,
            "success": False,
            "price": previous.get("price"),
            "source": provider or previous.get("source"),
            "checked_at": checked_at,
            "last_success_at": previous.get("last_success_at"),
            "last_failure_at": checked_at,
            "error": safe_error,
            "error_category": _market_error_category(safe_error),
            "consecutive_failures": consecutive_failures + 1,
        }

    updated[contract_code] = entry
    return updated


def _market_error_category(error_message: str | None) -> str:
    normalized = (error_message or "").lower()
    if "permission denied" in normalized:
        return "permission_denied"
    if "timeout" in normalized or "超时" in normalized:
        return "timeout"
    if "input parameters" in normalized or "parameter" in normalized:
        return "invalid_parameter"
    return "unknown_error"


def record_alert_status(
    status: str,
    message: str,
    code: str | None = None,
    direction: str | None = None,
    grid_steps: int | None = None,
    created: bool = False,
) -> None:
    update_monitor_status(
        last_alert_status=status,
        last_alert_message=_safe_status_message(message) or "暂无报警判断",
        last_alert_code=code,
        last_alert_direction=direction,
        last_alert_grid_steps=grid_steps,
        last_alert_created=created,
    )


def update_ifind_token_status(token_status) -> None:
    update_monitor_status(
        ifind_auth_state=token_status.auth_state,
        ifind_has_refresh_token=token_status.has_refresh_token,
        ifind_has_access_token=token_status.has_access_token,
        ifind_access_token_expires_at=token_status.access_token_expires_at,
        ifind_last_refresh_at=token_status.last_refresh_at,
        ifind_last_refresh_success=token_status.last_refresh_success,
        ifind_last_error=token_status.last_error,
    )


def record_notification(
    mode: str,
    status: str,
    content: str | None = None,
    success: bool | None = None,
    error_message: str | None = None,
    channel: str | None = None,
    delivery_status: str | None = None,
    sent_at: datetime | None = None,
    duration_ms: int | None = None,
    safe_error_summary: str | None = None,
    can_retry: bool | None = None,
    attempt_count: int | None = None,
) -> None:
    update_monitor_status(
        notification_mode=mode,
        last_notification_status=status,
        last_notification_content=content,
        last_notification_error=_safe_status_message(error_message),
        last_notification_at=sent_at or utc_now(),
        last_notification_success=success,
        last_notification_channel=channel,
        last_notification_delivery_status=delivery_status,
        last_notification_duration_ms=duration_ms,
        last_notification_safe_error_summary=_safe_status_message(safe_error_summary),
        last_notification_can_retry=can_retry,
        last_notification_attempt_count=attempt_count,
    )


def record_notification_failure(mode: str, error: Exception | str) -> None:
    error_message = str(error) or error.__class__.__name__
    update_monitor_status(
        notification_mode=mode,
        last_notification_status="通知失败",
        last_notification_error=_safe_status_message(error_message),
        last_notification_at=utc_now(),
        last_notification_success=False,
        last_notification_channel=mode,
        last_notification_delivery_status="request_error",
        last_notification_safe_error_summary=_safe_status_message(error_message),
        last_notification_can_retry=True,
        last_notification_attempt_count=1,
    )


def record_alert_dedup_status(status: str, duplicate_skipped: bool) -> None:
    update_monitor_status(
        last_alert_dedup_status=status,
        last_duplicate_alert_skipped=duplicate_skipped,
        last_duplicate_notification_skipped=duplicate_skipped,
    )
    if duplicate_skipped:
        record_alert_status(
            "duplicate_skipped",
            "重复报警已跳过，未创建重复 AlertLog，未重复发送通知。",
            created=False,
        )


def record_burst_alert_status(
    enabled: bool,
    detected: bool,
    alert_count: int,
    window_seconds: int,
    message: str | None,
) -> None:
    update_monitor_status(
        notification_burst_enabled=enabled,
        last_burst_alert_detected=detected,
        last_burst_alert_count=alert_count,
        last_burst_alert_window_seconds=window_seconds,
        last_burst_alert_message=_safe_status_message(message),
    )


def update_monitor_status(**changes) -> None:
    with _status_lock:
        _status.update(changes)


def _safe_status_message(message: str | None) -> str | None:
    if not message:
        return None

    sanitized = str(message)
    forbidden_terms = [
        "access_token",
        "refresh_token",
        "Bearer",
        "Authorization",
        "IFIND_REFRESH_TOKEN",
        "IFIND_ACCESS_TOKEN",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_WEBHOOK_SECRET",
        "WECOM_WEBHOOK_URL",
        "webhook",
        "secret",
        "headers",
        ".env",
    ]
    for term in forbidden_terms:
        sanitized = sanitized.replace(term, "[已隐藏]")
    return sanitized
