"""Routes for /api/suppliers (ERP Phase 3 — Supplier Management).

Registered in app.py with url_prefix="/api/suppliers".

  GET    /api/suppliers                   list  (?active=1|0, ?search=text on name/trn)
  GET    /api/suppliers/<id>              one
  POST   /api/suppliers                   create
  PUT    /api/suppliers/<id>              update (every field optional)
  DELETE /api/suppliers/<id>              hard delete — refused (409) if PDF bills are linked
  POST   /api/suppliers/<id>/deactivate   soft switch off
  POST   /api/suppliers/<id>/activate     switch back on
  POST   /api/suppliers/from-parsed       create from supplier details read off a PDF
  POST   /api/suppliers/link-bills        link imported PDF bills that have no supplier yet (by supplier name)

Who can do what: the app-wide before_request check in app.py already limits
POST / PUT / DELETE to admins, so the read-only 'user' role can only GET.

Bank details: bank_name and bank_account are only ever included in a response
for admins (or when AUTH_ENABLED is off). For everyone else the keys are
removed from the JSON completely.
"""

from __future__ import annotations

import re

from flask import Blueprint, g, request
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import ParsedInvoice, PurchaseOrder, Supplier
from utils.auth import auth_enabled
from utils.responses import api_response

suppliers_bp = Blueprint("suppliers", __name__)

NAME_MIN = 3
NAME_MAX = 255
LONG_TEXT_MAX = 5000
LEAD_TIME_MAX = 3650  # 10 years — just a sanity limit

# short text columns -> max length (matches the column sizes)
TEXT_FIELDS_MAX = {
    "trn": 50,
    "po_box": 50,
    "email": 255,
    "phone": 50,
    "contact_person": 150,
    "payment_terms": 100,
    "bank_name": 150,
    "bank_account": 100,
    "category": 100,
}
LONG_TEXT_FIELDS = ("address", "notes")
EDITABLE_FIELDS = {"name", "lead_time_days", "is_active"} | set(TEXT_FIELDS_MAX) | set(LONG_TEXT_FIELDS)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DIGITS_RE = re.compile(r"\d+", re.ASCII)


# --- helpers -------------------------------------------------------------


def _is_admin() -> bool:
    """True when the caller may see bank details."""
    if not auth_enabled():
        return True  # auth switched off (local dev) -> everyone is treated as admin
    user = getattr(g, "current_user", None)
    return user is not None and user.role == "admin"


def _supplier_json(supplier: Supplier) -> dict:
    return supplier.to_dict(include_bank=_is_admin())


