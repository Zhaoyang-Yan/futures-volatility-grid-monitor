from dataclasses import dataclass
from typing import Mapping

from app.monitor.burst import parse_burst_config


@dataclass(frozen=True)
class PreflightCheck:
    key: str
    title: str
    status: str
    message: str
    suggestion: str
    category: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "suggestion": self.suggestion,
            "category": self.category,
        }


def get_preflight_checks(
    config: Mapping,
    runtime_status: Mapping,
    ifind_permission_status,
    notification_channel_status,
    enabled_tasks_count: int,
) -> dict:
    checks = [
        _market_mode_check(config),
        _ifind_refresh_token_check(config),
        _ifind_access_token_check(config, runtime_status),
        _ifind_permission_check(ifind_permission_status),
        _auto_monitoring_check(runtime_status),
        _tasks_check(enabled_tasks_count),
        _notification_mode_check(config, notification_channel_status),
        _notification_diagnostics_check(runtime_status),
        _burst_check(config),
        _security_check(),
    ]
    ready_count = sum(1 for check in checks if check.status == "pass")
    warning_count = sum(1 for check in checks if check.status == "warning")
    blocked_count = sum(1 for check in checks if check.status == "blocked")
    overall_status = "blocked" if blocked_count else "warning" if warning_count else "ready"
    return {
        "overall_status": overall_status,
        "ready_count": ready_count,
        "warning_count": warning_count,
        "blocked_count": blocked_count,
        "checks": [check.to_dict() for check in checks],
    }


def _market_mode_check(config: Mapping) -> PreflightCheck:
    mode = (config.get("MARKET_DATA_MODE") or "manual_only").strip().lower()
    if mode == "ifind":
        return _check("market_mode", "行情模式", "pass", "当前为 iFind 真实行情模式。", "保持 iFind 行情配置可用。", "market")
    if mode == "manual_only":
        return _check(
            "market_mode",
            "行情模式",
            "blocked",
            "当前为 manual_only 安全模式，不会自动请求真实行情。",
            "真实盯盘前请切换到 iFind 行情模式。",
            "market",
        )
    if mode == "simulated":
        return _check(
            "market_mode",
            "行情模式",
            "warning",
            "当前为模拟行情模式，不适合真实盯盘。",
            "真实盯盘前请切换到 iFind 行情模式。",
            "market",
        )
    if mode == "real":
        return _check(
            "market_mode",
            "行情模式",
            "blocked",
            "真实行情模式尚未配置实际 provider。",
            "真实盯盘前请使用 iFind 行情模式。",
            "market",
        )
    return _check(
        "market_mode",
        "行情模式",
        "blocked",
        f"未知行情模式 {mode}，系统会安全跳过自动行情。",
        "请检查行情模式配置。",
        "market",
    )


def _ifind_refresh_token_check(config: Mapping) -> PreflightCheck:
    if config.get("IFIND_REFRESH_TOKEN"):
        return _check("ifind_refresh_ready", "iFind 授权", "pass", "iFind refresh token 已配置。", "到期或权限更新后请及时重新获取。", "ifind")
    return _check(
        "ifind_refresh_ready",
        "iFind 授权",
        "blocked",
        "iFind refresh token 未配置，无法自动刷新 access token。",
        "请在本地安全配置 iFind refresh token。",
        "ifind",
    )


def _ifind_access_token_check(config: Mapping, runtime_status: Mapping) -> PreflightCheck:
    access_ready = bool(runtime_status.get("ifind_has_access_token") or config.get("IFIND_ACCESS_TOKEN"))
    auth_state = runtime_status.get("ifind_auth_state")
    if access_ready or auth_state == "access_token_ready":
        return _check("ifind_access_ready", "iFind 访问令牌", "pass", "iFind 访问令牌已就绪。", "继续观察授权刷新状态。", "ifind")
    if config.get("IFIND_REFRESH_TOKEN"):
        return _check(
            "ifind_access_ready",
            "iFind 访问令牌",
            "warning",
            "iFind 访问令牌尚未生成，等待 refresh token 换取访问令牌。",
            "启动后可先点击“测试 iFind 行情”确认授权刷新是否正常。",
            "ifind",
        )
    status = "blocked" if (config.get("MARKET_DATA_MODE") or "").strip().lower() == "ifind" else "warning"
    return _check(
        "ifind_access_ready",
        "iFind 访问令牌",
        status,
        "iFind 访问令牌未就绪。",
        "真实盯盘前请确认 iFind 授权可正常换取访问令牌。",
        "ifind",
    )


def _ifind_permission_check(ifind_permission_status) -> PreflightCheck:
    status = getattr(ifind_permission_status, "status", "unknown")
    message = getattr(ifind_permission_status, "message", "iFind 权限更新状态未知。")
    if status == "ok":
        return _check("ifind_permission", "iFind 权限更新", "pass", message, "按月关注账号权限更新日。", "permission")
    if status in {"expiring_soon", "renewal_day", "renewal_grace"}:
        return _check(
            "ifind_permission",
            "iFind 权限更新",
            "warning",
            "iFind 权限即将更新或刚更新，请确认 refresh token 是否仍有效。",
            "权限更新日前后建议重新验证 iFind 授权。",
            "permission",
        )
    return _check("ifind_permission", "iFind 权限更新", "warning", message, "请检查 iFind 权限提醒配置。", "permission")


