import requests

from app.db import AlertLog, db
from app.monitor.engine import MonitoringEngine
from app.monitor.ifind_token_manager import IfindTokenManager
from app.monitor.market_provider import IfindMarketDataProvider, MarketPrice, resolve_ifind_code


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self.payload = payload or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("invalid json")
        return self.payload


class FakeHttpClient:
    def __init__(self, response=None, exc=None):
        self.response = response or FakeResponse()
        self.exc = exc
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


def token_manager_with_access_token():
    return IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        access_token="fake-access-token",
    )


def test_token_unavailable_does_not_request_realtime():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 1}))
    provider = IfindMarketDataProvider(
        token_manager=IfindTokenManager(base_url="https://quantapi.51ifind.com/api/v1"),
        http_client=http_client,
    )

    price = provider.get_price("CU2608")

    assert price.success is False
    assert price.current_price is None
    assert "未配置" in price.error_message
    assert http_client.calls == []


def test_realtime_latest_success_returns_market_price():
    http_client = FakeHttpClient(FakeResponse(payload={"tables": [{"table": {"latest": [106720]}}]}))
    provider = IfindMarketDataProvider(token_manager=token_manager_with_access_token(), http_client=http_client)

    price = provider.get_price("CU2608")

    assert price.success is True
    assert price.source == "ifind"
    assert price.current_price == 106720.0
    assert price.raw["ifind_code"] == "CU2608"
    assert "fake-access-token" not in str(price.raw)


def test_code_suffix_is_applied_to_request_body():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 106720}))
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
        code_suffix=".SHF",
    )

    provider.get_price("CU2608")

    assert http_client.calls[0]["json"]["codes"] == "CU2608.SHF"


def test_resolve_ifind_code_exact_map_has_highest_priority():
    resolved = resolve_ifind_code(
        "Y2609",
        code_map="Y2609=Y2609.DCE",
        exchange_suffix_map="Y=.DCE",
        code_suffix=".SHF",
    )

    assert resolved == "Y2609.DCE"


def test_resolve_ifind_code_keeps_full_ifind_code_with_dot():
    assert resolve_ifind_code("Y2609.DCE") == "Y2609.DCE"
    assert resolve_ifind_code("CU2608.SHF") == "CU2608.SHF"


def test_resolve_ifind_code_with_dot_skips_suffix_and_maps():
    assert (
        resolve_ifind_code(
            "Y2609.DCE",
            code_map="Y2609.DCE=WRONG.SHF",
            exchange_suffix_map="Y=.SHF",
            code_suffix=".SHF",
        )
        == "Y2609.DCE"
    )


def test_resolve_ifind_code_uses_prefix_map_before_fallback_suffix():
    resolved = resolve_ifind_code("Y2609", exchange_suffix_map="Y=.DCE", code_suffix=".SHF")

    assert resolved == "Y2609.DCE"


def test_resolve_ifind_code_uses_fallback_suffix():
    resolved = resolve_ifind_code("CU2608", code_suffix=".SHF")

    assert resolved == "CU2608.SHF"


def test_resolve_ifind_code_returns_original_without_mapping():
    resolved = resolve_ifind_code("Y2609")

    assert resolved == "Y2609"


def test_provider_uses_prefix_resolved_code_for_request_body():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 106720}))
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
        code_suffix=".SHF",
        exchange_suffix_map="Y=.DCE",
    )

    price = provider.get_price("Y2609")

    assert http_client.calls[0]["json"]["codes"] == "Y2609.DCE"
    assert price.raw["ifind_code"] == "Y2609.DCE"


def test_provider_uses_full_ifind_code_without_appending_suffix():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 106720}))
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
        code_suffix=".SHF",
        exchange_suffix_map="Y=.DCE",
    )

    price = provider.get_price("Y2609.DCE")

    assert http_client.calls[0]["json"]["codes"] == "Y2609.DCE"
    assert price.raw["ifind_code"] == "Y2609.DCE"


def test_existing_suffix_is_not_duplicated():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 106720}))
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
        code_suffix=".SHF",
    )

    provider.get_price("CU2608.SHF")

    assert http_client.calls[0]["json"]["codes"] == "CU2608.SHF"


