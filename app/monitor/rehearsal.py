from typing import Mapping, Sequence


def get_rehearsal_status(
    config: Mapping,
    runtime_status: Mapping,
    enabled_tasks: Sequence,
    notification_channel_status,
) -> dict:
    market_mode = (config.get("MARKET_DATA_MODE") or "manual_only").strip().lower()
    notification_mode = (config.get("NOTIFICATION_MODE") or "dry_run").strip().lower()
    blockers = []
    warnings = []

    if market_mode != "ifind":
        blockers.append("当前行情模式不是 ifind，不能进行真实行情演练。")

    if notification_mode != "feishu":
        blockers.append("当前通知模式不是 feishu，真实演练前请启用飞书通知。")
    elif not getattr(notification_channel_status, "is_configured", False):
        blockers.append("飞书通知未配置，无法验证真实通知链路。")

    if not _ifind_auth_configured(config, runtime_status):
        blockers.append("iFind 授权未就绪，请先确认授权配置和访问令牌状态。")

    if not runtime_status.get("auto_monitoring_enabled"):
        blockers.append("自动监控已关闭，不能进行自动盯盘演练。")
    if runtime_status.get("auto_monitoring_paused"):
        blockers.append("自动监控已暂停，演练前请先恢复自动监控。")

    if not enabled_tasks:
        blockers.append("当前没有启用的监控任务。")
    else:
        blockers.extend(_task_blockers(enabled_tasks))

    if market_mode == "ifind":
        warnings.append("演练前请先点击任务行里的“测试 iFind 行情”。")
    if notification_mode == "feishu":
        warnings.append("演练前请先点击“测试飞书通知”。")
    if not runtime_status.get("worker_started"):
        warnings.append("后台 worker 当前未显示为已启动，运行 Flask server 后请观察状态。")
    if runtime_status.get("last_notification_success") is not True:
        warnings.append("尚无最近一次真实通知成功记录，建议先完成飞书测试通知。")

    if blockers:
        status_key = "blocked"
        title = "暂不能进行真实行情演练"
        level = "danger"
    elif warnings:
        status_key = "short_rehearsal"
        title = "可以进行短时真实演练"
        level = "warning"
    else:
        status_key = "near_live"
        title = "接近正式盯盘"
        level = "success"

    return {
        "status_key": status_key,
        "title": title,
        "level": level,
        "badge_class": _badge_class(level),
        "blockers": blockers,
        "warnings": warnings,
        "suggestions": _suggestions(blockers, warnings),
        "checklist": _checklist(),
        "blockers_count": len(blockers),
        "warnings_count": len(warnings),
    }


def _ifind_auth_configured(config: Mapping, runtime_status: Mapping) -> bool:
    return bool(
        config.get("IFIND_REFRESH_TOKEN")
        or config.get("IFIND_ACCESS_TOKEN")
        or runtime_status.get("ifind_has_refresh_token")
        or runtime_status.get("ifind_has_access_token")
    )


def _task_blockers(enabled_tasks: Sequence) -> list[str]:
    blockers = []
    for task in enabled_tasks:
        code = getattr(task, "code", "未知合约")
        base_price = getattr(task, "base_price", None)
        threshold = getattr(task, "threshold", None)
        if base_price is None:
            blockers.append(f"{code} 的基准价缺失。")
        if threshold is None or threshold <= 0:
            blockers.append(f"{code} 的网格间距无效。")
    return blockers


def _suggestions(blockers: list[str], warnings: list[str]) -> list[str]:
    if blockers:
        return [
            "先处理阻塞项，再进行真实行情演练。",
            "完成 iFind 单次行情测试和飞书测试通知后，再短时开启自动监控。",
        ]
    suggestions = [
        "建议先短时运行 5-10 分钟。",
        "确认基准价和当前真实行情不要相差过大。",
        "演练时持续观察 /healthz、AlertLog 和飞书通知。",
        "如出现异常，立即暂停自动监控。",
    ]
    if warnings:
        suggestions.insert(0, "演练前请先处理风险提醒。")
    return suggestions


def _checklist() -> list[str]:
    return [
        "确认行情模式为 iFind。",
        "确认通知模式为飞书真实通知。",
        "点击“测试 iFind 行情”。",
        "点击“测试飞书通知”。",
        "确认启用任务的基准价和网格间距。",
        "短时开启自动监控。",
        "观察 /healthz。",
        "观察 AlertLog。",
        "如出现异常，立即暂停自动监控。",
    ]


def _badge_class(level: str) -> str:
    return {
        "danger": "text-bg-danger",
        "warning": "text-bg-warning",
        "success": "text-bg-success",
    }.get(level, "text-bg-secondary")
