"""Routes for the stock movement history (read-only).

GET /api/inventory/<id>/movements   — history of one product
GET /api/stock-movements            — global history with filters + paging

Both: any logged-in role may read. Newest first.
Note: created_at is stored in UTC, so from/to dates are UTC days.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from flask import Blueprint, request

from models import StockMovement
from utils.responses import api_response
from utils.stock_movements import VALID_REASONS

stock_movements_bp = Blueprint("stock_movements", __name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def _int_arg(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"'{name}' must be a whole number.")
    if value < minimum:
        raise ValueError(f"'{name}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        value = maximum
    return value


def _date_arg(name: str) -> date | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"'{name}': '{raw}' is not a valid ISO date ('YYYY-MM-DD').")


def _filtered_query(inventory_id: int | None = None):
    """Build the filtered query. Raises ValueError with a friendly message."""
    query = StockMovement.query

    if inventory_id is None and request.args.get("inventory_id"):
        inventory_id = _int_arg("inventory_id", 0, 1)
    if inventory_id is not None:
        query = query.filter(StockMovement.inventory_id == inventory_id)

    date_from = _date_arg("from")
    date_to = _date_arg("to")
    if date_from:
        query = query.filter(StockMovement.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        # "to" is inclusive: include the whole of that day.
        query = query.filter(
            StockMovement.created_at < datetime.combine(date_to + timedelta(days=1), time.min)
        )

    reason = request.args.get("reason")
    if reason:
        if reason not in VALID_REASONS:
            raise ValueError(f"'reason' must be one of {sorted(VALID_REASONS)}.")
        query = query.filter(StockMovement.reason == reason)

    search = (request.args.get("search") or "").strip()
    if search:
        # Escape % and _ so the person's text is matched literally.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(StockMovement.product_name.ilike(f"%{escaped}%", escape="\\"))

    return query


@stock_movements_bp.route("/inventory/<int:inventory_id>/movements", methods=["GET"])
def product_movements(inventory_id: int):
    try:
        query = _filtered_query(inventory_id=inventory_id)
        limit = _int_arg("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    rows = (
        query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .limit(limit)
        .all()
    )
    return api_response(data=[m.to_dict() for m in rows]), 200


@stock_movements_bp.route("/stock-movements", methods=["GET"])
def list_movements():
    try:
        query = _filtered_query()
        limit = _int_arg("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        offset = _int_arg("offset", 0, 0)
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    total = query.count()
    rows = (
        query.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return api_response(data={"movements": [m.to_dict() for m in rows], "total": total}), 200
