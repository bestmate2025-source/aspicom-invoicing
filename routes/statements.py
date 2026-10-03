"""
routes/statements.py
Client Account Statement endpoint.

GET /api/clients/<client_id>/statement?from=YYYY-MM-DD&to=YYYY-MM-DD
(registered in app.py with url_prefix="/api")

Rules (agreed with Farhan):
  - Only document_type = 'invoice' (quotations are excluded).
  - Only invoices whose status is in VISIBLE_STATUSES count. Drafts are
    invisible, and so are their payments.
  - All money uses Decimal, quantized to 2 DP (ROUND_HALF_UP),
    and is returned as strings such as "1825.00".
"""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, jsonify, request

from models import Client, Invoice, InvoicePayment

statements_bp = Blueprint("statements", __name__)

# One place that decides which invoices count (opening balance,
# transactions list, aging).
VISIBLE_STATUSES = {"Sent", "Paid", "Overdue"}
# Aging only looks at invoices that can still be unpaid.
AGING_STATUSES = {"Sent", "Overdue"}

TWO_DP = Decimal("0.01")
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------
def D(value):
    """Any value -> Decimal with 2 decimal places (ROUND_HALF_UP)."""
    if value is None or value == "":
        return ZERO
    return Decimal(str(value)).quantize(TWO_DP, rounding=ROUND_HALF_UP)


def money(value):
    """Decimal -> JSON string with exactly 2 decimals."""
    return str(D(value))


def ok(data):
    return jsonify({"data": data, "error": None, "message": None}), 200


def fail(status, code, message):
    return jsonify({"data": None, "error": code, "message": message}), status


def to_date(value):
    """DB value (date / datetime / 'YYYY-MM-DD' string / None) -> date or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def parse_query_date(raw, name):
    """'YYYY-MM-DD' string -> date. Raises ValueError with a clear message."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"Invalid '{name}' date '{raw}'. Use YYYY-MM-DD.")


def month_start(d):
    return d.replace(day=1)


def month_end(d):
    return d.replace(day=monthrange(d.year, d.month)[1])


def resolve_period(raw_from, raw_to):
    """Apply the defaulting rules from the spec."""
    d_from = parse_query_date(raw_from, "from") if raw_from else None
    d_to = parse_query_date(raw_to, "to") if raw_to else None

    if d_from is None and d_to is None:
        today = date.today()
        d_from, d_to = month_start(today), month_end(today)
    elif d_from is None:
        d_from = month_start(d_to)
    elif d_to is None:
        d_to = month_end(d_from)

    if d_from > d_to:
        raise ValueError("'from' date must not be after 'to' date.")
    return d_from, d_to


# ---------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------
@statements_bp.route("/clients/<int:client_id>/statement", methods=["GET"])
def client_statement(client_id):
    try:
        d_from, d_to = resolve_period(request.args.get("from"),
                                      request.args.get("to"))
    except ValueError as e:
        return fail(400, "bad_request", str(e))

    try:
        statuses = sorted(VISIBLE_STATUSES)

        # ---- client -------------------------------------------------
        client = Client.query.filter_by(id=client_id).first()
        if client is None:
            return fail(404, "not_found", "Client not found")

        # ---- invoices (invoices only, visible statuses only) -------
        inv_rows = Invoice.query.filter(
            Invoice.client_id == client_id,
            Invoice.document_type == "invoice",
            Invoice.status.in_(statuses),
        ).all()

        # ---- payments on those invoices ----------------------------
        pay_rows = (
            InvoicePayment.query
            .join(Invoice, InvoicePayment.invoice_id == Invoice.id)
            .filter(
                Invoice.client_id == client_id,
                Invoice.document_type == "invoice",
                Invoice.status.in_(statuses),
            )
            .all()
        )

        inv_by_id = {r.id: r for r in inv_rows}

        # ---- build one flat list of events -------------------------
        # sort key: (date, invoice-before-payment, id)
        events = []
        for r in inv_rows:
            ev_date = to_date(r.issued)
            if ev_date is None:
                continue  # an invoice with no issue date cannot be placed
            events.append({
                "date": ev_date, "order": 0, "id": r.id, "type": "invoice",
                "reference": r.invoice_number or "",
                "invoice_id": r.id,
                "invoice_number": r.invoice_number or "",
                "debit": D(r.total), "credit": ZERO,
            })
        for r in pay_rows:
            inv = inv_by_id.get(r.invoice_id)
            if inv is None:
                continue
            ev_date = to_date(r.paid_date) or to_date(inv.issued)
            if ev_date is None:
                continue
            events.append({
                "date": ev_date, "order": 1, "id": r.id, "type": "payment",
                "reference": r.reference or "",
                "invoice_id": r.invoice_id,
                "invoice_number": inv.invoice_number or "",
                "debit": ZERO, "credit": D(r.amount),
            })
        events.sort(key=lambda e: (e["date"], e["order"], e["id"]))

        # ---- opening balance (everything before `from`) ------------
        opening = ZERO
        for e in events:
            if e["date"] < d_from:
                opening += e["debit"] - e["credit"]
        opening = D(opening)

        # ---- transactions inside the period + running balance ------
        balance = opening
        total_debit = ZERO
        total_credit = ZERO
        transactions = []
        for e in events:
            if e["date"] < d_from or e["date"] > d_to:
                continue
            balance = D(balance + e["debit"] - e["credit"])
            total_debit += e["debit"]
            total_credit += e["credit"]
            transactions.append({
                "date": e["date"].isoformat(),
                "type": e["type"],
                "reference": e["reference"],
                "invoice_id": e["invoice_id"],
                "invoice_number": e["invoice_number"],
                "debit": money(e["debit"]),
                "credit": money(e["credit"]),
                "balance": money(balance),
            })
        closing = D(balance)

        # ---- aging (all unpaid Sent/Overdue invoices, any date) ----
        today = date.today()
        buckets = {"current": ZERO, "days_1_30": ZERO, "days_31_60": ZERO,
                   "days_61_90": ZERO, "days_90_plus": ZERO}
        for r in inv_rows:
            if r.status not in AGING_STATUSES:
                continue
            total = D(r.total)
            paid = D(r.amount_paid)
            if paid >= total:
                continue
            owed = total - paid

            base = to_date(r.due) or to_date(r.issued)
            days_overdue = (today - base).days if base else 0

            if days_overdue <= 0:
                key = "current"
            elif days_overdue <= 30:
                key = "days_1_30"
            elif days_overdue <= 60:
                key = "days_31_60"
            elif days_overdue <= 90:
                key = "days_61_90"
            else:
                key = "days_90_plus"
            buckets[key] += owed

        aging_total = sum(buckets.values(), ZERO)
        aging = {k: money(v) for k, v in buckets.items()}
        aging["total"] = money(aging_total)

        # ---- response ----------------------------------------------
        return ok({
            "client": {
                "id": client.id,
                "name": client.name,
                "trn": client.trn,
                "address": client.address,
                "po_box": client.po_box,
                "email": client.email,
            },
            "period": {"from": d_from.isoformat(), "to": d_to.isoformat()},
            "opening_balance": money(opening),
            "closing_balance": money(closing),
            "totals": {
                "invoiced": money(total_debit),
                "paid": money(total_credit),
                "outstanding": money(closing),
            },
            "aging": aging,
            "transactions": transactions,
        })

    except Exception as e:  # keep the {data,error,message} envelope on crashes
        return fail(500, "server_error", f"Could not build statement: {e}")
