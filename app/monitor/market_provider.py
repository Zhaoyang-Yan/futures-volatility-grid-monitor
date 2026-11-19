import logging
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

import requests

from app.monitor.ifind_token_manager import IfindTokenManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketPrice:
    code: str
    current_price: float | None
    timestamp: datetime | None
    source: str
    success: bool
    error_message: str | None = None
    raw: dict | None = None


class MarketDataProvider(ABC):
    auto_monitoring_enabled = True
    provider_status = "unknown"

    @abstractmethod
    def get_price(
        self,
        code: str,
        base_price: float | None = None,
        threshold: float | None = None,
    ) -> MarketPrice:
        """Return the latest price for a code without modifying application state."""

    def get_prices(self, codes: list[str]) -> dict[str, MarketPrice]:
        return {code: self.get_price(code) for code in codes}


class SimulatedMarketDataProvider(MarketDataProvider):
    provider_status = "simulated"

    def get_price(
        self,
        code: str,
        base_price: float | None = None,
        threshold: float | None = None,
    ) -> MarketPrice:
        if base_price is None:
            return MarketPrice(
                code=code,
                current_price=None,
                timestamp=utc_now(),
                source="simulated",
                success=False,
                error_message="模拟行情需要 base_price。",
            )

        threshold_offset = abs(threshold or 0) * 1.5
        max_offset = max(threshold_offset, base_price * 0.003, 1.0)
        offset = random.uniform(-max_offset, max_offset)
        return MarketPrice(
            code=code,
            current_price=round(base_price + offset, 2),
            timestamp=utc_now(),
            source="simulated",
            success=True,
        )


