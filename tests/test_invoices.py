"""Tests for /api/invoices — the big one. Run with: pytest tests/test_invoices.py -v"""

from __future__ import annotations

from models import InvoiceAudit


def _create_client(client, **overrides):
    payload = {"name": "NMC Wholesales", "address": "Al Ain, UAE", "trn": "100551136300003"}
    payload.update(overrides)
    return client.post("/api/clients", json=payload).get_json()["data"]["id"]


def _create_inventory(client, **overrides):
    payload = {"product": "Cocktail Aspi Whitening 5ml", "brand": "ASPICOMLLC",
               "price": 600, "par_level": 20, "balance": 50}
    payload.update(overrides)
    return client.post("/api/inventory", json=payload).get_json()["data"]


def _invoice_payload(client_id, **overrides):
    payload = {
        "client_id": client_id,
        "invoice_number": "INV-0001",
        "issued": "2026-01-23",
        "due": "2026-02-23",
        "vat_percent": 5,
        "items": [
            {"product_name": "Product A", "unit_price": 100, "qty": 2},
            {"product_name": "Product B", "unit_price": 50, "qty": 3},
        ],
    }
    payload.update(overrides)
    return payload


def test_post_creates_invoice_with_correct_totals(client):
    cid = _create_client(client)
    resp = client.post("/api/invoices", json=_invoice_payload(cid))
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["error"] is None
    data = body["data"]
    # subtotal = 100*2 + 50*3 = 350.00 ; vat = 350 * 5% = 17.50 ; total = 367.50
    assert data["subtotal"] == "350.00"
    assert data["vat"] == "17.50"
    assert data["total"] == "367.50"
    assert len(data["items"]) == 2


