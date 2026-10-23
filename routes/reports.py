"""Routes for /api/reports (ERP Phase 5 - Reports and Analytics).

Registered in app.py with url_prefix="/api/reports".

  GET /api/reports/inventory-valuation          stock value at cost
  GET /api/reports/stock-aging                  how long stock has been sitting
  GET /api/reports/sales-summary?from=&to=      invoiced sales, per currency and per client

All three are READ-ONLY. Login is enforced by the app-wide before_request in
app.py (any logged-in admin or user may read), so no extra decorator is needed.

Honest notes about what the numbers mean:
  * Cost comes from the latest PO line that actually received goods
    (qty_received > 0, PO not Cancelled). A product never purchased through a
    PO falls back to its selling price in Inventory and is marked
    cost_source = "selling_price" so you can see it is not a true cost.
  * Stock aging measures days since the latest 'initial' / 'stock_in'
    movement. It shows how long ago stock last arrived, not how fast it sells.
  * 1 company = 1 currency. Sales are grouped by company AND currency (the
    currency stored on each invoice), so USD and AED are never added together.
  * Inventory and purchase orders carry no company or currency of their own
    (the warehouse is shared). Valuation and aging values are therefore shown
    in ONE currency: the currency of the primary company (lowest id).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, request
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Client, Company, Inventory, Invoice, PurchaseOrder, PurchaseOrderItem, StockMovement
from utils.responses import api_response

reports_bp = Blueprint("reports", __name__)

SALES_STATUSES = ("Sent", "Paid", "Overdue")
AGING_REASONS = ("initial", "stock_in")
FAST_MAX_DAYS = 30   # fewer than 30 days            -> fast
DEAD_MIN_DAYS = 90   # more than 90 days             -> dead; 30..90 inclusive -> slow

_CENT = Decimal("0.01")


# ----------------------------------------------------------------------
# Pure helpers (no database, easy to test)
# ----------------------------------------------------------------------
def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value) -> str:
    return str(_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def _name_key(name) -> str:
    return " ".join(str(name or "").split()).lower()


def latest_costs(po_lines) -> dict:
    """po_lines: (product_name, unit_price) pairs ordered OLDEST to NEWEST.
    Returns {normalised name: latest unit price} - the newest line wins."""
    costs = {}
    for name, price in po_lines:
        costs[_name_key(name)] = _dec(price)
    return costs


def age_group(days: int) -> str:
    if days < FAST_MAX_DAYS:
        return "fast"
    if days > DEAD_MIN_DAYS:
        return "dead"
    return "slow"


def parse_date(text, label):
    """'' / None -> (None, None).  'YYYY-MM-DD' -> (date, None).  Bad -> (None, message)."""
    if text is None or str(text).strip() == "":
        return None, None
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"'{label}' must be a date like 2026-10-31."


def build_scope(company_id, company_name, currency) -> dict:
    """Valuation and aging have no company of their own - see the module notes."""
    return {
        "company_id": company_id,
        "company_name": company_name,
        "currency": currency or "USD",
        "note": "Stock is shared by all companies, so it is valued in the primary company's currency.",
    }


def build_valuation(items, costs, scope=None) -> dict:
    """items: (product, balance, selling_price). costs: from latest_costs()."""
    rows, total, units = [], Decimal("0"), Decimal("0")
    for product, balance, price in items:
        bal = _dec(balance)
        if bal <= 0:
            continue
        key = _name_key(product)
        if key in costs:
            unit, source = costs[key], "purchase"
        else:
            unit, source = _dec(price), "selling_price"
        value = (bal * unit).quantize(_CENT, rounding=ROUND_HALF_UP)
        total += value
        units += bal
        rows.append({
            "product_name": product,
            "balance": _money(bal),
            "unit_cost": _money(unit),
            "total_value": _money(value),
            "cost_source": source,
        })
    rows.sort(key=lambda r: Decimal(r["total_value"]), reverse=True)
    scope = scope or build_scope(None, None, "USD")
    return {
        "currency": scope["currency"],
        "scope": scope,
        "by_company": [{
            "company_id": scope["company_id"], "company_name": scope["company_name"],
            "currency": scope["currency"], "total_value": _money(total),
            "total_items": len(rows), "total_units": _money(units),
        }],
        "total_value": _money(total),
        "total_items": len(rows),
        "total_units": _money(units),
        "items_without_purchase_cost": sum(1 for r in rows if r["cost_source"] == "selling_price"),
        "items": rows,
    }


def build_aging(items, last_in, first_any, costs, now, scope=None) -> dict:
    """items: (inventory_id, product, balance, price, updated_at).
    last_in / first_any: {inventory_id: datetime}. now: datetime."""
    rows = []
    summary = {g: {"count": 0, "units": Decimal("0"), "value": Decimal("0")} for g in ("fast", "slow", "dead")}
    for inv_id, product, balance, price, updated_at in items:
        bal = _dec(balance)
        if bal <= 0:
            continue
        if inv_id in last_in:
            moved, basis = last_in[inv_id], "stock_in"
        elif inv_id in first_any:
            moved, basis = first_any[inv_id], "first_movement"
        else:
            moved, basis = updated_at or now, "item_updated"
        days = max((now - moved).days, 0)
        group = age_group(days)
        unit = costs.get(_name_key(product), _dec(price))
        value = (bal * unit).quantize(_CENT, rounding=ROUND_HALF_UP)
        summary[group]["count"] += 1
        summary[group]["units"] += bal
        summary[group]["value"] += value
        rows.append({
            "product_name": product,
            "balance": _money(bal),
            "last_movement_date": moved.strftime("%Y-%m-%d"),
            "days_since_last_movement": days,
            "category": group,
            "basis": basis,
            "total_value": _money(value),
        })
    rows.sort(key=lambda r: r["days_since_last_movement"], reverse=True)
    scope = scope or build_scope(None, None, "USD")
    return {
        "currency": scope["currency"],
        "scope": scope,
        "summary": {g: {"count": s["count"], "units": _money(s["units"]), "value": _money(s["value"])}
                    for g, s in summary.items()},
        "items": rows,
    }


def build_sales(groups) -> dict:
    """groups: (company_id, company_name, currency, client_id, client_name, total, vat, invoice_count),
    one row per company + currency + client."""
    blocks = {}
    for company_id, company_name, currency, client_id, client_name, total, vat, count in groups:
        cur = currency or "USD"
        b = blocks.setdefault((company_id, cur), {
            "company_id": company_id, "company_name": company_name or "(unknown)", "currency": cur,
            "total": Decimal("0"), "vat": Decimal("0"), "invoice_count": 0, "clients": []})
        t, v = _dec(total), _dec(vat)
        b["total"] += t
        b["vat"] += v
        b["invoice_count"] += int(count or 0)
        b["clients"].append({
            "client_id": client_id,
            "client_name": client_name or "(unknown)",
            "invoice_count": int(count or 0),
            "revenue": _money(t - v),
            "vat": _money(v),
            "total_amount": _money(t),
        })
    out = []
    for key in sorted(blocks, key=lambda k: (blocks[k]["company_name"].lower(), k[1])):
        b = blocks[key]
        b["clients"].sort(key=lambda c: Decimal(c["total_amount"]), reverse=True)
        out.append({
            "company_id": b["company_id"],
            "company_name": b["company_name"],
            "currency": b["currency"],
            "revenue": _money(b["total"] - b["vat"]),
            "vat": _money(b["vat"]),
            "total": _money(b["total"]),
            "invoice_count": b["invoice_count"],
            "clients": b["clients"],
        })
    # One grand number only when there is exactly one company+currency group: never mix currencies.
    single = len(out) == 1
    return {
        "invoice_count": sum(b["invoice_count"] for b in out),
        "mixed_currencies": len({b["currency"] for b in out}) > 1,
        "total_revenue": out[0]["revenue"] if single else (None if out else "0.00"),
        "total_vat": out[0]["vat"] if single else (None if out else "0.00"),
        "by_company": out,
    }


# ----------------------------------------------------------------------
# Database readers
# ----------------------------------------------------------------------
def _load_costs() -> dict:
    q = (
        db.session.query(PurchaseOrderItem.product_name, PurchaseOrderItem.unit_price)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.po_id)
        .filter(PurchaseOrderItem.qty_received > 0, PurchaseOrder.status != "Cancelled")
        .order_by(PurchaseOrder.order_date.asc(), PurchaseOrder.id.asc(), PurchaseOrderItem.id.asc())
    )
    return latest_costs(q.all())


def _load_scope() -> dict:
    row = db.session.query(Company.id, Company.name, Company.currency).order_by(Company.id.asc()).first()
    if row is None:
        return build_scope(None, None, "USD")
    return build_scope(row[0], row[1], row[2])


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@reports_bp.route("/inventory-valuation", methods=["GET"])
def inventory_valuation():
    try:
        items = db.session.query(Inventory.product, Inventory.balance, Inventory.price).all()
        data = build_valuation(items, _load_costs(), _load_scope())
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the valuation report."), 500
    return api_response(data=data)


@reports_bp.route("/stock-aging", methods=["GET"])
def stock_aging():
    try:
        items = db.session.query(
            Inventory.id, Inventory.product, Inventory.balance, Inventory.price, Inventory.updated_at
        ).filter(Inventory.balance > 0).all()
        last_in = dict(
            db.session.query(StockMovement.inventory_id, func.max(StockMovement.created_at))
            .filter(StockMovement.reason.in_(AGING_REASONS))
            .group_by(StockMovement.inventory_id).all()
        )
        first_any = dict(
            db.session.query(StockMovement.inventory_id, func.min(StockMovement.created_at))
            .group_by(StockMovement.inventory_id).all()
        )
        data = build_aging(items, last_in, first_any, _load_costs(), datetime.utcnow(), _load_scope())
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the stock aging report."), 500
    return api_response(data=data)


@reports_bp.route("/sales-summary", methods=["GET"])
def sales_summary():
    d_from, err = parse_date(request.args.get("from"), "from")
    if err:
        return api_response(error="validation_error", message=err), 400
    d_to, err = parse_date(request.args.get("to"), "to")
    if err:
        return api_response(error="validation_error", message=err), 400
    if d_from and d_to and d_from > d_to:
        return api_response(error="validation_error", message="'from' must not be after 'to'."), 400

    try:
        q = (
            db.session.query(
                Invoice.company_id, Company.name, Invoice.currency, Invoice.client_id, Client.name,
                func.sum(Invoice.total), func.sum(Invoice.vat), func.count(Invoice.id),
            )
            .join(Client, Client.id == Invoice.client_id)
            .join(Company, Company.id == Invoice.company_id)
            .filter(Invoice.document_type == "invoice", Invoice.status.in_(SALES_STATUSES))
        )
        if d_from:
            q = q.filter(Invoice.issued >= d_from)
        if d_to:
            q = q.filter(Invoice.issued <= d_to)
        q = q.group_by(Invoice.company_id, Company.name, Invoice.currency, Invoice.client_id, Client.name)
        data = build_sales(q.all())
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the sales report."), 500

    data["from"] = d_from.isoformat() if d_from else None
    data["to"] = d_to.isoformat() if d_to else None
    return api_response(data=data)
