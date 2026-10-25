from fastapi.testclient import TestClient

from slothwatch.dashboard import make_app


def test_dashboard_is_local_static_and_has_receipt_endpoint(settings_factory, capsys):
    settings = settings_factory("dashboard")
    app = make_app(settings)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "SlothWatch" in response.text
    assert "prefers-color-scheme" in response.text
    assert client.get("/api/status").status_code == 200
    payload = client.get("/api/receipts").json()
    assert payload["verified"] is True
    assert payload["canary_checks"] == 0
    assert payload["canary_catch_rate"] is None
    assert "esc(a.draft_message||'')" in response.text
    assert "forecast-chart" in response.text


def test_dashboard_rejects_invalid_suppression_threshold(settings_factory):
    client = TestClient(make_app(settings_factory("settings-validation")))
    response = client.post("/api/settings", json={"learning_threshold": 1.8})
    assert response.status_code == 400