def test_http_500_returns_failed_market_price():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(FakeResponse(status_code=500)),
    )

    price = provider.get_price("CU2608")

    assert price.success is False
    assert "HTTP 状态码 500" in price.error_message


def test_timeout_returns_failed_market_price():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(exc=requests.Timeout()),
    )

    price = provider.get_price("CU2608")

    assert price.success is False
    assert "超时" in price.error_message


def test_missing_latest_returns_failed_market_price():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(FakeResponse(payload={"tables": []})),
    )

    price = provider.get_price("CU2608")

    assert price.success is False
    assert "未找到 latest" in price.error_message


def test_string_latest_is_converted_to_float():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(FakeResponse(payload={"latest": "103200.50"})),
    )

    price = provider.get_price("CU2608")

    assert price.success is True
    assert price.current_price == 103200.5


def test_table_columns_and_data_latest_is_parsed():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(
            FakeResponse(payload={"tables": [{"columns": ["thscode", "latest"], "data": [["CU2608.SHF", "103201.50"]]}]})
        ),
    )

    price = provider.get_price("CU2608")

    assert price.success is True
    assert price.current_price == 103201.5


def test_code_map_is_used_for_request_body():
    http_client = FakeHttpClient(FakeResponse(payload={"latest": 106720}))
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
        code_map="CU2608=CU2608.SHF",
    )

    provider.get_price("CU2608")

    assert http_client.calls[0]["json"]["codes"] == "CU2608.SHF"


def test_ifind_get_prices_requests_batch_codes_and_parses_table_payload():
    http_client = FakeHttpClient(
        FakeResponse(
            payload={
                "tables": [
                    {
                        "columns": ["thscode", "latest"],
                        "data": [["CU2608.SHF", "103201.50"], ["LC2609.GFE", "8123.00"]],
                    }
                ]
            }
        )
    )
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=http_client,
    )

    prices = provider.get_prices(["CU2608.SHF", "LC2609.GFE"])

    assert http_client.calls[0]["json"]["codes"] == "CU2608.SHF,LC2609.GFE"
    assert prices["CU2608.SHF"].success is True
    assert prices["CU2608.SHF"].current_price == 103201.5
    assert prices["LC2609.GFE"].success is True
    assert prices["LC2609.GFE"].current_price == 8123.0


def test_ifind_get_prices_returns_only_found_codes_for_partial_payload():
    http_client = FakeHttpClient(
        FakeResponse(
            payload={
                "tables": [
                    {
                        "columns": ["thscode", "latest"],
                        "data": [["CU2608.SHF", "103201.50"]],
                    }
                ]
            }
        )
    )
    provider = IfindMarketDataProvider(token_manager=token_manager_with_access_token(), http_client=http_client)

    prices = provider.get_prices(["CU2608.SHF", "LC2609.GFE"])

    assert list(prices) == ["CU2608.SHF"]


def test_ifind_get_prices_raises_on_batch_http_error():
    provider = IfindMarketDataProvider(
        token_manager=token_manager_with_access_token(),
        http_client=FakeHttpClient(FakeResponse(status_code=502)),
    )

    try:
        provider.get_prices(["CU2608.SHF", "LC2609.GFE"])
    except RuntimeError as exc:
        assert "HTTP 状态码 502" in str(exc)
    else:
        raise AssertionError("batch HTTP errors should raise for engine fallback")


class StaticProvider:
    def __init__(self, price):
        self.price = price

    def get_price(self, code, base_price=None, threshold=None):
        return self.price


def test_monitoring_engine_rebases_on_successful_ifind_price(make_task):
    task = make_task()
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=106720,
            timestamp=None,
            source="ifind",
            success=True,
        )
    )

    result = MonitoringEngine(provider).run_once()

    db.session.refresh(task)
    assert result.alert_count == 1
    assert task.base_price == 106600
    assert AlertLog.query.count() == 1


def test_monitoring_engine_skips_failed_ifind_price(make_task):
    task = make_task()
    provider = StaticProvider(
        MarketPrice(
            code="CU2608",
            current_price=None,
            timestamp=None,
            source="ifind",
            success=False,
            error_message="iFind 实时行情返回中未找到 latest 价格",
        )
    )

    result = MonitoringEngine(provider).run_once()

    db.session.refresh(task)
    assert result.skipped_count == 1
    assert task.base_price == 105600
    assert AlertLog.query.count() == 0
