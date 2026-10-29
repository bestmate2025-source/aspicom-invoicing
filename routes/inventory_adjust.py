"""Manual stock adjustments (ERP Phase 2).

Registered in app.py with url_prefix="/api":

  POST /api/inventory/<id>/adjust
       body: { "change_qty": 5, "reason": "stock_in", "notes": "optional text" }

Who can use it: the app-wide before_request check in app.py already refuses
POST / PUT / DELETE / PATCH for anyone who is not an admin (403), so no extra
decorator is needed here.

How the stock changes: log_movement() (utils/stock_movements.py) is the ONLY
place that touches inventory.balance. It adds change_qty to the balance, floors
the result at 0, and writes the history row, all in one transaction. This route
must NOT also edit the balance itself, or the change would be applied twice.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Company, Inventory
from utils.accounting_auto import AccountingError, post_stock_adjustment
from utils.responses import api_response
from utils.stock_movements import get_current_user_id, log_movement

inventory_adjust_bp = Blueprint("inventory_adjust", __name__)

# Reasons a person may choose by hand (the invoice_* ones are automatic only).
ALLOWED_REASONS = ("stock_in", "stock_out", "damage", "expiry", "return", "rtv", "correction")
# Stock arriving must be positive; stock leaving must be negative; correction may be either.
ADD_REASONS = ("stock_in", "return")
REMOVE_REASONS = ("stock_out", "damage", "expiry", "rtv")

ALLOWED_FIELDS = {"change_qty", "reason", "notes"}
MAX_ABS_QTY = Decimal("1000000")
NOTES_MAX = 5000


def parse_adjustment(body):
    """Check the request body. Returns (cleaned_dict, None) or (None, (error, message))."""
    if not isinstance(body, dict):
        return None, ("invalid_json", "Request body must be a JSON object.")

    unknown = set(body.keys()) - ALLOWED_FIELDS
    if unknown:
        return None, ("unknown_fields", f"Unrecognized field(s): {', '.join(sorted(unknown))}.")

    for key in ("change_qty", "reason"):
        if key not in body or body[key] is None or body[key] == "":
            return None, ("missing_field", f"'{key}' is required.")

    raw_qty = body["change_qty"]
    if isinstance(raw_qty, bool) or not isinstance(raw_qty, (int, float, str)):
        return None, ("invalid_type", "'change_qty' must be a number.")
    try:
        qty = Decimal(str(raw_qty).strip())
    except InvalidOperation:
        return None, ("invalid_type", "'change_qty' must be a number.")
    if not qty.is_finite() or (isinstance(raw_qty, float) and not math.isfinite(raw_qty)):
        return None, ("invalid_type", "'change_qty' must be a finite number.")
    qty = qty.quantize(Decimal("0.01"))
    if qty == 0:
        return None, ("invalid_value", "'change_qty' cannot be 0.")
    if abs(qty) > MAX_ABS_QTY:
        return None, ("invalid_value", f"'change_qty' must be between -{MAX_ABS_QTY} and {MAX_ABS_QTY}.")

    reason = body["reason"]
    if not isinstance(reason, str) or reason not in ALLOWED_REASONS:
        return None, ("invalid_value", f"'reason' must be one of: {', '.join(ALLOWED_REASONS)}.")
    if reason in ADD_REASONS and qty < 0:
        return None, ("invalid_value", f"For '{reason}' the quantity must be positive.")
    if reason in REMOVE_REASONS and qty > 0:
        return None, ("invalid_value", f"For '{reason}' the quantity must be negative (for example -5).")

    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        return None, ("invalid_type", "'notes' must be text.")
    notes = (notes or "").strip() or None
    if notes is not None and len(notes) > NOTES_MAX:
        return None, ("invalid_value", f"'notes' must be at most {NOTES_MAX} characters.")

    return {"change_qty": qty, "reason": reason, "notes": notes}, None


@inventory_adjust_bp.route("/inventory/<int:inventory_id>/adjust", methods=["POST"])
def adjust_inventory(inventory_id: int):
    body = request.get_json(silent=True)
    cleaned, err = parse_adjustment(body)
    if err:
        return api_response(error=err[0], message=err[1]), 400

    # Lock the row so two people adjusting the same product at once cannot clash.
    item = Inventory.query.filter_by(id=inventory_id).with_for_update().first()
    if item is None:
        return api_response(error="not_found", message="Inventory item not found."), 404

    try:
        movement = log_movement(
            inventory_id=item.id,
            product_name=item.product,
            change_qty=cleaned["change_qty"],
            reason=cleaned["reason"],
            notes=cleaned["notes"],
            created_by=get_current_user_id(),
        )
        # Wave 3E: book the change (at average cost) when the company's accounting is on.
        # movement.change_qty is what REALLY happened (the balance never goes below 0).
        db.session.flush()
        post_stock_adjustment(
            Company.query.get(item.company_id), movement.id, item.product, cleaned["reason"],
            movement.change_qty, item.avg_cost, date.today(),
        )
        db.session.commit()
    except AccountingError as exc:
        db.session.rollback()
        return api_response(error="accounting_error", message=str(exc)), 409
    except ValueError as exc:
        db.session.rollback()
        return api_response(error="invalid_value", message=str(exc)), 400
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=item.to_dict()), 200
