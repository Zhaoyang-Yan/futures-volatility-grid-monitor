def test_healthz_returns_runtime_status(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()
    assert data["status"] in {"ok", "error"}
    assert "worker_started" in data
    assert data["market_data_mode"] == "manual_only"
    assert "auto_monitoring_enabled" in data
    assert "last_poll_status" in data
    assert "last_error" in data
    assert "ifind_auth_state" in data
    assert "ifind_refresh_configured" in data
    assert "ifind_access_ready" in data
    assert "ifind_last_refresh_success" in data
    assert "ifind_last_error" in data
    assert "ifind_permission_renewal_day" in data
    assert "ifind_permission_next_renewal_date" in data
    assert "ifind_permission_days_until_renewal" in data
    assert "ifind_permission_status" in data
    assert "ifind_permission_message" in data
    assert "ifind_permission_error" in data
    assert "notification_mode" in data
    assert "last_notification_status" in data
    assert "last_notification_success" in data
    assert "last_notification_error" in data
    assert "last_notification_at" in data
    assert "last_alert_dedup_status" in data
    assert "last_duplicate_alert_skipped" in data
    assert "secret-access-token" not in response.get_data(as_text=True)
    assert "access_token" not in response.get_data(as_text=True)
    assert "refresh_token" not in response.get_data(as_text=True)
    assert "Bearer" not in response.get_data(as_text=True)
    assert "IFIND_REFRESH_TOKEN" not in response.get_data(as_text=True)
    assert "IFIND_ACCESS_TOKEN" not in response.get_data(as_text=True)
    assert ".env" not in response.get_data(as_text=True)
    assert "headers" not in response.get_data(as_text=True)
