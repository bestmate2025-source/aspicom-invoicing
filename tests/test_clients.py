"""Tests for /api/clients. Run with: pytest tests/test_clients.py -v

See tests/conftest.py for why these run against in-memory SQLite with FK
enforcement explicitly enabled, rather than real MySQL.
"""

from __future__ import annotations


def _post_client(client, **overrides):
    payload = {
        "name": "NMC Wholesales",
        "address": "Al Ain, UAE",
        "trn": "100551136300003",
        "email": "ap@nmc.example",
    }
    payload.update(overrides)
    return client.post("/api/clients", json=payload)


def test_create_client(client):
    resp = _post_client(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["error"] is None
    assert body["data"]["name"] == "NMC Wholesales"
    assert body["data"]["id"] is not None


def test_create_client_missing_name_returns_400(client):
    resp = client.post("/api/clients", json={"address": "no name given"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_field"


def test_create_client_unknown_field_returns_400(client):
    resp = _post_client(client, phone="+971500000000")  # not in EDITABLE_FIELDS
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_fields"


def test_list_clients(client):
    """GET /api/clients returns the {data, error, message} envelope, not a bare array."""
    _post_client(client, name="Client A")
    _post_client(client, name="Client B")

    resp = client.get("/api/clients")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"data", "error", "message"}
    assert body["error"] is None
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2


def test_get_client_by_id(client):
    created = _post_client(client).get_json()["data"]
    resp = client.get(f"/api/clients/{created['id']}")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["id"] == created["id"]


def test_get_client_not_found(client):
    resp = client.get("/api/clients/999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_update_client(client):
    created = _post_client(client).get_json()["data"]
    resp = client.put(f"/api/clients/{created['id']}", json={"email": "new@nmc.example"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["email"] == "new@nmc.example"
    # untouched fields survive the partial update
    assert body["data"]["name"] == "NMC Wholesales"


def test_update_client_unknown_field_returns_400(client):
    created = _post_client(client).get_json()["data"]
    resp = client.put(f"/api/clients/{created['id']}", json={"not_a_real_field": "x"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_fields"


def test_update_client_not_found_returns_404(client):
    resp = client.put("/api/clients/999999", json={"email": "x@example.com"})
    assert resp.status_code == 404


def test_delete_client(client):
    created = _post_client(client).get_json()["data"]
    resp = client.delete(f"/api/clients/{created['id']}")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["deleted"] is True
    assert client.get(f"/api/clients/{created['id']}").status_code == 404


def test_delete_client_not_found_returns_404(client):
    resp = client.delete("/api/clients/999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_delete_client_with_invoices_returns_409(client, db):
    """A client with an existing invoice can't be deleted — FK RESTRICT."""
    created = _post_client(client).get_json()["data"]

    # Insert an invoice directly through the model, bypassing the (not yet
    # built) invoices route on purpose, so this test doesn't depend on
    # routes/invoices.py landing first.
    from models import Invoice

    invoice = Invoice(
        client_id=created["id"],
        invoice_number="TEST-0001",
        status="Draft",
        vat_percent=0,
        subtotal=0,
        vat=0,
        total=0,
    )
    db.session.add(invoice)
    db.session.commit()

    resp = client.delete(f"/api/clients/{created['id']}")
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["error"] == "fk_conflict"
    assert "invoice" in body["message"].lower()

    # and the client must still exist — the delete was actually blocked
    assert client.get(f"/api/clients/{created['id']}").status_code == 200