def _validate(body: dict, require_name: bool):
    """Validate + clean the fields that are present in body.

    Returns (cleaned_dict, None) or (None, (error_code, message)).
    Only keys present in the body end up in cleaned_dict, so PUT can be partial.
    Empty strings become None.
    """
    cleaned: dict = {}

    if require_name or "name" in body:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, ("missing_field", "'name' is required and must be a non-empty string.")
        name = name.strip()
        if not (NAME_MIN <= len(name) <= NAME_MAX):
            return None, ("invalid_type", f"'name' must be between {NAME_MIN} and {NAME_MAX} characters.")
        cleaned["name"] = name

    for key, max_len in TEXT_FIELDS_MAX.items():
        if key not in body:
            continue
        value = body[key]
        if value is None:
            cleaned[key] = None
            continue
        if not isinstance(value, str):
            return None, ("invalid_type", f"'{key}' must be a string.")
        value = value.strip() or None
        if value is not None and len(value) > max_len:
            return None, ("invalid_type", f"'{key}' must be at most {max_len} characters.")
        if key == "email" and value is not None and not EMAIL_RE.match(value):
            return None, ("invalid_type", f"'email': '{value}' is not a valid email address.")
        cleaned[key] = value

    for key in LONG_TEXT_FIELDS:
        if key not in body:
            continue
        value = body[key]
        if value is None:
            cleaned[key] = None
            continue
        if not isinstance(value, str):
            return None, ("invalid_type", f"'{key}' must be a string.")
        value = value.strip() or None
        if value is not None and len(value) > LONG_TEXT_MAX:
            return None, ("invalid_type", f"'{key}' must be at most {LONG_TEXT_MAX} characters.")
        cleaned[key] = value

    if "lead_time_days" in body:
        raw = body["lead_time_days"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            cleaned["lead_time_days"] = None
        else:
            bad = ("invalid_type", f"'lead_time_days' must be a whole number between 0 and {LEAD_TIME_MAX}.")
            if isinstance(raw, bool):
                return None, bad
            if isinstance(raw, str):
                text = raw.strip()
                if not DIGITS_RE.fullmatch(text):
                    return None, bad
                raw = int(text)
            elif isinstance(raw, float):
                if not raw.is_integer():
                    return None, bad
                raw = int(raw)
            elif not isinstance(raw, int):
                return None, bad
            if not (0 <= raw <= LEAD_TIME_MAX):
                return None, bad
            cleaned["lead_time_days"] = raw

    if "is_active" in body:
        if not isinstance(body["is_active"], bool):
            return None, ("invalid_type", "'is_active' must be true or false.")
        cleaned["is_active"] = body["is_active"]

    return cleaned, None


def _find_duplicate(name: str, exclude_id: int | None = None) -> Supplier | None:
    """Another supplier with the same name (case-insensitive), or None."""
    query = Supplier.query.filter(func.lower(Supplier.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Supplier.id != exclude_id)
    return query.first()


def _duplicate_response(existing: Supplier):
    return api_response(
        error="conflict",
        message=f"A supplier named '{existing.name}' already exists (id {existing.id}).",
    ), 409


def _create_supplier(body: dict, ignore_unknown: bool = False):
    """Shared by POST /api/suppliers and POST /api/suppliers/from-parsed."""
    if ignore_unknown:
        body = {k: v for k, v in body.items() if k in EDITABLE_FIELDS}
    else:
        unknown = set(body.keys()) - EDITABLE_FIELDS
        if unknown:
            return api_response(
                error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
            ), 400

    cleaned, err = _validate(body, require_name=True)
    if err:
        return api_response(error=err[0], message=err[1]), 400

    existing = _find_duplicate(cleaned["name"])
    if existing is not None:
        return _duplicate_response(existing)

    supplier = Supplier(**cleaned)
    db.session.add(supplier)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=_supplier_json(supplier)), 201


def _json_body():
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return None
    return body


# --- routes --------------------------------------------------------------


@suppliers_bp.route("", methods=["GET"])
def list_suppliers():
    """GET /api/suppliers — all suppliers, name A-Z.

    Query: active=1 (only active) or active=0 (only inactive);
           search=text (matches name or TRN, case-insensitive).
    """
    query = Supplier.query

    active = request.args.get("active")
    if active in ("1", "true"):
        query = query.filter(Supplier.is_active.is_(True))
    elif active in ("0", "false"):
        query = query.filter(Supplier.is_active.is_(False))
    elif active not in (None, ""):
        return api_response(error="invalid_type", message="'active' must be 1 or 0."), 400

    search = (request.args.get("search") or "").strip()
    if search:
        # Escape % and _ so the typed text is matched literally.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(or_(
            Supplier.name.ilike(pattern, escape="\\"),
            Supplier.trn.ilike(pattern, escape="\\"),
        ))

    rows = query.order_by(Supplier.name.asc(), Supplier.id.asc()).all()
    return api_response(data=[_supplier_json(s) for s in rows]), 200


@suppliers_bp.route("/from-parsed", methods=["POST"])
def create_from_parsed():
    """POST /api/suppliers/from-parsed — create a supplier from details read off a PDF.

    Same rules as the normal create, except extra keys the PDF parser may send
    are ignored instead of rejected.
    """
    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400
    return _create_supplier(body, ignore_unknown=True)


def _norm_name(value) -> str:
    """Name for matching: lower case, extra spaces squeezed."""
    return " ".join(str(value or "").lower().split())


