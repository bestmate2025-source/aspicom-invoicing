"""Routes for /api/reports (ERP Phase 5 - Reports and Analytics).

Registered in app.py with url_prefix="/api/reports".

  GET /api/reports/inventory-valuation          stock value at cost
  GET /api/reports/stock-aging                  how long stock has been sitting
  GET /api/reports/sales-summary?from=&to=      invoiced sales, per currency and per client
  GET /api/reports/profit-loss?company_id=&from=&to=     Profit & Loss from the books (Wave 3F, admin only)
  GET /api/reports/balance-sheet?company_id=&to=         Balance Sheet from the books (Wave 3F, admin only)

All of them are READ-ONLY. Login is enforced by the app-wide before_request in
app.py (any logged-in admin or user may read the first three). The two financial
reports (Wave 3F) are ADMIN ONLY, like the Accounting tab.

Wave 3F - financial reports read the POSTED journals (status Posted or Reversed, so a reversed
entry and its reversal cancel out), one company at a time, in that company's currency:
  * Accounts are grouped by their TYPE (Asset / Liability / Equity / Income / Expense), so an
    account you add later is included automatically and the Balance Sheet always balances.
  * Profit & Loss: Revenue = Income accounts (4000 Sales Revenue), Cost of goods sold = 5000,
    Expenses = every other Expense account (5100, 5200, 5300, 6000 ... and any you add).
  * Balance Sheet is "as of" the To date and uses ALL history up to that date (From is ignored),
    because Equity = Retained Earnings (3000) + the profit earned up to that date.

Honest notes about what the numbers mean:
  * Cost comes from the latest PO line that actually received goods
    (qty_received > 0, PO not Cancelled). A product never purchased through a
    PO falls back to its selling price in Inventory and is marked
    cost_source = "selling_price" so you can see it is not a true cost.
  * Stock aging measures days since the latest 'initial' / 'stock_in'
    movement. It shows how long ago stock last arrived, not how fast it sells.
  * 1 company = 1 currency. Sales are grouped by company AND currency (the
    currency stored on each invoice), so USD and AED are never added together.
  * Wave 3A: every company has its own stock and its own purchase orders. Valuation and
    aging are per company (?company_id=N, default = the primary company, lowest id) and
    shown in that company's currency. Costs come from that company's own POs.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from flask import Blueprint, g, request
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    Account, Client, Company, Inventory, Invoice, JournalEntry, JournalLine, PurchaseOrder, PurchaseOrderItem,
    StockMovement,
)
from utils.auth import get_current_user
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
    """Valuation and aging are for ONE company - see the module notes."""
    return {
        "company_id": company_id,
        "company_name": company_name,
        "currency": currency or "USD",
        "note": "This company's own stock, valued in the company's currency.",
    }


def build_valuation(items, costs, scope=None) -> dict:
    """items: (product, balance, selling_price[, average_cost]). costs: from latest_costs().
    Cost used: the average cost if the product has one, else the latest purchase price, else the selling price."""
    rows, total, units = [], Decimal("0"), Decimal("0")
    for entry in items:
        product, balance, price = entry[0], entry[1], entry[2]
        avg = _dec(entry[3]) if len(entry) > 3 else Decimal("0")
        bal = _dec(balance)
        if bal <= 0:
            continue
        key = _name_key(product)
        if avg > 0:
            unit, source = avg, "average_cost"
        elif key in costs:
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
        "items_with_average_cost": sum(1 for r in rows if r["cost_source"] == "average_cost"),
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
def _load_costs(company_id) -> dict:
    q = (
        db.session.query(PurchaseOrderItem.product_name, PurchaseOrderItem.unit_price)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.po_id)
        .filter(PurchaseOrderItem.qty_received > 0, PurchaseOrder.status != "Cancelled",
                PurchaseOrder.company_id == company_id)
        .order_by(PurchaseOrder.order_date.asc(), PurchaseOrder.id.asc(), PurchaseOrderItem.id.asc())
    )
    return latest_costs(q.all())


def _load_scope():
    """(scope, error_response). ?company_id=N picks the company; no value = the first company."""
    raw = request.args.get("company_id")
    query = db.session.query(Company.id, Company.name, Company.currency)
    if raw not in (None, ""):
        if not raw.isdigit():
            return None, (api_response(error="validation_error", message="'company_id' must be a whole number."), 400)
        row = query.filter(Company.id == int(raw)).first()
        if row is None:
            return None, (api_response(error="not_found", message=f"No company with id {raw}."), 404)
    else:
        row = query.order_by(Company.id.asc()).first()
    if row is None:
        return build_scope(None, None, "USD"), None
    return build_scope(row[0], row[1], row[2]), None


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@reports_bp.route("/inventory-valuation", methods=["GET"])
def inventory_valuation():
    scope, problem = _load_scope()
    if problem:
        return problem
    try:
        items = db.session.query(Inventory.product, Inventory.balance, Inventory.price, Inventory.avg_cost).filter(
            Inventory.company_id == scope["company_id"]).all()
        data = build_valuation(items, _load_costs(scope["company_id"]), scope)
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the valuation report."), 500
    return api_response(data=data)


@reports_bp.route("/stock-aging", methods=["GET"])
def stock_aging():
    scope, problem = _load_scope()
    if problem:
        return problem
    try:
        items = db.session.query(
            Inventory.id, Inventory.product, Inventory.balance, Inventory.price, Inventory.updated_at
        ).filter(Inventory.balance > 0, Inventory.company_id == scope["company_id"]).all()
        last_in = dict(
            db.session.query(StockMovement.inventory_id, func.max(StockMovement.created_at))
            .filter(StockMovement.reason.in_(AGING_REASONS))
            .group_by(StockMovement.inventory_id).all()
        )
        first_any = dict(
            db.session.query(StockMovement.inventory_id, func.min(StockMovement.created_at))
            .group_by(StockMovement.inventory_id).all()
        )
        data = build_aging(items, last_in, first_any, _load_costs(scope["company_id"]), datetime.utcnow(), scope)
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


# ======================================================================
# Wave 3F - Profit & Loss and Balance Sheet (from the journals)
# ======================================================================
CODE_SALES = "4000"
CODE_COGS = "5000"
PL_NAMED_EXPENSES = ("5100", "5200", "5300", "6000")          # always listed, even when 0
BS_NAMED = {
    "Asset": ("1000", "1100", "1200", "1300", "1400"),
    "Liability": ("2000", "2100"),
    "Equity": ("3000",),
}
_TWO = Decimal("0.01")


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _m(value: Decimal) -> str:
    return f"{value.quantize(_TWO, rounding=ROUND_HALF_UP):.2f}"


def _clean_rows(rows):
    """rows = (code, name, type, debit, credit) -> same with Decimals."""
    return [(code, name, typ, _d(debit), _d(credit)) for code, name, typ, debit, credit in rows]


def build_profit_loss(rows) -> dict:
    """Pure. Revenue - COGS = Gross profit; Gross profit - Expenses = Net profit. All amounts are Decimals."""
    revenue_lines, expense_lines = [], []
    revenue = cogs = expenses = Decimal("0")
    for code, name, typ, debit, credit in _clean_rows(rows):
        if typ == "Income":
            amount = credit - debit
            if amount != 0 or code == CODE_SALES:
                revenue_lines.append({"code": code, "name": name, "amount": amount})
            revenue += amount
        elif typ == "Expense":
            amount = debit - credit
            if code == CODE_COGS:
                cogs += amount
            else:
                if amount != 0 or code in PL_NAMED_EXPENSES:
                    expense_lines.append({"code": code, "name": name, "amount": amount})
                expenses += amount
    gross = revenue - cogs
    net = gross - expenses
    return {
        "revenue": revenue, "revenue_lines": revenue_lines, "cogs": cogs, "gross_profit": gross,
        "expenses": expense_lines, "total_expenses": expenses, "net_profit": net,
    }


def build_balance_sheet(rows) -> dict:
    """Pure. Assets = Liabilities + Equity, where Equity includes the profit earned to date."""
    out = {"Asset": [], "Liability": [], "Equity": []}
    totals = {"Asset": Decimal("0"), "Liability": Decimal("0"), "Equity": Decimal("0")}
    profit = Decimal("0")
    for code, name, typ, debit, credit in _clean_rows(rows):
        if typ == "Income":
            profit += credit - debit
        elif typ == "Expense":
            profit -= debit - credit
        elif typ in out:
            amount = (debit - credit) if typ == "Asset" else (credit - debit)
            if amount != 0 or code in BS_NAMED[typ]:
                out[typ].append({"code": code, "name": name, "amount": amount})
            totals[typ] += amount
    equity = list(out["Equity"])
    equity.append({"code": None, "name": "Profit to date (current earnings)", "amount": profit})
    total_equity = totals["Equity"] + profit
    right = totals["Liability"] + total_equity
    return {
        "assets": out["Asset"], "total_assets": totals["Asset"],
        "liabilities": out["Liability"], "total_liabilities": totals["Liability"],
        "equity": equity, "total_equity": total_equity,
        "net_profit_to_date": profit,
        "difference": totals["Asset"] - right,
        "balanced": totals["Asset"] == right,
    }


def _lines_out(lines):
    return [{"code": ln["code"], "name": ln["name"], "amount": _m(ln["amount"])} for ln in lines]


def admin_only(fn):
    """Financial reports: a valid admin token is required (same rule as the Accounting tab)."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        actor = get_current_user()
        if actor is None:
            return api_response(error="unauthorized", message="Authentication required."), 401
        if actor.role != "admin":
            return api_response(error="forbidden", message="Admin access required."), 403
        g.current_user = actor
        return fn(*args, **kwargs)

    return wrapper


