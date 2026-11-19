from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import time
from typing import Mapping

import requests

from app.monitor.status import record_notification


@dataclass(frozen=True)
class NotificationResult:
    success: bool
    mode: str
    status: str
    message: str
    content: str | None = None
    error_message: str | None = None
    channel: str | None = None
    sent_at: datetime | None = None
    duration_ms: int | None = None
    attempt_count: int = 1
    safe_error_summary: str | None = None
    can_retry: bool = False
    delivery_status: str | None = None
    http_status_code: int | None = None
    provider_error_code: str | None = None

    @property
    def enabled(self) -> bool:
        return self.success


@dataclass(frozen=True)
class NotificationChannelStatus:
    notification_mode: str
    channel: str
    label: str
    is_configured: bool
    is_placeholder: bool
    can_send_real_message: bool
    message: str
    error: str | None = None


class BaseNotifier(ABC):
    def notify_alert(self, alert_context: dict) -> NotificationResult:
        return self.send(alert_context)

    @abstractmethod
    def send(self, alert_context: dict) -> NotificationResult:
        """Generate or send an alert notification."""


class OffNotifier(BaseNotifier):
    mode = "off"

    def send(self, alert_context: dict) -> NotificationResult:
        result = NotificationResult(
            success=False,
            mode=self.mode,
            status="disabled",
            message="未启用",
            channel="off",
            sent_at=utc_now(),
            duration_ms=0,
            delivery_status="disabled",
        )
        record_notification_result(result)
        return result


class DryRunNotifier(BaseNotifier):
    mode = "dry_run"

    def send(self, alert_context: dict) -> NotificationResult:
        content = alert_context.get("notification_content") or build_alert_notification(alert_context)
        result = NotificationResult(
            success=True,
            mode=self.mode,
            status="dry_run_generated",
            message="Dry Run 已生成",
            content=content,
            channel="dry_run",
            sent_at=utc_now(),
            duration_ms=0,
            delivery_status="dry_run",
        )
        record_notification_result(result)
        return result


class FeishuPlaceholderNotifier(BaseNotifier):
    mode = "feishu_placeholder"

    def __init__(self, config: Mapping | None = None):
        self.config = config or {}

    def send(self, alert_context: dict) -> NotificationResult:
        status = get_notification_channel_status(self.mode, self.config)
        result = NotificationResult(
            success=status.is_configured,
            mode=self.mode,
            status="configured_placeholder" if status.is_configured else "missing_config",
            message=status.message,
            channel="feishu",
            sent_at=utc_now(),
            duration_ms=0,
            safe_error_summary=None if status.is_configured else "飞书 webhook 未配置",
            can_retry=False,
            delivery_status="configured_placeholder" if status.is_configured else "missing_config",
        )
        record_notification_result(result)
        return result


class WeComPlaceholderNotifier(BaseNotifier):
    mode = "wecom_placeholder"

    def __init__(self, config: Mapping | None = None):
        self.config = config or {}

    def send(self, alert_context: dict) -> NotificationResult:
        status = get_notification_channel_status(self.mode, self.config)
        result = NotificationResult(
            success=status.is_configured,
            mode=self.mode,
            status="configured_placeholder" if status.is_configured else "missing_config",
            message=status.message,
            channel="wecom",
            sent_at=utc_now(),
            duration_ms=0,
            safe_error_summary=None if status.is_configured else "企业微信 webhook 未配置",
            can_retry=False,
            delivery_status="configured_placeholder" if status.is_configured else "missing_config",
        )
        record_notification_result(result)
        return result