def test_post_ignores_client_supplied_item_amount_and_recomputes(client):
    """A wrong 'amount' sent on an item is never read — server always
    computes unit_price * qty itself, regardless of what's supplied."""
    cid = _create_client(client)
    payload = _invoice_payload(cid, items=[
        {"product_name": "Product A", "unit_price": 100, "qty": 2, "amount": 999999},
    ])
    resp = client.post("/api/invoices", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert data["items"][0]["amount"] == "200.00"  # not 999999
    assert data["subtotal"] == "200.00"


def test_post_duplicate_invoice_number_returns_409(client):
    cid = _create_client(client)
    client.post("/api/invoices", json=_invoice_payload(cid))
    resp = client.post("/api/invoices", json=_invoice_payload(cid))  # same invoice_number again
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "conflict"


def test_post_nonexistent_client_id_returns_409(client):
    resp = client.post("/api/invoices", json=_invoice_payload(999999))
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "fk_conflict"


def test_post_no_items_returns_400(client):
    cid = _create_client(client)
    resp = client.post("/api/invoices", json=_invoice_payload(cid, items=[]))
    assert resp.status_code == 400


def test_post_qty_zero_or_negative_returns_400(client):
    cid = _create_client(client)
    payload = _invoice_payload(cid, items=[{"product_name": "X", "unit_price": 10, "qty": 0}])
    resp = client.post("/api/invoices", json=payload)
    assert resp.status_code == 400


def test_get_list_and_get_one(client):
    cid = _create_client(client)
    created = client.post("/api/invoices", json=_invoice_payload(cid)).get_json()["data"]

    list_resp = client.get("/api/invoices")
    assert list_resp.status_code == 200
    assert len(list_resp.get_json()["data"]) == 1

    one_resp = client.get(f"/api/invoices/{created['id']}")
    assert one_resp.status_code == 200
    assert one_resp.get_json()["data"]["invoice_number"] == "INV-0001"

    assert client.get("/api/invoices/999999").status_code == 404


def test_put_updates_and_writes_one_audit_row_per_changed_field(client):
    cid = _create_client(client)
    created = client.post("/api/invoices", json=_invoice_payload(cid)).get_json()["data"]

    # status + notes only — neither has a knock-on effect on subtotal/vat/
    # total, so the audit-row count stays exactly 2 and predictable.
    resp = client.put(f"/api/invoices/{created['id']}", json={"status": "Sent", "notes": "Reviewed"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["status"] == "Sent"
    assert data["notes"] == "Reviewed"

    rows = InvoiceAudit.query.filter_by(invoice_id=created["id"]).filter(
        InvoiceAudit.action != "create"
    ).all()
    assert len(rows) == 2

    status_row = next(r for r in rows if r.field_name == "status")
    assert status_row.action == "status_change"  # not 'update' — see routes/invoices.py docstring
    assert status_row.old_value == "Draft"
    assert status_row.new_value == "Sent"

    notes_row = next(r for r in rows if r.field_name == "notes")
    assert notes_row.action == "update"
    assert notes_row.new_value == "Reviewed"


def test_put_vat_percent_change_cascades_into_vat_and_total_audit_rows(client):
    """Changing vat_percent recomputes vat/total too — both are stored
    columns that actually changed, so both get their own audit row."""
    cid = _create_client(client)
    created = client.post("/api/invoices", json=_invoice_payload(cid, vat_percent=5)).get_json()["data"]

    resp = client.put(f"/api/invoices/{created['id']}", json={"vat_percent": 10})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["vat"] == "35.00"     # 350 * 10%
    assert data["total"] == "385.00"  # 350 + 35

    changed_fields = {
        r.field_name for r in InvoiceAudit.query.filter_by(invoice_id=created["id"], action="update").all()
    }
    assert {"vat_percent", "vat", "total"} <= changed_fields


def test_delete_writes_audit_before_deleting(client):
    cid = _create_client(client)
    created = client.post("/api/invoices", json=_invoice_payload(cid)).get_json()["data"]

    resp = client.delete(f"/api/invoices/{created['id']}")
    assert resp.status_code == 200

    audit_row = InvoiceAudit.query.filter_by(invoice_id=created["id"], action="delete").first()
    assert audit_row is not None
    assert "INV-0001" in audit_row.old_value

    assert client.get(f"/api/invoices/{created['id']}").status_code == 404


def test_delete_nonexistent_invoice_returns_404(client):
    resp = client.delete("/api/invoices/999999")
    assert resp.status_code == 404


def test_inventory_decremented_on_post(client):
    inv_item = _create_inventory(client, product="Widget", balance=50)
    cid = _create_client(client)
    payload = _invoice_payload(
        cid, items=[{"product_name": "widget", "unit_price": 10, "qty": 5}]  # case mismatch, on purpose
    )
    resp = client.post("/api/invoices", json=payload)
    assert resp.status_code == 201

    updated = client.get("/api/inventory").get_json()["data"]
    match = next(i for i in updated if i["id"] == inv_item["id"])
    assert match["balance"] == "45.00"  # 50 - 5


def test_inventory_not_decremented_on_put(client):
    inv_item = _create_inventory(client, product="Gadget", balance=50)
    cid = _create_client(client)
    payload = _invoice_payload(cid, items=[{"product_name": "Gadget", "unit_price": 10, "qty": 5}])
    created = client.post("/api/invoices", json=payload).get_json()["data"]

    after_post = client.get("/api/inventory").get_json()["data"]
    balance_after_post = next(i for i in after_post if i["id"] == inv_item["id"])["balance"]
    assert balance_after_post == "45.00"

    client.put(f"/api/invoices/{created['id']}", json={
        "items": [{"product_name": "Gadget", "unit_price": 10, "qty": 20}]
    })

    after_put = client.get("/api/inventory").get_json()["data"]
    balance_after_put = next(i for i in after_put if i["id"] == inv_item["id"])["balance"]
    assert balance_after_put == balance_after_post  # unchanged by the PUT


def test_pdf_endpoint_returns_501(client):
    cid = _create_client(client)
    created = client.post("/api/invoices", json=_invoice_payload(cid)).get_json()["data"]
    resp = client.get(f"/api/invoices/{created['id']}/pdf")
    assert resp.status_code == 501
    assert resp.get_json()["error"] == "not_implemented"
