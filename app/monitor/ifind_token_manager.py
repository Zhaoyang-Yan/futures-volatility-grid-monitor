from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from weakref import WeakSet

import requests


DEFAULT_TOKEN_LIFETIME = timedelta(days=7)
_TOKEN_MANAGERS = WeakSet()


@dataclass(frozen=True)
class IfindTokenStatus:
    configured: bool
    has_refresh_token: bool
    has_access_token: bool
    access_token_expires_at: datetime | None
    last_refresh_at: datetime | None
    last_refresh_success: bool
    last_error: str | None
    auth_state: str


class IfindTokenManager:
    def __init__(
        self,
        base_url: str,
        refresh_token: str = "",
        access_token: str = "",
        refresh_margin_seconds: int = 3600,
        http_client=None,
        timeout_seconds: int = 10,
    ) -> None:
        self.base_url = (base_url or "https://quantapi.51ifind.com/api/v1").rstrip("/")
        self.refresh_token = refresh_token or ""
        self._access_token = access_token or ""
        self.refresh_margin = timedelta(seconds=refresh_margin_seconds)
        self.http_client = http_client or requests
        self.timeout_seconds = timeout_seconds
        self.access_token_expires_at = self._default_expiry() if self._access_token else None
        self.last_refresh_at = None
        self.last_refresh_success = bool(self._access_token)
        self.last_error = None if self._access_token else self._initial_error()
        self.auth_state = "access_token_ready" if self._access_token else self._initial_auth_state()
        _TOKEN_MANAGERS.add(self)

    @classmethod
    def from_config(cls, config, http_client=None):
        return cls(
            base_url=config.get("IFIND_BASE_URL", ""),
            refresh_token=config.get("IFIND_REFRESH_TOKEN", ""),
            access_token=config.get("IFIND_ACCESS_TOKEN", ""),
            refresh_margin_seconds=config.get("IFIND_TOKEN_REFRESH_MARGIN_SECONDS", 3600),
            http_client=http_client,
        )

    def get_valid_access_token(self) -> str | None:
        if self._access_token and not self._should_refresh():
            self.auth_state = "access_token_ready"
            self.last_error = None
            return self._access_token

        if not self.refresh_token:
            self._mark_not_configured()
            return None

        return self._refresh_access_token()

    def get_status(self) -> IfindTokenStatus:
        return IfindTokenStatus(
            configured=bool(self.base_url and self.refresh_token),
            has_refresh_token=bool(self.refresh_token),
            has_access_token=bool(self._access_token),
            access_token_expires_at=self.access_token_expires_at,
            last_refresh_at=self.last_refresh_at,
            last_refresh_success=self.last_refresh_success,
            last_error=self.last_error,
            auth_state=self.auth_state,
        )

    def clear(self) -> None:
        self._access_token = ""
        self.access_token_expires_at = None
        self.last_refresh_at = None
        self.last_refresh_success = False
        self.last_error = self._initial_error()
        self.auth_state = self._initial_auth_state()

    reset = clear

    def update_refresh_token(self, refresh_token: str) -> None:
        self.refresh_token = refresh_token or ""
        self.clear()

    def _refresh_access_token(self) -> str | None:
        self.last_refresh_at = utc_now()
        try:
            response = self.http_client.get(
                self._access_token_url(),
                headers={"refresh_token": self.refresh_token},
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            self._mark_refresh_failed(f"iFind 授权请求失败：{exc.__class__.__name__}")
            return None

        if response.status_code in {401, 403}:
            self._mark_refresh_token_invalid()
            return None

        if response.status_code != 200:
            self._mark_refresh_failed(f"iFind 授权请求失败，HTTP 状态码 {response.status_code}")
            return None

        try:
            payload = response.json()
        except ValueError:
            self._mark_refresh_failed("iFind 授权响应不是有效 JSON")
            return None

        if self._is_invalid_token_payload(payload):
            self._mark_refresh_token_invalid()
            return None

        token_payload = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
        if not access_token:
            self._mark_refresh_failed("iFind 授权响应缺少访问令牌")
            return None

        self._access_token = access_token
        self.access_token_expires_at = self._parse_expiry(token_payload) or self._default_expiry()
        self.last_refresh_success = True
        self.last_error = None
        self.auth_state = "access_token_ready"
        return self._access_token

    def _access_token_url(self) -> str:
        return f"{self.base_url}/get_access_token"

    def _should_refresh(self) -> bool:
        if self.access_token_expires_at is None:
            return True
        return utc_now() + self.refresh_margin >= self.access_token_expires_at

    def _default_expiry(self) -> datetime:
        return utc_now() + DEFAULT_TOKEN_LIFETIME - self.refresh_margin

    def _initial_error(self) -> str | None:
        if self.refresh_token:
            return None
        return "iFind refresh token 未配置"

    def _initial_auth_state(self) -> str:
        if self.refresh_token:
            return "unknown"
        return "not_configured"

    def _mark_not_configured(self) -> None:
        self.last_refresh_success = False
        self.last_error = "iFind refresh token 未配置"
        self.auth_state = "not_configured"

    def _mark_refresh_failed(self, message: str) -> None:
        self.last_refresh_success = False
        self.last_error = self._with_permission_hint(message)
        self.auth_state = "refresh_failed"

    def _mark_refresh_token_invalid(self) -> None:
        self.last_refresh_success = False
        self.last_error = self._with_permission_hint("iFind refresh token 无效或已过期")
        self.auth_state = "refresh_token_invalid"

    @staticmethod
    def _with_permission_hint(message: str) -> str:
        return (
            f"{message}。iFind 授权失败：refresh token 可能已失效、"
            "账号权限已到期或权限已更新，请重新获取 refresh token。"
        )

    @staticmethod
    def _is_invalid_token_payload(payload) -> bool:
        if not isinstance(payload, dict):
            return False
        message = str(payload.get("message") or payload.get("error") or payload.get("error_message") or "")
        code = str(payload.get("code") or payload.get("error_code") or "")
        combined = f"{code} {message}".lower()
        return any(keyword in combined for keyword in ("invalid", "expired", "过期", "无效"))

    @staticmethod
    def _parse_expiry(payload: dict) -> datetime | None:
        for key in ("expire_time", "expires_at", "expiration"):
            value = payload.get(key)
            parsed = parse_datetime(value)
            if parsed is not None:
                return parsed

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            return utc_now() + timedelta(seconds=int(expires_in))
        return None


def parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        parsed = _parse_datetime_string(value)
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_datetime_string(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(normalized)
    except (TypeError, ValueError):
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def reset_ifind_token_managers(refresh_token: str | None = None) -> None:
    for manager in list(_TOKEN_MANAGERS):
        if refresh_token is None:
            manager.clear()
        else:
            manager.update_refresh_token(refresh_token)