class FeishuNotifier(BaseNotifier):
    mode = "feishu"

    def __init__(self, config: Mapping | None = None, http_client=None, timestamp_func=None):
        self.config = config or {}
        self.http_client = http_client or requests
        self.timestamp_func = timestamp_func or time.time

    def send(self, alert_context: dict) -> NotificationResult:
        started_at = time.perf_counter()
        webhook_url = self.config.get("FEISHU_WEBHOOK_URL", "")
        if not webhook_url:
            result = NotificationResult(
                success=False,
                mode=self.mode,
                status="missing_config",
                message="飞书 webhook 未配置，未发送通知。",
                channel="feishu",
                sent_at=utc_now(),
                duration_ms=duration_ms(started_at),
                safe_error_summary="飞书 webhook 未配置",
                can_retry=False,
                delivery_status="missing_config",
            )
            record_notification_result(result)
            return result

        content = alert_context.get("notification_content") or build_alert_notification(alert_context)
        payload = build_feishu_payload(
            content,
            secret=self.config.get("FEISHU_WEBHOOK_SECRET", ""),
            timestamp_func=self.timestamp_func,
        )
        try:
            response = self.http_client.post(
                webhook_url,
                json=payload,
                timeout=int(self.config.get("FEISHU_REQUEST_TIMEOUT_SECONDS", 10)),
            )
        except Exception as exc:
            result = NotificationResult(
                success=False,
                mode=self.mode,
                status="request_error",
                message="飞书通知发送失败。",
                content=content,
                error_message=f"飞书通知请求失败：{exc.__class__.__name__}",
                channel="feishu",
                sent_at=utc_now(),
                duration_ms=duration_ms(started_at),
                safe_error_summary=f"飞书通知请求异常：{exc.__class__.__name__}",
                can_retry=is_retryable_notification_error(exception=exc),
                delivery_status="request_error",
            )
            record_notification_result(result)
            return result

        if response.status_code < 200 or response.status_code >= 300:
            result = NotificationResult(
                success=False,
                mode=self.mode,
                status="http_error",
                message="飞书通知发送失败。",
                content=content,
                error_message=f"飞书通知请求失败，HTTP 状态码 {response.status_code}",
                channel="feishu",
                sent_at=utc_now(),
                duration_ms=duration_ms(started_at),
                safe_error_summary=f"飞书 webhook 请求失败，HTTP {response.status_code}",
                can_retry=is_retryable_notification_error(http_status_code=response.status_code),
                delivery_status="http_error",
                http_status_code=response.status_code,
            )
            record_notification_result(result)
            return result

        feishu_error = _feishu_business_error(
            response,
            sensitive_values=(webhook_url, self.config.get("FEISHU_WEBHOOK_SECRET", "")),
        )
        if feishu_error:
            error_message, provider_error_code = feishu_error
            result = NotificationResult(
                success=False,
                mode=self.mode,
                status="feishu_error",
                message="飞书通知发送失败。",
                content=content,
                error_message=error_message,
                channel="feishu",
                sent_at=utc_now(),
                duration_ms=duration_ms(started_at),
                safe_error_summary=error_message,
                # Keep the notifier's diagnostic posture conservative for
                # unknown Feishu business failures. The persistent delivery
                # layer performs the stricter retry classification.
                can_retry=True,
                delivery_status="feishu_error",
                provider_error_code=provider_error_code,
            )
            record_notification_result(result)
            return result

        result = NotificationResult(
            success=True,
            mode=self.mode,
            status="sent",
            message="飞书通知发送成功。",
            content=content,
            channel="feishu",
            sent_at=utc_now(),
            duration_ms=duration_ms(started_at),
            safe_error_summary=None,
            can_retry=False,
            delivery_status="sent",
        )
        record_notification_result(result)
        return result


class UnsupportedNotifier(BaseNotifier):
    def __init__(self, mode: str):
        self.mode = mode

    def send(self, alert_context: dict) -> NotificationResult:
        result = NotificationResult(
            success=False,
            mode=self.mode,
            status="unsupported",
            message=f"通知模式 {self.mode} 尚未支持，已安全禁用。",
            channel="unsupported",
            sent_at=utc_now(),
            duration_ms=0,
            safe_error_summary="通知模式未支持",
            can_retry=False,
            delivery_status="unsupported",
        )
        record_notification_result(result)
        return result


