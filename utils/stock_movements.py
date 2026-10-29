"""Helpers for the stock_movements log (ERP Phase 1 — Stock Movement Foundation).

Rule of the house: nothing changes inventory.balance directly any more.
Everything goes through log_movement(), which updates the balance AND writes
the history row in the same database transaction. The caller commits.
"""

from __future__ import annotations

from decimal import Decimal

from flask import g, has_request_context
from sqlalchemy import func

from extensions import db
from models import Inventory, StockMovement

ZERO = Decimal("0")

VALID_REASONS = {
    "initial", "invoice_created", "invoice_deleted", "invoice_edited",
    "quotation_converted", "stock_in", "stock_out", "damage", "expiry",
    "return", "rtv", "correction",
}

INVOICE_SALE_REASONS = ("invoice_created", "invoice_edited", "quotation_converted")


def get_current_user_id() -> int | None:
    """Id of the logged-in user, or None (auth switched off / no request)."""
    if not has_request_context():
        return None
    user = getattr(g, "current_user", None)
    return getattr(user, "id", None)


def find_inventory_by_name(product_name: str | None, company_id: int | None) -> Inventory | None:
    """Case-insensitive product lookup INSIDE ONE COMPANY's stock. Returns the Inventory row or None.

    Wave 3A: each company has its own stock, so the company is required - a product with the
    same name in another company's stock is never touched."""
    if not product_name or not product_name.strip() or company_id is None:
        return None
    return Inventory.query.filter(
        Inventory.company_id == company_id,
        func.lower(Inventory.product) == product_name.strip().lower(),
    ).first()


def log_movement(
    inventory_id: int,
    product_name: str | None,
    change_qty,
    reason: str,
    reference_id: int | None = None,
    reference_label: str | None = None,
    notes: str | None = None,
    created_by: int | None = None,
) -> StockMovement:
    """Apply a stock change and record it. Does NOT commit — the caller commits.

    The balance never goes below 0. If a change would push it below 0, the
    balance is floored at 0 and the row records the REAL change that happened
    (not the requested one), with a note saying how much was short. That keeps
    balance_after = previous balance + change_qty true on every row.
    """
    if reason not in VALID_REASONS:
        raise ValueError(f"Unknown stock movement reason: {reason!r}")

    inv = Inventory.query.get(inventory_id)
    if inv is None:
        raise ValueError(f"No inventory product with id {inventory_id}.")

    requested = Decimal(str(change_qty))
    old_balance = inv.balance if inv.balance is not None else ZERO
    new_balance = old_balance + requested

    if new_balance < 0:
        short_by = -new_balance
        extra = f"Stock short by {short_by}: balance floored at 0 (requested change {requested})."
        notes = f"{notes} | {extra}" if notes else extra
        new_balance = ZERO

    actual_change = new_balance - old_balance
    inv.balance = new_balance

    movement = StockMovement(
        inventory_id=inv.id,
        product_name=product_name or inv.product,
        change_qty=actual_change,
        balance_after=new_balance,
        reason=reason,
        reference_id=reference_id,
        reference_label=reference_label,
        notes=notes,
        created_by=created_by if created_by is not None else get_current_user_id(),
    )
    db.session.add(movement)
    return movement


def net_logged_change_for_invoice(invoice_id: int) -> dict[int, Decimal]:
    """Net stock change logged for one invoice, per inventory product.

    Example: invoice sold 10 (-10), was edited down to 6 (+4) -> {product_id: -6}.
    Used on delete so we give back exactly what the invoice really took.
    Returns {} for old invoices created before Phase 1 (they have no rows).
    """
    rows = (
        db.session.query(StockMovement.inventory_id, func.sum(StockMovement.change_qty))
        .filter(
            StockMovement.reference_id == invoice_id,
            StockMovement.reason.in_(INVOICE_SALE_REASONS),
        )
        .group_by(StockMovement.inventory_id)
        .all()
    )
    return {inv_id: Decimal(str(total)) for inv_id, total in rows if total is not None}