def _account_rows(company_id, date_from=None, date_to=None):
    """(code, name, type, debit, credit) for EVERY account of the company (0 when nothing was posted)."""
    query = (
        db.session.query(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .filter(JournalEntry.company_id == company_id, JournalEntry.status.in_(("Posted", "Reversed")))
    )
    if date_from:
        query = query.filter(JournalEntry.entry_date >= date_from)
    if date_to:
        query = query.filter(JournalEntry.entry_date <= date_to)
    sums = {aid: (_d(dr), _d(cr)) for aid, dr, cr in query.group_by(JournalLine.account_id)}
    rows = []
    for account in Account.query.filter(Account.company_id == company_id).order_by(Account.code).all():
        debit, credit = sums.get(account.id, (Decimal("0"), Decimal("0")))
        rows.append((account.code, account.name, account.type, debit, credit))
    return rows


def _fin_scope():
    """(scope, date_from, date_to, error_response)."""
    scope, problem = _load_scope()
    if problem:
        return None, None, None, problem
    date_from, msg = parse_date(request.args.get("from"), "from")
    if msg:
        return None, None, None, (api_response(error="validation_error", message=msg), 400)
    date_to, msg = parse_date(request.args.get("to"), "to")
    if msg:
        return None, None, None, (api_response(error="validation_error", message=msg), 400)
    if date_from and date_to and date_from > date_to:
        return None, None, None, (api_response(error="validation_error", message="'from' must not be after 'to'."), 400)
    return scope, date_from, date_to, None


@reports_bp.route("/profit-loss", methods=["GET"])
@admin_only
def profit_loss():
    scope, date_from, date_to, problem = _fin_scope()
    if problem:
        return problem
    try:
        pl = build_profit_loss(_account_rows(scope["company_id"], date_from, date_to))
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the Profit & Loss."), 500
    return api_response(data={
        "company_id": scope["company_id"], "company_name": scope["company_name"], "currency": scope["currency"],
        "from": date_from.isoformat() if date_from else None, "to": date_to.isoformat() if date_to else None,
        "revenue": _m(pl["revenue"]), "revenue_lines": _lines_out(pl["revenue_lines"]),
        "cogs": _m(pl["cogs"]), "gross_profit": _m(pl["gross_profit"]),
        "expenses": _lines_out(pl["expenses"]), "total_expenses": _m(pl["total_expenses"]),
        "net_profit": _m(pl["net_profit"]),
    })


@reports_bp.route("/balance-sheet", methods=["GET"])
@admin_only
def balance_sheet():
    scope, _date_from, date_to, problem = _fin_scope()     # 'from' is accepted but not used: a balance sheet is "as of"
    if problem:
        return problem
    try:
        bs = build_balance_sheet(_account_rows(scope["company_id"], None, date_to))
    except SQLAlchemyError:
        db.session.rollback()
        return api_response(error="database_error", message="Could not build the Balance Sheet."), 500
    return api_response(data={
        "company_id": scope["company_id"], "company_name": scope["company_name"], "currency": scope["currency"],
        "as_of": (date_to or date.today()).isoformat(),
        "assets": _lines_out(bs["assets"]), "total_assets": _m(bs["total_assets"]),
        "liabilities": _lines_out(bs["liabilities"]), "total_liabilities": _m(bs["total_liabilities"]),
        "equity": _lines_out(bs["equity"]), "total_equity": _m(bs["total_equity"]),
        "net_profit_to_date": _m(bs["net_profit_to_date"]),
        "balanced": bs["balanced"], "difference": _m(bs["difference"]),
    })
