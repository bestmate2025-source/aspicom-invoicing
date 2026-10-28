"""Tests for /api/company. Run with: pytest tests/test_company.py -v"""

from __future__ import annotations

_UNSET_FIELDS = ("name", "address", "trn", "phone", "email", "website", "signee",
                  "logo_path", "stamp_path")


def test_get_company_returns_empty_row_on_fresh_db(client):
    resp = client.get("/api/company")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    data = body["data"]
    assert data["id"] == 1
    for field in _UNSET_FIELDS:
        assert data[field] is None


def test_put_company_updates_fields(client):
    resp = client.put("/api/company", json={"name": "Aspicom LLC", "trn": "100441082300003"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert body["data"]["name"] == "Aspicom LLC"
    assert body["data"]["trn"] == "100441082300003"

    # Confirm it actually persisted, not just echoed back in the response.
    resp2 = client.get("/api/company")
    assert resp2.get_json()["data"]["name"] == "Aspicom LLC"


def test_put_company_unknown_field_returns_400(client):
    resp = client.put("/api/company", json={"not_a_real_field": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_fields"


def test_put_company_non_string_returns_400(client):
    resp = client.put("/api/company", json={"name": 12345})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_type"


def test_put_company_rejects_logo_and_stamp_path(client):
    """logo_path/stamp_path are settable only via the upload endpoints —
    confirms that design decision actually holds at the route level."""
    resp = client.put("/api/company", json={"logo_path": "uploads/logo.png"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_fields"
