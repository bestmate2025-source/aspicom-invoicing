"""Tests for /api/inventory. Run with: pytest tests/test_inventory.py -v

Note: MySQL DECIMAL(12,2) columns return values padded to 2 decimal
places — e.g. 600 becomes "600.00", 5 becomes "5.00". That's correct
MySQL behavior, not a bug, so the assertions below match it.
"""

from __future__ import annotations


def _post_item(client, **overrides):
    payload = {"product": "Cocktail Aspi Whitening 5ml", "brand": "ASPICOMLLC",
               "price": 600, "par_level": 20, "balance": 50}
    payload.update(overrides)
    return client.post("/api/inventory", json=payload)


def test_create_inventory_item(client):
    resp = _post_item(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["error"] is None
    assert body["data"]["product"] == "Cocktail Aspi Whitening 5ml"
    assert body["data"]["price"] == "600.00"  # MySQL DECIMAL(12,2) pads to 2 decimals


def test_create_inventory_item_missing_product_returns_400(client):
    resp = client.post("/api/inventory", json={"price": 10})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_field"


def test_create_inventory_item_duplicate_product_returns_409(client):
    _post_item(client)
    resp = _post_item(client)  # same product name again
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "conflict"


def test_create_inventory_item_bad_number_returns_400(client):
    resp = _post_item(client, product="Other Product", price="not-a-number")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_type"


def test_list_inventory(client):
    _post_item(client, product="Product A")
    _post_item(client, product="Product B")
    resp = client.get("/api/inventory")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert len(body["data"]) == 2


def test_low_stock_filter(client):
    _post_item(client, product="Low Stock Item", par_level=20, balance=5)   # below par
    _post_item(client, product="Healthy Stock Item", par_level=20, balance=50)  # above par

    resp = client.get("/api/inventory?low_stock=1")
    assert resp.status_code == 200
    names = [i["product"] for i in resp.get_json()["data"]]
    assert names == ["Low Stock Item"]


def test_update_inventory_item(client):
    created = _post_item(client).get_json()["data"]
    resp = client.put(f"/api/inventory/{created['id']}", json={"balance": 5})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["balance"] == "5.00"  # MySQL DECIMAL(10,2) pads


def test_delete_inventory_item(client):
    created = _post_item(client).get_json()["data"]
    resp = client.delete(f"/api/inventory/{created['id']}")
    assert resp.status_code == 200

    # No GET /api/inventory/<id> route exists for inventory (only list was
    # spec'd) — confirm deletion via the list endpoint instead.
    remaining = client.get("/api/inventory").get_json()["data"]
    assert all(i["id"] != created["id"] for i in remaining)