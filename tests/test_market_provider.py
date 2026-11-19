from app.monitor.market_provider import (
    IfindMarketDataProvider,
    MarketPrice,
    SimulatedMarketDataProvider,
    get_market_data_provider,
)


def test_simulated_provider_returns_market_price():
    provider = SimulatedMarketDataProvider()

    price = provider.get_price("CU2608", base_price=105600)

    assert isinstance(price, MarketPrice)
    assert price.success is True
    assert price.source == "simulated"
    assert price.code == "CU2608"
    assert price.current_price is not None
    assert price.timestamp is not None


def test_ifind_provider_without_config_returns_failed_market_price():
    provider = IfindMarketDataProvider()

    price = provider.get_price("CU2608", base_price=105600)

    assert price.success is False
    assert price.source == "ifind"
    assert price.current_price is None
    assert "未配置" in price.error_message


def test_ifind_provider_with_config_is_still_not_implemented():
    class FakeHttpClient:
        def post(self, url, headers=None, json=None, timeout=None):
            class Response:
                status_code = 500

                def json(self):
                    return {}

            return Response()

    provider = IfindMarketDataProvider(
        base_url="https://example.invalid",
        access_token="fake-access-token",
        refresh_token="fake-refresh-token",
        http_client=FakeHttpClient(),
    )

    price = provider.get_price("CU2608", base_price=105600)

    assert price.success is False
    assert price.source == "ifind"
    assert price.current_price is None
    assert "HTTP 状态码 500" in price.error_message


def test_provider_factory_modes():
    assert get_market_data_provider("manual_only") is None
    assert isinstance(get_market_data_provider("simulated"), SimulatedMarketDataProvider)
    assert isinstance(get_market_data_provider("real"), IfindMarketDataProvider)
    assert isinstance(get_market_data_provider("ifind"), IfindMarketDataProvider)
    assert get_market_data_provider("unknown_value") is None