def get_notifier(mode: str | None, config: Mapping | None = None) -> BaseNotifier:
    normalized_mode = (mode or "dry_run").strip().lower()
    if normalized_mode == "off":
        return OffNotifier()
    if normalized_mode == "dry_run":
        return DryRunNotifier()
    if normalized_mode == "feishu_placeholder":
        return FeishuPlaceholderNotifier(config)
    if normalized_mode == "wecom_placeholder":
        return WeComPlaceholderNotifier(config)
    if normalized_mode == "feishu":
        return FeishuNotifier(config)
    return UnsupportedNotifier(normalized_mode)


def get_notification_channel_status(mode: str | None, config: Mapping | None = None) -> NotificationChannelStatus:
    config = config or {}
    normalized_mode = (mode or "dry_run").strip().lower()

    if normalized_mode == "off":
        return NotificationChannelStatus(
            notification_mode=normalized_mode,
            channel="off",
            label="关闭",
            is_configured=False,
            is_placeholder=False,
            can_send_real_message=False,
            message="通知已关闭。",
        )

    if normalized_mode == "dry_run":
        return NotificationChannelStatus(
            notification_mode=normalized_mode,
            channel="dry_run",
            label="Dry Run",
            is_configured=True,
            is_placeholder=False,
            can_send_real_message=False,
            message="当前只生成通知预览，不发送外部通知。",
        )

    if normalized_mode == "feishu_placeholder":
        configured = bool(config.get("FEISHU_WEBHOOK_URL"))
        return NotificationChannelStatus(
            notification_mode=normalized_mode,
            channel="feishu",
            label="飞书",
            is_configured=configured,
            is_placeholder=True,
            can_send_real_message=False,
            message=(
                "飞书 webhook 已配置，但当前为 placeholder 模式，不发送真实通知。"
                if configured
                else "飞书 webhook 未配置，当前不会发送真实通知。"
            ),
        )

    if normalized_mode == "feishu":
        configured = bool(config.get("FEISHU_WEBHOOK_URL"))
        return NotificationChannelStatus(
            notification_mode=normalized_mode,
            channel="feishu",
            label="飞书",
            is_configured=configured,
            is_placeholder=False,
            can_send_real_message=configured,
            message=(
                "飞书真实通知已启用。"
                if configured
                else "飞书 webhook 未配置，无法发送真实通知。"
            ),
        )

    if normalized_mode == "wecom_placeholder":
        configured = bool(config.get("WECOM_WEBHOOK_URL"))
        return NotificationChannelStatus(
            notification_mode=normalized_mode,
            channel="wecom",
            label="企业微信",
            is_configured=configured,
            is_placeholder=True,
            can_send_real_message=False,
            message=(
                "企业微信 webhook 已配置，但当前为 placeholder 模式，不发送真实通知。"
                if configured
                else "企业微信 webhook 未配置，当前不会发送真实通知。"
            ),
        )

    return NotificationChannelStatus(
        notification_mode=normalized_mode,
        channel="unsupported",
        label="未支持",
        is_configured=False,
        is_placeholder=False,
        can_send_real_message=False,
        message=f"通知模式 {normalized_mode} 尚未支持，已安全禁用。",
        error="unsupported_mode",
    )


def record_notification_result(result: NotificationResult) -> None:
    record_notification(
        mode=result.mode,
        status=result.message,
        content=result.content,
        success=result.success,
        error_message=result.error_message,
        channel=result.channel,
        delivery_status=result.delivery_status or result.status,
        sent_at=result.sent_at,
        duration_ms=result.duration_ms,
        safe_error_summary=result.safe_error_summary,
        can_retry=result.can_retry,
        attempt_count=result.attempt_count,
    )


