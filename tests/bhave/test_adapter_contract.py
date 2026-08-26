from backend.app.services.bhave_client import BHaveClient


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_bhave_client_extracts_configured_payload(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _Response({"items": [{"patient_id": "1", "behavior_name": "agressao"}]})

    monkeypatch.setattr("requests.get", fake_get)
    client = BHaveClient(
        "https://example.test/api",
        "token",
        records_path="/v2/events",
        auth_scheme="api-key",
        auth_header="X-API-Key",
        records_key="items",
    )

    records = client.fetch_behavior_records("2026-01-01", "2026-01-31")

    assert records == [{"patient_id": "1", "behavior_name": "agressao"}]
    assert calls[0]["url"] == "https://example.test/api/v2/events"
    assert calls[0]["headers"] == {"X-API-Key": "token"}


def test_bhave_contract_reports_missing_credentials():
    status = BHaveClient("", "").validate_contract()
    assert not status["ok"]
    assert "SELLAS_BHAVE_BASE_URL" in status["missing"]
