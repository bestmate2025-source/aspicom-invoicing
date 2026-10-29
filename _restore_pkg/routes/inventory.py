"""Routes for /api/inventory — full CRUD, plus a low-stock filter.

Follows routes/clients.py and routes/company.py patterns: api_response()
envelope on every return, whitelisted editable fields, a specific DB
constraint caught ahead of the broader SQLAlchemyError. Here that's a
duplicate `product` name (unique PER COMPANY since Wave 3A) rather than an FK
conflict — same principle as clients.py's 409, different constraint.

Wave 3A: every company has its own stock. Each product belongs to one company
(inventory.company_id) and that cannot be changed later.

vat_percent on inventory = the DEFAULT VAT% for this product. When the
product is added to an invoice, this value auto-fills the line item's
vat_percent. It can still be overridden per-invoice.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Company, Inventory
from utils.accounting_auto import opening_posted
from utils.auth import get_current_user
from utils.responses import api_response

inventory_bp = Blueprint("inventory", __name__)

EDITABLE_FIELDS = {"company_id", "product", "brand", "price", "vat_percent", "par_level", "balance", "avg_cost"}
DECIMAL_FIELDS = {"price", "vat_percent", "par_level", "balance", "avg_cost"}
PUT_EDITABLE_FIELDS = {"product", "brand", "price", "vat_percent", "par_level", "avg_cost"}  # no balance - use /adjust endpoint
# company_id is also accepted on PUT, but only when it is the same company (the edit screen sends it back).


def _to_decimal(value) -> Decimal:
    """Convert a JSON number/string to Decimal via str() first.

    Never via float() directly — float(0.1) already carries binary rounding
    error before Decimal ever sees it (Decimal(0.1) != Decimal('0.1')).
    Going through str() first is what actually gets money math right.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"'{value}' is not a valid number.")


def _is_admin() -> bool:
    actor = get_current_user()
    return actor is not None and actor.role == "admin"


def _view(item) -> dict:
    """Cost is the owner's business: only an admin sees avg_cost."""
    data = item.to_dict()
    if not _is_admin():
        data.pop("avg_cost", None)
    return data


def _cost_denied():
    return api_response(error="forbidden", message="Only an admin can set a product's cost."), 403


def _primary_company_id():
    row = db.session.query(Company.id).order_by(Company.id.asc()).first()
    return row[0] if row else None


@inventory_bp.route("", methods=["GET"])
def list_inventory():
    """GET /api/inventory — list products, alphabetical.

    Query params:
        company_id=N — only that company's stock (leave out = all companies).
        low_stock=1  — only products where balance < par_level.
    """
    query = Inventory.query
    raw_company = request.args.get("company_id")
    if raw_company not in (None, ""):
        if not raw_company.isdigit():
            return api_response(error="invalid_type", message="'company_id' must be a whole number."), 400
        query = query.filter(Inventory.company_id == int(raw_company))
    if request.args.get("low_stock") == "1":
        query = query.filter(Inventory.balance < Inventory.par_level)
    items = query.order_by(Inventory.product.asc()).all()
    return api_response(data=[_view(i) for i in items]), 200


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

    # Validate vat_percent range
    if "vat_percent" in decimals and not (Decimal("0") <= decimals["vat_percent"] <= Decimal("100")):
        return api_response(error="invalid_type", message="'vat_percent' must be between 0 and 100."), 400

    if "avg_cost" in body and not _is_admin():
        return _cost_denied()
    if "avg_cost" in decimals and decimals["avg_cost"] < 0:
        return api_response(error="invalid_type", message="'avg_cost' cannot be negative."), 400

    # Which company's stock? Sent by the screen. If it is missing we use the first company.
    company_id = body.get("company_id")
    if company_id is None:
        company_id = _primary_company_id()
    if isinstance(company_id, bool) or not isinstance(company_id, int):
        return api_response(error="invalid_type", message="'company_id' must be a whole number."), 400
    if Company.query.get(company_id) is None:
        return api_response(error="fk_conflict", message=f"No company with id {company_id}."), 409

    item = Inventory(
        company_id=company_id,
        product=product.strip(),
        brand=body.get("brand"),
        price=decimals.get("price", Decimal("0")),
        vat_percent=decimals.get("vat_percent", Decimal("5")),
        par_level=decimals.get("par_level", Decimal("0")),
        balance=decimals.get("balance", Decimal("0")),
        avg_cost=decimals.get("avg_cost", Decimal("0")),
    )
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(
            error="conflict", message=f"A product named '{product.strip()}' already exists in this company's stock."
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=_view(item)), 201


@inventory_bp.route("/<int:item_id>", methods=["PUT"])
def update_inventory_item(item_id: int):
    """PUT /api/inventory/<id> — update. Whitelist: product, brand, price, vat_percent, par_level, balance."""
    item = Inventory.query.get(item_id)
    if item is None:
        return api_response(error="not_found", message=f"No inventory item with id {item_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    if "company_id" in body:
        if body["company_id"] != item.company_id:
            return api_response(
                error="company_locked",
                message="A product belongs to one company and cannot be moved to another. Add it to the other company instead.",
            ), 400
        body = {k: v for k, v in body.items() if k != "company_id"}

    unknown = set(body.keys()) - PUT_EDITABLE_FIELDS
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

    if "vat_percent" in decimals and not (Decimal("0") <= decimals["vat_percent"] <= Decimal("100")):
        return api_response(error="invalid_type", message="'vat_percent' must be between 0 and 100."), 400

    if "avg_cost" in body:
        if not _is_admin():
            return _cost_denied()
        if "avg_cost" in decimals:
            if decimals["avg_cost"] < 0:
                return api_response(error="invalid_type", message="'avg_cost' cannot be negative."), 400
            if decimals["avg_cost"] != (item.avg_cost or 0) and opening_posted(item.company_id):
                return api_response(
                    error="cost_locked",
                    message="This company's opening stock is already in the books, so the cost cannot be typed over by hand any more. "
                            "Undo the opening stock first, or let purchases move the cost.",
                ), 409

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
            error="conflict", message=f"A product named '{item.product}' already exists in this company's stock."
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=_view(item)), 200


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