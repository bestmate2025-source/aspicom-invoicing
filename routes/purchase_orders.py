"""Routes for /api/purchase-orders (ERP Phase 4 - Purchase Orders and Goods Received Notes).

Registered in app.py with url_prefix="/api/purchase-orders".

  GET  /api/purchase-orders                 list (?status=Draft|Sent|Partial|Received|Cancelled, ?supplier_id=N)
  GET  /api/purchase-orders/<id>            one PO with its items and its GRNs
  POST /api/purchase-orders                 create (PO number is generated here, totals are worked out here)
  PUT  /api/purchase-orders/<id>            edit - Draft POs only
  POST /api/purchase-orders/<id>/receive    receive goods: creates a GRN and ADDS stock
  POST /api/purchase-orders/<id>/cancel     cancel a Draft/Sent PO that has received nothing yet

Who can do what: the app-wide before_request check in app.py already limits
POST / PUT / DELETE to admins, so the read-only 'user' role can only GET.

Stock rule: only /receive changes stock, and only through log_movement()
(reason 'stock_in'), exactly like every other stock change in the app.

Status flow:  Draft -> Sent -> Partial -> Received      (Cancelled from Draft/Sent)
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    GoodsReceivedNote,
    GoodsReceivedNoteItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from utils.responses import api_response
from utils.stock_movements import find_inventory_by_name, get_current_user_id, log_movement

purchase_orders_bp = Blueprint("purchase_orders", __name__)

ZERO = Decimal("0")
CENT = Decimal("0.01")
MAX_MONEY = Decimal("99999999.99")      # fits DECIMAL(12,2)
MAX_QTY = Decimal("1000000")
MAX_ITEMS = 200
NOTES_MAX = 5000
NAME_MAX = 255

# Number formats. Change here if you ever want a different look.
PO_PREFIX = "PO-"
GRN_PREFIX = "GRN-"
NUMBER_SEPARATOR = ":"      # PO-2026-10-19:0001

PO_FIELDS = {"supplier_id", "order_date", "expected_date", "notes", "status", "items"}
ITEM_FIELDS = {"product_name", "brand", "unit_price", "qty_ordered", "vat_percent"}
SAVE_STATUSES = ("Draft", "Sent")            # what a person may set when creating / editing
RECEIVABLE_STATUSES = ("Draft", "Sent", "Partial")
LIST_STATUSES = ("Draft", "Sent", "Partial", "Received", "Cancelled")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _Invalid(Exception):
    """A request problem: carries the error code and the message for the api_response envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- pure helpers (no database; easy to test) ---------------------------------


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _number(raw, label: str) -> Decimal:
    """Turn a JSON value into a finite Decimal. Rejects true/false, text that is not a number, NaN, Infinity."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise _Invalid("invalid_type", f"{label} must be a number.")
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise _Invalid("invalid_type", f"{label} must be a number.")
    if not value.is_finite():
        raise _Invalid("invalid_type", f"{label} must be a finite number.")
    return value


def _clean_text(raw, label: str, max_len: int, required: bool = False):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise _Invalid("missing_field", f"{label} is required.")
        return None
    if not isinstance(raw, str):
        raise _Invalid("invalid_type", f"{label} must be text.")
    text = raw.strip()
    if len(text) > max_len:
        raise _Invalid("invalid_value", f"{label} must be at most {max_len} characters.")
    return text


def _parse_date(raw, label: str) -> date:
    if not isinstance(raw, str) or not DATE_RE.match(raw.strip()):
        raise _Invalid("invalid_type", f"{label} must be a date like 2026-10-19.")
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise _Invalid("invalid_value", f"{label} is not a real date.")


def clean_items(raw_items) -> list[dict]:
    """Validate the PO lines and work out each line's amount (unit price x qty, VAT not included)."""
    if not isinstance(raw_items, list) or not raw_items:
        raise _Invalid("missing_field", "'items' must be a list with at least one line.")
    if len(raw_items) > MAX_ITEMS:
        raise _Invalid("invalid_value", f"A purchase order can have at most {MAX_ITEMS} lines.")

    cleaned = []
    for number, raw in enumerate(raw_items, start=1):
        tag = f"Line {number}"
        if not isinstance(raw, dict):
            raise _Invalid("invalid_type", f"{tag} must be an object.")
        unknown = set(raw.keys()) - ITEM_FIELDS
        if unknown:
            raise _Invalid("unknown_fields", f"{tag}: unrecognized field(s): {', '.join(sorted(unknown))}.")

        name = _clean_text(raw.get("product_name"), f"{tag} product name", NAME_MAX, required=True)
        brand = _clean_text(raw.get("brand"), f"{tag} brand", NAME_MAX)

        if raw.get("unit_price") in (None, ""):
            raise _Invalid("missing_field", f"{tag} unit price is required.")
        price = _number(raw["unit_price"], f"{tag} unit price")
        if price < 0 or price > MAX_MONEY:
            raise _Invalid("invalid_value", f"{tag} unit price must be between 0 and {MAX_MONEY}.")
        price = _q(price)

        if raw.get("qty_ordered") in (None, ""):
            raise _Invalid("missing_field", f"{tag} quantity is required.")
        qty = _q(_number(raw["qty_ordered"], f"{tag} quantity"))
        if qty <= 0 or qty > MAX_QTY:
            raise _Invalid("invalid_value", f"{tag} quantity must be more than 0 and at most {MAX_QTY}.")

        vat_raw = raw.get("vat_percent")
        vat_pct = ZERO if vat_raw in (None, "") else _q(_number(vat_raw, f"{tag} VAT %"))
        if vat_pct < 0 or vat_pct > 100:
            raise _Invalid("invalid_value", f"{tag} VAT % must be between 0 and 100.")

        amount = _q(price * qty)
        if amount > MAX_MONEY:
            raise _Invalid("invalid_value", f"{tag} amount is too large.")
        cleaned.append({
            "product_name": name, "brand": brand, "unit_price": price,
            "qty_ordered": qty, "vat_percent": vat_pct, "amount": amount,
        })
    return cleaned