def build_alert_notification(alert_context: dict) -> str:
    direction_label = "上涨" if alert_context["direction"] == "UP" else "下跌"
    lines = [
        "【补发网格监控报警】" if alert_context.get("is_retry") else "【网格监控报警】",
        *( [f"报警编号：{alert_context['alert_number']}"] if alert_context.get("alert_number") else [] ),
        *( [f"原报警时间：{alert_context['original_alert_time']}"] if alert_context.get("is_retry") and alert_context.get("original_alert_time") else [] ),
        f"合约：{alert_context['code']}",
        f"方向：{direction_label}",
        f"当前价格：{alert_context['current_price']:.2f}",
        f"旧基准价：{alert_context['old_base_price']:.2f}",
        f"新基准价：{alert_context['new_base_price']:.2f}",
        f"突破网格数：{alert_context['grid_steps']}",
        f"阈值：{alert_context['threshold']:.2f}",
    ]
    volatility_warning = build_volatility_warning(alert_context["grid_steps"])
    if volatility_warning:
        lines.append(volatility_warning)
    burst_warning = alert_context.get("burst_alert_message")
    if burst_warning:
        lines.append(burst_warning)
    lines.extend(
        [
            f"行情来源：{alert_context.get('source_label') or alert_context.get('source') or '未知'}",
            f"时间：{alert_context['created_at']}",
        ]
    )
    return "\n".join(lines)


def build_feishu_payload(content: str, secret: str = "", timestamp_func=None) -> dict:
    payload = {
        "msg_type": "text",
        "content": {"text": content},
    }
    if secret:
        timestamp = str(int((timestamp_func or time.time)()))
        payload["timestamp"] = timestamp
        payload["sign"] = sign_feishu_message(timestamp, secret)
    return payload


def sign_feishu_message(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        b"",
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _feishu_business_error(response, sensitive_values=()) -> tuple[str, str] | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    code = payload.get("code", 0)
    if code in {0, "0", None}:
        return None

    message = str(payload.get("msg") or payload.get("message") or "飞书返回业务错误")
    return (
        f"飞书通知返回错误，code={code}，message={_safe_feishu_message(message, sensitive_values)}",
        str(code),
    )


def is_retryable_notification_error(
    *,
    http_status_code: int | None = None,
    provider_error_code: str | None = None,
    exception: Exception | None = None,
) -> bool:
    """Classify transient Feishu failures without relying on request contents."""
    if http_status_code is not None:
        return http_status_code == 429 or http_status_code >= 500
    if provider_error_code is not None:
        # 19006 is Feishu's transient internal-service error. Unknown business
        # errors are treated as permanent instead of repeatedly sending a bad payload.
        return str(provider_error_code) == "19006"
    if exception is not None:
        return isinstance(
            exception,
            (requests.exceptions.Timeout, requests.exceptions.ConnectionError, TimeoutError, ConnectionError, OSError),
        )
    return False


def sanitize_notification_error(message: str | None, sensitive_values=()) -> str | None:
    if not message:
        return None
    return _safe_feishu_message(str(message), sensitive_values)


def _safe_feishu_message(message: str, sensitive_values=()) -> str:
    sanitized = message
    forbidden_terms = [
        "access_token",
        "refresh_token",
        "Bearer",
        "IFIND_REFRESH_TOKEN",
        "IFIND_ACCESS_TOKEN",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_WEBHOOK_SECRET",
        "WECOM_WEBHOOK_URL",
        ".env",
        "headers",
    ]
    for term in forbidden_terms:
        sanitized = sanitized.replace(term, "[已隐藏]")
    for value in sensitive_values:
        if value:
            sanitized = sanitized.replace(str(value), "[已隐藏]")
    return sanitized[:120]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def duration_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def build_volatility_warning(grid_steps: int) -> str | None:
    if grid_steps >= 3:
        return f"🚨 强烈波动：本次一次性突破 {grid_steps} 个网格"
    if grid_steps == 2:
        return f"⚠️ 剧烈波动：本次一次性突破 {grid_steps} 个网格"
    return None