def plan_bill_links(suppliers, bills) -> tuple[dict, int]:
    """Decide which bills get which supplier. Pure function (no database).

    suppliers: [(supplier_id, name)]   bills: [(bill_id, supplier_name_on_bill)]
    A bill is linked when its supplier name equals one supplier's name (capitals and
    extra spaces ignored). If two suppliers share a name the bill is skipped (too risky to guess).
    Returns ({bill_id: supplier_id}, skipped_ambiguous_count).
    """
    by_name: dict[str, int] = {}
    duplicated: set[str] = set()
    for supplier_id, name in suppliers:
        key = _norm_name(name)
        if not key:
            continue
        if key in by_name:
            duplicated.add(key)
        else:
            by_name[key] = supplier_id
    plan: dict[int, int] = {}
    skipped = 0
    for bill_id, bill_name in bills:
        key = _norm_name(bill_name)
        if not key or key not in by_name:
            continue
        if key in duplicated:
            skipped += 1
        else:
            plan[bill_id] = by_name[key]
    return plan, skipped


@suppliers_bp.route("/link-bills", methods=["POST"])
def link_bills():
    """POST /api/suppliers/link-bills - give every imported PDF bill that has no supplier yet
    the supplier with the same name. Safe to call any number of times."""
    suppliers = [(s.id, s.name) for s in Supplier.query.all()]
    bills = ParsedInvoice.query.filter(ParsedInvoice.supplier_id.is_(None)).all()
    plan, skipped = plan_bill_links(suppliers, [(b.id, b.supplier_name) for b in bills])
    for bill in bills:
        if bill.id in plan:
            bill.supplier_id = plan[bill.id]
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500
    return api_response(data={"linked": len(plan), "skipped_ambiguous": skipped}), 200


@suppliers_bp.route("", methods=["POST"])
def create_supplier():
    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400
    return _create_supplier(body)


@suppliers_bp.route("/<int:supplier_id>", methods=["GET"])
def get_supplier(supplier_id: int):
    supplier = Supplier.query.get(supplier_id)
    if supplier is None:
        return api_response(error="not_found", message=f"No supplier with id {supplier_id}."), 404
    return api_response(data=_supplier_json(supplier)), 200


@suppliers_bp.route("/<int:supplier_id>", methods=["PUT"])
def update_supplier(supplier_id: int):
    supplier = Supplier.query.get(supplier_id)
    if supplier is None:
        return api_response(error="not_found", message=f"No supplier with id {supplier_id}."), 404

    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    if "id" in body:
        return api_response(error="invalid_field", message="'id' cannot be changed."), 400
    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    cleaned, err = _validate(body, require_name=False)
    if err:
        return api_response(error=err[0], message=err[1]), 400

    if "name" in cleaned:
        existing = _find_duplicate(cleaned["name"], exclude_id=supplier.id)
        if existing is not None:
            return _duplicate_response(existing)

    for key, value in cleaned.items():
        setattr(supplier, key, value)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=_supplier_json(supplier)), 200


@suppliers_bp.route("/<int:supplier_id>", methods=["DELETE"])
def delete_supplier(supplier_id: int):
    supplier = Supplier.query.get(supplier_id)
    if supplier is None:
        return api_response(error="not_found", message=f"No supplier with id {supplier_id}."), 404

    linked = ParsedInvoice.query.filter(ParsedInvoice.supplier_id == supplier.id).count()
    if linked:
        return api_response(
            error="conflict",
            message=f"Cannot delete '{supplier.name}': {linked} imported supplier bill(s) are linked to it. "
                    f"Deactivate it instead.",
        ), 409

    po_count = PurchaseOrder.query.filter(PurchaseOrder.supplier_id == supplier.id).count()
    if po_count:
        return api_response(
            error="conflict",
            message=f"Cannot delete '{supplier.name}': {po_count} purchase order(s) use it. "
                    f"Deactivate it instead.",
        ), 409

    db.session.delete(supplier)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"id": supplier_id, "deleted": True}), 200


def _set_active(supplier_id: int, value: bool):
    supplier = Supplier.query.get(supplier_id)
    if supplier is None:
        return api_response(error="not_found", message=f"No supplier with id {supplier_id}."), 404

    supplier.is_active = value
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=_supplier_json(supplier)), 200


@suppliers_bp.route("/<int:supplier_id>/deactivate", methods=["POST"])
def deactivate_supplier(supplier_id: int):
    return _set_active(supplier_id, False)


@suppliers_bp.route("/<int:supplier_id>/activate", methods=["POST"])
def activate_supplier(supplier_id: int):
    return _set_active(supplier_id, True)