def compute_totals(items: list[dict]) -> dict:
    """subtotal = sum of line amounts, vat = sum of each line's VAT (rounded per line), total = subtotal + vat."""
    subtotal = sum((it["amount"] for it in items), ZERO)
    vat = sum((_q(it["amount"] * it["vat_percent"] / Decimal("100")) for it in items), ZERO)
    subtotal, vat = _q(subtotal), _q(vat)
    total = subtotal + vat
    if total > MAX_MONEY:
        raise _Invalid("invalid_value", "The purchase order total is too large.")
    return {"subtotal": subtotal, "vat": vat, "total": total}


def clean_po(body, creating: bool) -> dict:
    """Validate a create/edit body. Returns only the fields that were sent (with defaults filled in when creating)."""
    if not isinstance(body, dict):
        raise _Invalid("invalid_json", "Request body must be a JSON object.")
    unknown = set(body.keys()) - PO_FIELDS
    if unknown:
        raise _Invalid("unknown_fields", f"Unrecognized field(s): {', '.join(sorted(unknown))}.")

    out: dict = {}

    if "supplier_id" in body or creating:
        raw = body.get("supplier_id")
        if raw in (None, ""):
            raise _Invalid("missing_field", "'supplier_id' is required.")
        if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).strip().isdigit():
            raise _Invalid("invalid_type", "'supplier_id' must be a whole number.")
        out["supplier_id"] = int(str(raw).strip())

    if "order_date" in body:
        if body["order_date"] in (None, ""):
            if not creating:
                raise _Invalid("invalid_value", "'order_date' cannot be empty.")
            out["order_date"] = date.today()
        else:
            out["order_date"] = _parse_date(body["order_date"], "'order_date'")
    elif creating:
        out["order_date"] = date.today()

    if "expected_date" in body:
        out["expected_date"] = None if body["expected_date"] in (None, "") else _parse_date(body["expected_date"], "'expected_date'")
    elif creating:
        out["expected_date"] = None

    if "notes" in body:
        out["notes"] = _clean_text(body["notes"], "'notes'", NOTES_MAX)
    elif creating:
        out["notes"] = None

    if "status" in body:
        if body["status"] not in SAVE_STATUSES:
            raise _Invalid("invalid_value", f"'status' must be one of: {', '.join(SAVE_STATUSES)}. (Use the Receive and Cancel buttons for the other statuses.)")
        out["status"] = body["status"]
    elif creating:
        out["status"] = "Draft"

    if "items" in body or creating:
        out["items"] = clean_items(body.get("items"))

    return out


