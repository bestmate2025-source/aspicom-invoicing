"""Routes for manual stock adjustments (ERP Phase 2).

POST /api/inventory/<id>/adjust
  Body: { change_qty: number, reason: "stock_in"|"stock_out"|"damage"|
          "expiry"|"return"|"rtv"|"correction", notes: string (optional) }
  Requires admin. Records the change through the stock_movements helper
  so every adjustment appears in the Movements tab.

The ONLY way to change inventory.balance now — direct edits through
PUT /api/inventory/<id> cannot touch balance.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, request

from extensions import db
from models import Inventory
from utils.responses import api_response
from utils.stock_movements import log_movement

inventory_adjust_bp = Blueprint("inventory_adjust", __name__)

MANUAL_REASONS = {
    "stock_in", "stock_out", "damage", "expiry",
    "return", "rtv", "correction",
}


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"'{value}' is not a valid number.")


@inventory_adjust_bp.route("/inventory/<int:item_id>/adjust", methods=["POST"])
def adjust_inventory(item_id: int):
    item = Inventory.query.get(item_id)
    if item is None:
        return api_response(error="not_found", message=f"No inventory item with id {item_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    # Validate change_qty
    if "change_qty" not in body:
        return api_response(error="missing_field", message="'change_qty' is required."), 400
    try:
        change_qty = _to_decimal(body["change_qty"])
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    if change_qty == 0:
        return api_response(error="bad_request", message="'change_qty' cannot be zero."), 400

    # Validate reason
    reason = body.get("reason")
    if not isinstance(reason, str) or reason not in MANUAL_REASONS:
        return api_response(
            error="invalid_type",
            message=f"'reason' must be one of {sorted(MANUAL_REASONS)}.",
        ), 400

    # Validate notes (optional)
    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        return api_response(error="invalid_type", message="'notes' must be a string."), 400
    if notes:
        notes = notes.strip() or None

    try:
        movement = log_movement(
            inventory_id=item.id,
            product_name=item.product,
            change_qty=change_qty,
            reason=reason,
            notes=notes,
        )
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - surface DB errors plainly
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={
        "movement": movement.to_dict(),
        "inventory": item.to_dict(),
    }), 201