def _auto_monitoring_check(runtime_status: Mapping) -> PreflightCheck:
    if not runtime_status.get("auto_monitoring_enabled"):
        return _check("auto_monitoring", "自动监控", "blocked", "自动监控已关闭。", "真实盯盘前请开启自动监控。", "monitor")
    if runtime_status.get("auto_monitoring_paused"):
        return _check("auto_monitoring", "自动监控", "blocked", "自动监控已暂停。", "真实盯盘前请恢复自动监控。", "monitor")
    return _check("auto_monitoring", "自动监控", "pass", "自动监控已启用。", "确认行情模式和通知模式也已准备好。", "monitor")


def _tasks_check(enabled_tasks_count: int) -> PreflightCheck:
    if enabled_tasks_count > 0:
        return _check("enabled_tasks", "监控任务", "pass", f"当前有 {enabled_tasks_count} 个启用中的监控任务。", "真实盯盘前请复核每个任务的基准价和网格间距。", "tasks")
    return _check("enabled_tasks", "监控任务", "blocked", "当前没有启用中的监控任务。", "请至少新增并启用一个监控任务。", "tasks")


def _notification_mode_check(config: Mapping, notification_channel_status) -> PreflightCheck:
    mode = (config.get("NOTIFICATION_MODE") or "dry_run").strip().lower()
    if mode == "feishu":
        if notification_channel_status.is_configured:
            return _check("notification_mode", "通知模式", "pass", "飞书真实通知已启用。", "真实盯盘前建议先手动触发一次测试报警。", "notification")
        return _check("notification_mode", "通知模式", "blocked", "飞书 webhook 未配置，无法发送真实通知。", "请安全配置飞书 webhook 后再真实盯盘。", "notification")
    if mode == "dry_run":
        return _check("notification_mode", "通知模式", "warning", "当前只生成通知预览，不会发送真实通知。", "真实盯盘前请切换到飞书真实通知模式。", "notification")
    if mode == "off":
        return _check("notification_mode", "通知模式", "blocked", "通知已关闭。", "真实盯盘前请启用通知。", "notification")
    if mode == "feishu_placeholder":
        return _check("notification_mode", "通知模式", "warning", "飞书仍为 placeholder，不会真实发送。", "真实盯盘前请切换到飞书真实通知模式。", "notification")
    if mode == "wecom_placeholder":
        return _check("notification_mode", "通知模式", "warning", "企业微信仍为 placeholder，不会真实发送。", "当前阶段请使用飞书真实通知或保持 Dry Run。", "notification")
    return _check("notification_mode", "通知模式", "blocked", f"通知模式 {mode} 尚未支持。", "请使用已支持的通知模式。", "notification")


def _notification_diagnostics_check(runtime_status: Mapping) -> PreflightCheck:
    delivery_status = runtime_status.get("last_notification_delivery_status")
    if runtime_status.get("last_notification_success") is True:
        return _check("notification_diagnostics", "通知诊断", "pass", "最近通知发送成功。", "继续观察通知送达情况。", "notification")
    if runtime_status.get("last_notification_can_retry"):
        summary = runtime_status.get("last_notification_safe_error_summary") or "最近通知发送失败。"
        return _check("notification_diagnostics", "通知诊断", "warning", summary, "请修复通知配置或网络后重试。", "notification")
    if delivery_status:
        return _check("notification_diagnostics", "通知诊断", "warning", f"最近通知状态：{delivery_status}。", "真实盯盘前建议确认一次真实通知。", "notification")
    return _check("notification_diagnostics", "通知诊断", "warning", "尚无真实通知发送记录。", "建议真实盯盘前先手动测试一次通知链路。", "notification")


def _burst_check(config: Mapping) -> PreflightCheck:
    burst_config = parse_burst_config(config)
    if burst_config.enabled:
        return _check("burst_protection", "高频报警保护", "pass", "高频报警提示已启用。", "保持窗口和阈值符合你的夜盘监控节奏。", "burst")
    return _check("burst_protection", "高频报警保护", "warning", "高频报警提示关闭。", "建议真实盯盘前开启高频报警提示。", "burst")


def _security_check() -> PreflightCheck:
    return _check("sensitive_info", "敏感信息安全", "pass", "运行前检查不会显示 token、webhook 或 secret 原文。", "继续避免在日志和截图中暴露敏感配置。", "security")


def _check(key: str, title: str, status: str, message: str, suggestion: str, category: str) -> PreflightCheck:
    return PreflightCheck(
        key=key,
        title=title,
        status=status,
        message=_sanitize(message),
        suggestion=_sanitize(suggestion),
        category=category,
    )


def _sanitize(text: str) -> str:
    forbidden_terms = [
        "IFIND_REFRESH_TOKEN",
        "IFIND_ACCESS_TOKEN",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_WEBHOOK_SECRET",
        "WECOM_WEBHOOK_URL",
        "Bearer",
        "headers",
        ".env",
    ]
    sanitized = str(text)
    for term in forbidden_terms:
        sanitized = sanitized.replace(term, "[已隐藏]")
    return sanitized
