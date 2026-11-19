from dataclasses import dataclass
from typing import Mapping


DEFAULT_BURST_WINDOW_SECONDS = 300
DEFAULT_BURST_THRESHOLD = 3


@dataclass(frozen=True)
class BurstConfig:
    enabled: bool
    window_seconds: int
    threshold: int


def parse_burst_config(config: Mapping | None = None) -> BurstConfig:
    config = config or {}
    return BurstConfig(
        enabled=_parse_bool(config.get("NOTIFICATION_BURST_ENABLED", True), True),
        window_seconds=_parse_positive_int(
            config.get("NOTIFICATION_BURST_WINDOW_SECONDS", DEFAULT_BURST_WINDOW_SECONDS),
            DEFAULT_BURST_WINDOW_SECONDS,
        ),
        threshold=_parse_positive_int(
            config.get("NOTIFICATION_BURST_THRESHOLD", DEFAULT_BURST_THRESHOLD),
            DEFAULT_BURST_THRESHOLD,
        ),
    )


def build_burst_context(alert_count: int, config: Mapping | None = None) -> dict:
    burst_config = parse_burst_config(config)
    detected = bool(burst_config.enabled and alert_count >= burst_config.threshold)
    return {
        "burst_alert_enabled": burst_config.enabled,
        "burst_alert_detected": detected,
        "burst_alert_count": alert_count,
        "burst_alert_window_seconds": burst_config.window_seconds,
        "burst_alert_threshold": burst_config.threshold,
        "burst_alert_message": (
            format_burst_alert_message(alert_count, burst_config.window_seconds)
            if detected
            else None
        ),
    }


def format_burst_alert_message(alert_count: int, window_seconds: int) -> str:
    return (
        f"⚠️ 高频报警：最近 {_window_label(window_seconds)}内该合约已触发 "
        f"{alert_count} 次报警，请重点关注。"
    )


def _window_label(window_seconds: int) -> str:
    if window_seconds % 60 == 0:
        return f"{window_seconds // 60} 分钟"
    return f"{window_seconds} 秒"


def _parse_positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