class IfindMarketDataProvider(MarketDataProvider):
    auto_monitoring_enabled = True
    batch_enabled = True
    provider_status = "real_not_configured"

    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        refresh_margin_seconds: int = 3600,
        realtime_endpoint: str = "/real_time_quotation",
        realtime_indicators: str = "latest",
        request_timeout_seconds: int = 10,
        code_suffix: str = "",
        code_map: str | Mapping | None = None,
        exchange_suffix_map: str | Mapping | None = None,
        http_client=None,
        token_manager: IfindTokenManager | None = None,
    ) -> None:
        self.token_manager = token_manager or IfindTokenManager(
            base_url=base_url or "",
            access_token=access_token or "",
            refresh_token=refresh_token or "",
            refresh_margin_seconds=refresh_margin_seconds,
        )
        self.http_client = http_client or requests
        self.realtime_endpoint = realtime_endpoint or "/real_time_quotation"
        self.realtime_indicators = realtime_indicators or "latest"
        self.request_timeout_seconds = request_timeout_seconds
        self.code_suffix = code_suffix or ""
        self.code_map = parse_code_map(code_map)
        self.exchange_suffix_map = parse_code_map(exchange_suffix_map)
        token_status = self.token_manager.get_status()
        if token_status.configured or token_status.has_access_token:
            self.provider_status = "ifind"

    @property
    def is_configured(self) -> bool:
        return self.token_manager.get_status().configured

    def get_price(
        self,
        code: str,
        base_price: float | None = None,
        threshold: float | None = None,
    ) -> MarketPrice:
        access_token = self.token_manager.get_valid_access_token()
        if not access_token:
            status = self.token_manager.get_status()
            return MarketPrice(
                code=code,
                current_price=None,
                timestamp=utc_now(),
                source="ifind",
                success=False,
                error_message=status.last_error or "iFind 授权失败",
            )

        ifind_code = self.normalize_code(code)
        payload = {
            "codes": ifind_code,
            "indicators": self.realtime_indicators,
        }
        try:
            response = self.http_client.post(
                self._realtime_url(),
                headers={
                    "Content-Type": "application/json",
                    "access_token": access_token,
                },
                json=payload,
                timeout=self.request_timeout_seconds,
            )
        except requests.Timeout:
            return self._failed_price(code, ifind_code, "iFind 实时行情请求超时")
        except Exception:
            return self._failed_price(code, ifind_code, "iFind 实时行情请求失败")

        if response.status_code in {401, 403}:
            self.token_manager.clear()
            return self._failed_price(code, ifind_code, "iFind access token 失效，等待下次刷新")

        if response.status_code != 200:
            return self._failed_price(code, ifind_code, f"iFind 实时行情请求失败，HTTP 状态码 {response.status_code}")

        try:
            response_payload = response.json()
        except ValueError:
            return self._failed_price(code, ifind_code, "iFind 实时行情返回不是有效 JSON")

        business_error = extract_business_error(response_payload)
        if business_error:
            if is_auth_error_message(business_error):
                self.token_manager.clear()
            return self._failed_price(code, ifind_code, business_error, response_payload)

        current_price = parse_realtime_price(response_payload, ifind_code)
        if current_price is None:
            return self._failed_price(code, ifind_code, "iFind 实时行情返回中未找到 latest 价格", response_payload)

        return MarketPrice(
            code=code,
            current_price=current_price,
            timestamp=utc_now(),
            source="ifind",
            success=True,
            raw={"ifind_code": ifind_code, "payload": response_payload},
        )

    def get_prices(self, codes: list[str]) -> dict[str, MarketPrice]:
        unique_codes = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
        if not unique_codes:
            return {}

        access_token = self.token_manager.get_valid_access_token()
        if not access_token:
            status = self.token_manager.get_status()
            return {
                code: self._failed_price(
                    code,
                    self.normalize_code(code),
                    status.last_error or "iFind 授权失败",
                )
                for code in unique_codes
            }

        resolved_codes = {code: self.normalize_code(code) for code in unique_codes}
        payload = {
            "codes": ",".join(resolved_codes.values()),
            "indicators": self.realtime_indicators,
        }
        try:
            response = self.http_client.post(
                self._realtime_url(),
                headers={
                    "Content-Type": "application/json",
                    "access_token": access_token,
                },
                json=payload,
                timeout=self.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise RuntimeError("iFind 实时行情请求超时") from exc
        except Exception as exc:
            raise RuntimeError("iFind 实时行情请求失败") from exc

        if response.status_code in {401, 403}:
            self.token_manager.clear()
            return {
                code: self._failed_price(code, resolved_codes[code], "iFind access token 失效，等待下次刷新")
                for code in unique_codes
            }

        if response.status_code != 200:
            raise RuntimeError(f"iFind 实时行情请求失败，HTTP 状态码 {response.status_code}")

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise RuntimeError("iFind 实时行情返回不是有效 JSON") from exc

        business_error = extract_business_error(response_payload)
        if business_error:
            if is_auth_error_message(business_error):
                self.token_manager.clear()
            raise RuntimeError(business_error)

        parsed_prices = parse_realtime_prices(response_payload, list(resolved_codes.values()))
        results = {}
        for code, ifind_code in resolved_codes.items():
            current_price = parsed_prices.get(ifind_code)
            if current_price is None:
                continue
            results[code] = MarketPrice(
                code=code,
                current_price=current_price,
                timestamp=utc_now(),
                source="ifind",
                success=True,
                raw={"ifind_code": ifind_code, "payload": response_payload},
            )
        return results

    def normalize_code(self, code: str) -> str:
        return resolve_ifind_code(
            code,
            code_map=self.code_map,
            exchange_suffix_map=self.exchange_suffix_map,
            code_suffix=self.code_suffix,
        )

    def _realtime_url(self) -> str:
        endpoint = self.realtime_endpoint
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return f"{self.token_manager.base_url}{endpoint}"

    @staticmethod
    def _failed_price(
        code: str,
        ifind_code: str,
        error_message: str,
        payload: dict | None = None,
    ) -> MarketPrice:
        raw = {"ifind_code": ifind_code}
        if payload is not None:
            raw["payload"] = payload
        return MarketPrice(
            code=code,
            current_price=None,
            timestamp=utc_now(),
            source="ifind",
            success=False,
            error_message=error_message,
            raw=raw,
        )


def get_market_data_provider(mode: str, config: Mapping | None = None) -> MarketDataProvider | None:
    normalized_mode = (mode or "manual_only").strip().lower()

    if normalized_mode == "simulated":
        return SimulatedMarketDataProvider()

    if normalized_mode == "manual_only":
        return None

    if normalized_mode in {"real", "ifind"}:
        config = config or {}
        return IfindMarketDataProvider(
            base_url=config.get("IFIND_BASE_URL"),
            access_token=config.get("IFIND_ACCESS_TOKEN"),
            refresh_token=config.get("IFIND_REFRESH_TOKEN"),
            refresh_margin_seconds=config.get("IFIND_TOKEN_REFRESH_MARGIN_SECONDS", 3600),
            realtime_endpoint=config.get("IFIND_REALTIME_ENDPOINT", "/real_time_quotation"),
            realtime_indicators=config.get("IFIND_REALTIME_INDICATORS", "latest"),
            request_timeout_seconds=config.get("IFIND_REQUEST_TIMEOUT_SECONDS", 10),
            code_suffix=config.get("IFIND_CODE_SUFFIX", ""),
            code_map=config.get("IFIND_CODE_MAP", ""),
            exchange_suffix_map=config.get("IFIND_EXCHANGE_SUFFIX_MAP", ""),
        )

    logger.warning("未知行情源模式 %s，已回退到手动测试模式。", mode)
    return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_code_map(code_map: str | Mapping | None) -> dict[str, str]:
    if isinstance(code_map, Mapping):
        return {str(key).strip(): str(value).strip() for key, value in code_map.items() if str(key).strip()}
    if not code_map:
        return {}

    mappings = {}
    for item in re.split(r"[,;\n]+", str(code_map)):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            mappings[key] = value
    return mappings


def resolve_ifind_code(
    contract_code: str,
    code_map: str | Mapping | None = None,
    exchange_suffix_map: str | Mapping | None = None,
    code_suffix: str = "",
) -> str:
    normalized_code = str(contract_code or "").strip()
    if "." in normalized_code:
        return normalized_code

    exact_map = parse_code_map(code_map)
    if normalized_code in exact_map:
        return exact_map[normalized_code]

    upper_code = normalized_code.upper()
    upper_exact_map = {key.upper(): value for key, value in exact_map.items()}
    if upper_code in upper_exact_map:
        return upper_exact_map[upper_code]

    suffix_map = parse_code_map(exchange_suffix_map)
    for prefix, suffix in sorted(suffix_map.items(), key=lambda item: len(item[0]), reverse=True):
        if prefix and upper_code.startswith(prefix.upper()):
            return f"{normalized_code}{suffix}"

    if code_suffix:
        return f"{normalized_code}{str(code_suffix).strip()}"
    return normalized_code


def parse_realtime_price(payload: dict, requested_code: str) -> float | None:
    for value in iter_latest_values(payload):
        parsed = to_float(value)
        if parsed is not None:
            return parsed
    return None


def parse_realtime_prices(payload: dict, requested_codes: list[str]) -> dict[str, float]:
    requested = [str(code) for code in requested_codes]
    requested_set = set(requested)
    prices = {}
    for code, value in iter_code_latest_pairs(payload):
        if code in requested_set:
            parsed = to_float(value)
            if parsed is not None:
                prices[code] = parsed

    if not prices and len(requested) == 1:
        single_price = parse_realtime_price(payload, requested[0])
        if single_price is not None:
            prices[requested[0]] = single_price

    return prices


def iter_code_latest_pairs(node):
    if isinstance(node, dict):
        yield from code_latest_pairs_from_table_like(node)
        for value in node.values():
            yield from iter_code_latest_pairs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_code_latest_pairs(item)


def code_latest_pairs_from_table_like(node: dict):
    columns = first_list_value(node, ("columns", "indicators", "fields", "field"))
    values = first_list_value(node, ("data", "value", "values"))
    if not columns or values is None:
        return

    normalized_columns = [str(column).lower() for column in columns]
    latest_index = _first_column_index(normalized_columns, ("latest",))
    code_index = _first_column_index(normalized_columns, ("thscode", "code", "securitycode", "contract_code"))
    if latest_index is None or code_index is None:
        return

    for row in values:
        if not isinstance(row, (list, tuple)):
            continue
        if len(row) <= max(latest_index, code_index):
            continue
        yield str(row[code_index]), row[latest_index]


def _first_column_index(columns: list[str], candidates: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        if candidate in columns:
            return columns.index(candidate)
    return None


def iter_latest_values(node):
    if isinstance(node, dict):
        table_value = latest_from_table_like(node)
        if table_value is not None:
            yield table_value

        for key, value in node.items():
            if str(key).lower() == "latest":
                if isinstance(value, list):
                    yield from value
                else:
                    yield value
        for value in node.values():
            yield from iter_latest_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_latest_values(item)


def latest_from_table_like(node: dict):
    columns = first_list_value(node, ("columns", "indicators", "fields", "field"))
    values = first_list_value(node, ("data", "value", "values"))
    if not columns or values is None:
        return None

    normalized_columns = [str(column).lower() for column in columns]
    if "latest" not in normalized_columns:
        return None

    latest_index = normalized_columns.index("latest")
    rows = values
    if rows and isinstance(rows[0], list):
        first_row = rows[0]
    else:
        first_row = rows

    if latest_index >= len(first_row):
        return None
    return first_row[latest_index]


def first_list_value(node: dict, keys: tuple[str, ...]):
    for key in keys:
        value = node.get(key)
        if isinstance(value, list):
            return value
    return None


def to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(",", "")
        if normalized in {"", "--", "None", "none", "null", "NULL"}:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def extract_business_error(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None

    for key in ("error", "error_message"):
        value = payload.get(key)
        if value:
            return str(value)

    error_code = payload.get("errorcode") or payload.get("error_code")
    if error_code not in (None, "", 0, "0"):
        message = payload.get("errmsg") or payload.get("message")
        if message:
            return str(message)
        return f"iFind 业务错误：{error_code}"
    return None


def is_auth_error_message(message: str) -> bool:
    normalized = message.lower()
    return any(keyword in normalized for keyword in ("token", "auth", "unauthorized", "鉴权", "授权", "过期", "无效"))
