from typing import Mapping


def get_live_mode_status(
    config: Mapping,
    runtime_status: Mapping,
    preflight_checks: Mapping,
    enabled_tasks_count: int,
    notification_channel_status,
) -> dict:
    market_mode = (config.get("MARKET_DATA_MODE") or "manual_only").strip().lower()
    notification_mode = (config.get("NOTIFICATION_MODE") or "dry_run").strip().lower()
    auto_enabled = bool(runtime_status.get("auto_monitoring_enabled"))
    auto_paused = bool(runtime_status.get("auto_monitoring_paused"))
    auto_active = auto_enabled and not auto_paused
    refresh_configured = bool(config.get("IFIND_REFRESH_TOKEN"))
    access_ready = bool(runtime_status.get("ifind_has_access_token") or config.get("IFIND_ACCESS_TOKEN"))
    real_notification_enabled = bool(getattr(notification_channel_status, "can_send_real_message", False))

    blockers = [check for check in preflight_checks.get("checks", []) if check.get("status") == "blocked"]
    warnings = [check for check in preflight_checks.get("checks", []) if check.get("status") == "warning"]

    if market_mode == "manual_only":
        status = "test_mode"
        title = "当前为测试安全模式"
        message = "不会自动请求真实 iFind 行情，适合检查页面和手动测试价格。"
        suggestions = [
            "正式盯盘前请切换到 iFind 行情模式。",
            "确认 iFind 授权、飞书通知和自动监控均已就绪。",
        ]
    elif market_mode == "simulated":
        status = "test_mode"
        title = "当前为模拟行情测试模式"
        message = "后台可能使用模拟行情推进网格，不适合正式盯盘。"
        suggestions = [
            "正式盯盘前请切换到 iFind 行情模式。",
            "避免用模拟行情影响真实任务基准价。",
        ]
    elif blockers:
        status = "blocked"
        title = "暂不能正式盯盘"
        message = "仍有阻塞项需要处理，建议先完成运行前检查中的阻塞项。"
        suggestions = _check_suggestions(blockers)
    elif warnings or preflight_checks.get("overall_status") == "warning":
        status = "warning"
        title = "接近正式盯盘，但仍有警告"
        message = "关键链路未发现阻塞项，但仍建议处理警告后再长时间运行。"
        suggestions = _check_suggestions(warnings)
    else:
        status = "ready"
        title = "正式盯盘准备完成"
        message = "系统已具备真实行情轮询与飞书预警条件。"
        suggestions = ["正式运行前请复核启用任务的基准价、网格间距和通知接收人。"]

    suggestions = suggestions[:5] or ["请查看运行前检查并处理提示项。"]

    return {
        "live_mode_status": status,
        "live_mode_title": title,
        "live_mode_message": message,
        "live_mode_badge": _badge_for_status(status),
        "live_mode_suggestions": suggestions,
        "is_live_market_enabled": market_mode == "ifind" and refresh_configured and access_ready,
        "is_real_notification_enabled": real_notification_enabled,
        "is_auto_monitoring_active": auto_active,
        "enabled_tasks_count": enabled_tasks_count,
        "live_mode_blockers_count": len(blockers),
        "live_mode_warnings_count": len(warnings),
        "required_config_summary": {
            "market_data_mode": market_mode,
            "notification_mode": notification_mode,
            "auto_monitoring_enabled": auto_enabled,
            "auto_monitoring_paused": auto_paused,
            "ifind_refresh_configured": refresh_configured,
            "ifind_access_ready": access_ready,
            "feishu_channel_configured": bool(getattr(notification_channel_status, "is_configured", False)),
        },
    }


def _check_suggestions(checks: list[Mapping]) -> list[str]:
    suggestions = []
    for check in checks:
        title = check.get("title") or "检查项"
        message = check.get("message") or ""
        suggestion = check.get("suggestion") or ""
        if message:
            suggestions.append(f"{title}：{message}")
        if suggestion:
            suggestions.append(f"建议：{suggestion}")
    return suggestions


def _badge_for_status(status: str) -> str:
    return {
        "test_mode": "text-bg-secondary",
        "blocked": "text-bg-danger",
        "warning": "text-bg-warning",
        "ready": "text-bg-success",
    }.get(status, "text-bg-secondary")
