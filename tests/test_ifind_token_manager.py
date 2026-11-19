from app.monitor.ifind_token_manager import IfindTokenManager
from app.monitor.market_provider import IfindMarketDataProvider


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.response

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


def test_refresh_token_not_configured_returns_none():
    manager = IfindTokenManager(base_url="https://quantapi.51ifind.com/api/v1")

    token = manager.get_valid_access_token()
    status = manager.get_status()

    assert token is None
    assert status.auth_state == "not_configured"
    assert status.has_refresh_token is False
    assert status.has_access_token is False
    assert "未配置" in status.last_error


def test_get_access_token_success_does_not_expose_token_in_status():
    http_client = FakeHttpClient(
        FakeResponse(payload={"data": {"access_token": "secret-access-token", "expires_in": 7200}})
    )
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        refresh_token="secret-refresh-token",
        http_client=http_client,
    )

    token = manager.get_valid_access_token()
    status = manager.get_status()

    assert token == "secret-access-token"
    assert status.auth_state == "access_token_ready"
    assert status.has_access_token is True
    assert status.last_refresh_success is True
    assert status.last_error is None
    assert not hasattr(status, "access_token")
    assert http_client.calls[0]["headers"] == {"refresh_token": "secret-refresh-token"}


def test_get_access_token_http_500_fails_safely():
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        refresh_token="secret-refresh-token",
        http_client=FakeHttpClient(FakeResponse(status_code=500, payload={})),
    )

    token = manager.get_valid_access_token()
    status = manager.get_status()

    assert token is None
    assert status.auth_state == "refresh_failed"
    assert "HTTP 状态码 500" in status.last_error
    assert "权限已到期或权限已更新" in status.last_error


def test_get_access_token_missing_access_token_fails_safely():
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        refresh_token="secret-refresh-token",
        http_client=FakeHttpClient(FakeResponse(payload={"data": {}})),
    )

    token = manager.get_valid_access_token()
    status = manager.get_status()

    assert token is None
    assert status.auth_state == "refresh_failed"
    assert "缺少访问令牌" in status.last_error
    assert "access_token" not in status.last_error


def test_invalid_refresh_token_marks_status():
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        refresh_token="expired-refresh-token",
        http_client=FakeHttpClient(FakeResponse(status_code=401, payload={})),
    )

    token = manager.get_valid_access_token()
    status = manager.get_status()

    assert token is None
    assert status.auth_state == "refresh_token_invalid"
    assert "refresh token 可能已失效" in status.last_error
    assert "权限已到期或权限已更新" in status.last_error
    assert ".env" not in status.last_error


def test_ifind_provider_without_token_returns_failed_market_price():
    manager = IfindTokenManager(base_url="https://quantapi.51ifind.com/api/v1")
    provider = IfindMarketDataProvider(token_manager=manager)

    price = provider.get_price("CU2608", base_price=105600)

    assert price.success is False
    assert price.source == "ifind"
    assert price.current_price is None
    assert "未配置" in price.error_message


def test_ifind_provider_with_token_requests_realtime_price_and_handles_http_500():
    manager = IfindTokenManager(
        base_url="https://quantapi.51ifind.com/api/v1",
        access_token="secret-access-token",
    )
    http_client = FakeHttpClient(FakeResponse(status_code=500, payload={}))
    provider = IfindMarketDataProvider(
        token_manager=manager,
        http_client=http_client,
    )

    price = provider.get_price("CU2608", base_price=105600)

    assert price.success is False
    assert price.source == "ifind"
    assert price.current_price is None
    assert "HTTP 状态码 500" in price.error_message
    assert http_client.calls
    assert http_client.calls[0]["json"]["codes"] == "CU2608"
    assert "secret-access-token" not in str(price.raw)