def check_dates(order_date: date, expected_date) -> None:
    if expected_date is not None and expected_date < order_date:
        raise _Invalid("invalid_value", "The expected date cannot be before the order date.")


def plan_receipt(po_items: list[dict], raw_items) -> list[tuple[int, Decimal]]:
    """Work out what to receive.

    po_items: [{"id", "product_name", "qty_ordered", "qty_received"}] of the PO (Decimals).
    raw_items: the request's [{"item_id", "qty_received"}].
    Returns [(item_id, qty)] for the lines with qty > 0. Raises _Invalid on any problem.
    Receiving more than what is still outstanding is refused.
    """
    if not isinstance(raw_items, list) or not raw_items:
        raise _Invalid("missing_field", "'items' must be a list with at least one line.")
    by_id = {it["id"]: it for it in po_items}
    seen: set[int] = set()
    lines: list[tuple[int, Decimal]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise _Invalid("invalid_type", "Each received line must be an object.")
        unknown = set(raw.keys()) - {"item_id", "qty_received"}
        if unknown:
            raise _Invalid("unknown_fields", f"Unrecognized field(s): {', '.join(sorted(unknown))}.")
        item_raw = raw.get("item_id")
        if isinstance(item_raw, bool) or not isinstance(item_raw, (int, str)) or not str(item_raw).strip().isdigit():
            raise _Invalid("invalid_type", "'item_id' must be a whole number.")
        item_id = int(str(item_raw).strip())
        if item_id not in by_id:
            raise _Invalid("invalid_value", f"Item {item_id} does not belong to this purchase order.")
        if item_id in seen:
            raise _Invalid("invalid_value", f"Item {item_id} appears more than once.")
        seen.add(item_id)

        if raw.get("qty_received") in (None, ""):
            continue  # blank = nothing received for this line
        qty = _q(_number(raw["qty_received"], "'qty_received'"))
        if qty < 0:
            raise _Invalid("invalid_value", "'qty_received' cannot be negative.")
        if qty == 0:
            continue
        item = by_id[item_id]
        remaining = item["qty_ordered"] - item["qty_received"]
        if qty > remaining:
            raise _Invalid(
                "invalid_value",
                f"'{item['product_name']}': you entered {qty} but only {remaining} is still outstanding.",
            )
        lines.append((item_id, qty))

    if not lines:
        raise _Invalid("invalid_value", "Enter a quantity greater than 0 for at least one item.")
    return lines


def status_after_receipt(items: list[dict]) -> str:
    """'Received' when every line is fully in, otherwise 'Partial'."""
    done = all(it["qty_received"] >= it["qty_ordered"] for it in items)
    return "Received" if done else "Partial"


# --- database helpers -----------------------------------------------------------


def _json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _next_number(prefix: str, column, day: date) -> str:
    """Next free number for that day, e.g. PO-2026-10-19:0003."""
    stem = f"{prefix}{day.isoformat()}{NUMBER_SEPARATOR}"
    last = db.session.query(func.max(column)).filter(column.like(stem + "%")).scalar()
    seq = None
    if last:
        try:
            seq = int(str(last).rsplit(NUMBER_SEPARATOR, 1)[1]) + 1
        except (IndexError, ValueError):
            seq = None
    if seq is None:
        seq = (db.session.query(func.count(column)).filter(column.like(stem + "%")).scalar() or 0) + 1
    return f"{stem}{seq:04d}"


def _error(exc: _Invalid, status: int = 400):
    return api_response(error=exc.code, message=exc.message), status


def _db_error(exc: Exception):
    db.session.rollback()
    return api_response(error="db_error", message=str(exc)), 500


def _check_supplier(supplier_id: int, current_supplier_id: int | None = None):
    """Returns an error response tuple, or None when the supplier is fine."""
    supplier = Supplier.query.get(supplier_id)
    if supplier is None:
        return api_response(error="invalid_value", message=f"Supplier {supplier_id} does not exist."), 400
    if not supplier.is_active and supplier_id != current_supplier_id:
        return api_response(
            error="invalid_value",
            message=f"Supplier '{supplier.name}' is inactive. Activate it first or choose another supplier.",
        ), 400
    return None


# --- routes -------------------------------------------------------------------


@purchase_orders_bp.route("", methods=["GET"])
def list_purchase_orders():
    query = PurchaseOrder.query.options(joinedload(PurchaseOrder.supplier))

    status = request.args.get("status")
    if status not in (None, ""):
        if status not in LIST_STATUSES:
            return api_response(error="invalid_value", message=f"'status' must be one of: {', '.join(LIST_STATUSES)}."), 400
        query = query.filter(PurchaseOrder.status == status)

    supplier_id = request.args.get("supplier_id")
    if supplier_id not in (None, ""):
        if not supplier_id.isdigit():
            return api_response(error="invalid_type", message="'supplier_id' must be a whole number."), 400
        query = query.filter(PurchaseOrder.supplier_id == int(supplier_id))

    rows = query.order_by(PurchaseOrder.id.desc()).all()
    return api_response(data=[po.to_dict() for po in rows]), 200


@purchase_orders_bp.route("/<int:po_id>", methods=["GET"])
def get_purchase_order(po_id: int):
    po = PurchaseOrder.query.options(joinedload(PurchaseOrder.supplier)).filter_by(id=po_id).first()
    if po is None:
        return api_response(error="not_found", message=f"No purchase order with id {po_id}."), 404
    return api_response(data=po.to_dict(include_items=True, include_grns=True)), 200


@purchase_orders_bp.route("", methods=["POST"])
def create_purchase_order():
    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400
    try:
        cleaned = clean_po(body, creating=True)
        check_dates(cleaned["order_date"], cleaned["expected_date"])
        totals = compute_totals(cleaned["items"])
    except _Invalid as exc:
        return _error(exc)

    problem = _check_supplier(cleaned["supplier_id"])
    if problem:
        return problem

    po = None
    for _attempt in range(5):  # a clash on the number (two people at once) just picks the next number
        candidate = PurchaseOrder(
            supplier_id=cleaned["supplier_id"],
            po_number=_next_number(PO_PREFIX, PurchaseOrder.po_number, cleaned["order_date"]),
            status=cleaned["status"],
            order_date=cleaned["order_date"],
            expected_date=cleaned["expected_date"],
            notes=cleaned["notes"],
            subtotal=totals["subtotal"], vat=totals["vat"], total=totals["total"],
        )
        candidate.items = [PurchaseOrderItem(**it, qty_received=ZERO) for it in cleaned["items"]]
        try:
            with db.session.begin_nested():
                db.session.add(candidate)
                db.session.flush()
            po = candidate
            break
        except IntegrityError:
            continue
    if po is None:
        db.session.rollback()
        return api_response(error="db_error", message="Could not create the purchase order. Please try again."), 500

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        return _db_error(exc)
    return api_response(data=po.to_dict(include_items=True, include_grns=True)), 201


@purchase_orders_bp.route("/<int:po_id>", methods=["PUT"])
def update_purchase_order(po_id: int):
    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    po = PurchaseOrder.query.filter_by(id=po_id).with_for_update().first()
    if po is None:
        return api_response(error="not_found", message=f"No purchase order with id {po_id}."), 404
    if po.status != "Draft":
        return api_response(
            error="conflict",
            message=f"{po.po_number} is {po.status}. Only Draft purchase orders can be edited.",
        ), 409

    try:
        cleaned = clean_po(body, creating=False)
        check_dates(cleaned.get("order_date", po.order_date), cleaned.get("expected_date", po.expected_date))
        totals = compute_totals(cleaned["items"]) if "items" in cleaned else None
    except _Invalid as exc:
        return _error(exc)

    if "supplier_id" in cleaned:
        problem = _check_supplier(cleaned["supplier_id"], current_supplier_id=po.supplier_id)
        if problem:
            return problem

    for key in ("supplier_id", "order_date", "expected_date", "notes", "status"):
        if key in cleaned:
            setattr(po, key, cleaned[key])
    if "items" in cleaned:
        po.items = [PurchaseOrderItem(**it, qty_received=ZERO) for it in cleaned["items"]]
        po.subtotal, po.vat, po.total = totals["subtotal"], totals["vat"], totals["total"]

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        return _db_error(exc)
    return api_response(data=po.to_dict(include_items=True, include_grns=True)), 200


@purchase_orders_bp.route("/<int:po_id>/cancel", methods=["POST"])
def cancel_purchase_order(po_id: int):
    po = PurchaseOrder.query.filter_by(id=po_id).with_for_update().first()
    if po is None:
        return api_response(error="not_found", message=f"No purchase order with id {po_id}."), 404
    if po.status not in ("Draft", "Sent") or any((it.qty_received or ZERO) > 0 for it in po.items):
        return api_response(
            error="conflict",
            message=f"{po.po_number} is {po.status} and cannot be cancelled. Only Draft or Sent orders with nothing received can be.",
        ), 409
    po.status = "Cancelled"
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        return _db_error(exc)
    return api_response(data=po.to_dict(include_items=True, include_grns=True)), 200


@purchase_orders_bp.route("/<int:po_id>/receive", methods=["POST"])
def receive_purchase_order(po_id: int):
    """Receive goods: write a GRN, add the received quantities to the PO lines, add stock, update the status.

    Body: {"items": [{"item_id": 12, "qty_received": 5}, ...], "notes": "optional", "received_date": "2026-10-19" (optional)}
    Everything happens in ONE database transaction: if anything fails, nothing is saved.
    """
    body = _json_body()
    if body is None:
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400
    unknown = set(body.keys()) - {"items", "notes", "received_date"}
    if unknown:
        return api_response(error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."), 400

    po = PurchaseOrder.query.filter_by(id=po_id).with_for_update().first()
    if po is None:
        return api_response(error="not_found", message=f"No purchase order with id {po_id}."), 404
    if po.status not in RECEIVABLE_STATUSES:
        return api_response(
            error="conflict",
            message=f"{po.po_number} is {po.status}. Goods can only be received on Draft, Sent or Partial orders.",
        ), 409

    try:
        notes = _clean_text(body.get("notes"), "'notes'", NOTES_MAX)
        received_date = date.today()
        if body.get("received_date") not in (None, ""):
            received_date = _parse_date(body["received_date"], "'received_date'")
        by_id = {it.id: it for it in po.items}
        lines = plan_receipt(
            [{"id": it.id, "product_name": it.product_name,
              "qty_ordered": it.qty_ordered or ZERO, "qty_received": it.qty_received or ZERO} for it in po.items],
            body.get("items"),
        )
    except _Invalid as exc:
        return _error(exc)

    # Every received product must already exist in Inventory (matched by name, capitals ignored).
    inventory_for: dict[int, object] = {}
    missing: list[str] = []
    for item_id, _qty in lines:
        name = by_id[item_id].product_name
        inv = find_inventory_by_name(name)
        if inv is None:
            if name not in missing:
                missing.append(name)
        else:
            inventory_for[item_id] = inv
    if missing:
        return api_response(
            error="invalid_value",
            message="These products are not in Inventory yet: " + ", ".join(missing)
                    + ". Add them in Inventory first, then receive again.",
        ), 400

    # Create the GRN (retry with the next number if two people clash on the same number).
    grn = None
    for _attempt in range(5):
        candidate = GoodsReceivedNote(
            po_id=po.id,
            grn_number=_next_number(GRN_PREFIX, GoodsReceivedNote.grn_number, received_date),
            received_date=received_date,
            received_by=get_current_user_id(),
            notes=notes,
        )
        try:
            with db.session.begin_nested():
                db.session.add(candidate)
                db.session.flush()
            grn = candidate
            break
        except IntegrityError:
            continue
    if grn is None:
        db.session.rollback()
        return api_response(error="db_error", message="Could not create the GRN. Please try again."), 500

    try:
        for item_id, qty in lines:
            item = by_id[item_id]
            inv = inventory_for[item_id]
            item.qty_received = (item.qty_received or ZERO) + qty
            db.session.add(GoodsReceivedNoteItem(
                grn_id=grn.id, po_item_id=item.id, product_name=item.product_name, qty_received=qty,
            ))
            log_movement(
                inventory_id=inv.id,
                product_name=inv.product,
                change_qty=qty,
                reason="stock_in",
                reference_id=po.id,
                reference_label=po.po_number,
                notes=grn.grn_number + (f" | {notes}" if notes else ""),
                created_by=get_current_user_id(),
            )
        po.status = status_after_receipt(
            [{"qty_ordered": it.qty_ordered or ZERO, "qty_received": it.qty_received or ZERO} for it in po.items]
        )
        db.session.commit()
    except ValueError as exc:  # log_movement refused something
        db.session.rollback()
        return api_response(error="invalid_value", message=str(exc)), 400
    except SQLAlchemyError as exc:
        return _db_error(exc)

    return api_response(data=po.to_dict(include_items=True, include_grns=True)), 201
