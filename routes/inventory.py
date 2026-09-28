"""Routes for /api/inventory — full CRUD, plus a low-stock filter.

Follows routes/clients.py and routes/company.py patterns: api_response()
envelope on every return, whitelisted editable fields, a specific DB
constraint caught ahead of the broader SQLAlchemyError. Here that's a
duplicate `product` name (inventory.product is UNIQUE) rather than an FK
conflict — same principle as clients.py's 409, different constraint.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Inventory
from utils.responses import api_response

inventory_bp = Blueprint("inventory", __name__)

EDITABLE_FIELDS = {"product", "brand", "price", "par_level", "balance"}
DECIMAL_FIELDS = {"price", "par_level", "balance"}


def _to_decimal(value) -> Decimal:
    """Convert a JSON number/string to Decimal via str() first.

    Never via float() directly — float(0.1) already carries binary rounding
    error before Decimal ever sees it (Decimal(0.1) != Decimal('0.1')).
    Going through str() first is what actually gets money math right.

    Raises:
        ValueError: If `value` isn't a valid number.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"'{value}' is not a valid number.")


@inventory_bp.route("", methods=["GET"])
def list_inventory():
    """GET /api/inventory — list all products, alphabetical.

    Query params:
        low_stock=1 — only products where balance < par_level.
    """
    query = Inventory.query
    if request.args.get("low_stock") == "1":
        query = query.filter(Inventory.balance < Inventory.par_level)
    items = query.order_by(Inventory.product.asc()).all()
    return api_response(data=[i.to_dict() for i in items]), 200


@inventory_bp.route("", methods=["POST"])
def create_inventory_item():
    """POST /api/inventory — create a product. `product` required; others optional."""
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    product = body.get("product")
    if not isinstance(product, str) or not product.strip():
        return api_response(
            error="missing_field", message="'product' is required and must be a non-empty string."
        ), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    if "brand" in body and body["brand"] is not None and not isinstance(body["brand"], str):
        return api_response(error="invalid_type", message="'brand' must be a string."), 400

    decimals = {}
    for field in DECIMAL_FIELDS:
        if field in body and body[field] is not None:
            try:
                decimals[field] = _to_decimal(body[field])
            except ValueError as exc:
                return api_response(error="invalid_type", message=f"'{field}': {exc}"), 400

    item = Inventory(
        product=product.strip(),
        brand=body.get("brand"),
        price=decimals.get("price", Decimal("0")),
        par_level=decimals.get("par_level", Decimal("0")),
        balance=decimals.get("balance", Decimal("0")),
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(
            error="conflict", message=f"A product named '{product.strip()}' already exists."
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=item.to_dict()), 201


@inventory_bp.route("/<int:item_id>", methods=["PUT"])
def update_inventory_item(item_id: int):
    """PUT /api/inventory/<id> — update. Whitelist: product, brand, price, par_level, balance."""
    item = Inventory.query.get(item_id)
    if item is None:
        return api_response(error="not_found", message=f"No inventory item with id {item_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    if "product" in body and (not isinstance(body["product"], str) or not body["product"].strip()):
        return api_response(error="invalid_type", message="'product' must be a non-empty string."), 400

    if "brand" in body and body["brand"] is not None and not isinstance(body["brand"], str):
        return api_response(error="invalid_type", message="'brand' must be a string."), 400

    decimals = {}
    for field in DECIMAL_FIELDS:
        if field in body and body[field] is not None:
            try:
                decimals[field] = _to_decimal(body[field])
            except ValueError as exc:
                return api_response(error="invalid_type", message=f"'{field}': {exc}"), 400

    if "product" in body:
        item.product = body["product"].strip()
    if "brand" in body:
        item.brand = body["brand"]
    for field, value in decimals.items():
        setattr(item, field, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(
            error="conflict", message=f"A product named '{item.product}' already exists."
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=item.to_dict()), 200


@inventory_bp.route("/<int:item_id>", methods=["DELETE"])
def delete_inventory_item(item_id: int):
    """DELETE /api/inventory/<id> — delete a product.

    No FK guards this one: invoice_items.product_name is a plain string
    snapshot taken at invoice-save time, not a foreign key into inventory —
    so deleting a product never blocks on existing invoices.
    """
    item = Inventory.query.get(item_id)
    if item is None:
        return api_response(error="not_found", message=f"No inventory item with id {item_id}."), 404

    db.session.delete(item)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"id": item_id, "deleted": True}), 200
