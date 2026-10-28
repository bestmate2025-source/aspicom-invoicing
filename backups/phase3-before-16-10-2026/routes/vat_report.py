"""routes/vat_report.py
Company-wide VAT report.

GET /api/vat-report?from=YYYY-MM-DD&to=YYYY-MM-DD&company_id=&client_id=&currency=
(registered in app.py with url_prefix="/api")

Rules:
  - Only document_type = 'invoice' (quotations never count).
  - Only invoices with a status in VISIBLE_STATUSES (Drafts are excluded);
    the constant is shared with routes/statements.py so both reports agree.
  - Date filter uses invoices.issued; if issued is empty, created_at is used.
  - All money uses Decimal, 2 DP ROUND_HALF_UP, returned as strings.

Money columns, per invoice and in every total:
  subtotal = invoices.subtotal      (sum of line amounts, BEFORE discount)
  discount = subtotal + vat - total (derived)
  vat      = invoices.vat           (VAT charged, calculated after discount)
  taxable  = total - vat            (value the VAT was charged on = subtotal - discount)
  total    = invoices.total
Note: subtotal + vat equals total only when there is no discount.

Currencies are NOT converted. Totals add the numbers as they are, so when
USD and AED invoices are both in range, use `by_currency` (or the optional
`currency` filter) for meaningful figures.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, request
from sqlalchemy import func

from extensions import db
from models import Client, Company, Invoice
from routes.statements import VISIBLE_STATUSES, D, fail, money, ok, resolve_period

vat_report_bp = Blueprint("vat_report", __name__)

ZERO = Decimal("0.00")
MONEY_KEYS = ("subtotal", "discount", "vat", "taxable", "total")


def _blank() -> dict:
    return {k: ZERO for k in MONEY_KEYS}


def _add(acc: dict, parts: dict) -> None:
    for k in MONEY_KEYS:
        acc[k] += parts[k]


def _as_strings(acc: dict) -> dict:
    return {k: money(acc[k]) for k in MONEY_KEYS}


def _optional_int(name: str):
    """Query param -> int, or None when absent/empty. Raises ValueError if not a number."""
    raw = (request.args.get(name) or "").strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"'{name}' must be a whole number.")


def _optional_currency():
    raw = (request.args.get("currency") or "").strip().upper()
    if raw == "":
        return None
    if len(raw) != 3 or not raw.isalpha():
        raise ValueError("'currency' must be a 3-letter code such as USD or AED.")
    return raw


@vat_report_bp.route("/vat-report", methods=["GET"])
def vat_report():
    try:
        d_from, d_to = resolve_period(request.args.get("from"), request.args.get("to"))
        company_id = _optional_int("company_id")
        client_id = _optional_int("client_id")
        currency = _optional_currency()
    except ValueError as e:
        return fail(400, "bad_request", str(e))

    try:
        # issued, or created_at's date when issued is empty
        effective_date = func.coalesce(Invoice.issued, func.date(Invoice.created_at))

        query = (
            db.session.query(Invoice, Client.name, Company.name)
            .join(Client, Invoice.client_id == Client.id)
            .outerjoin(Company, Invoice.company_id == Company.id)
            .filter(
                Invoice.document_type == "invoice",
                Invoice.status.in_(sorted(VISIBLE_STATUSES)),
                effective_date >= d_from,
                effective_date <= d_to,
            )
        )
        if company_id is not None:
            query = query.filter(Invoice.company_id == company_id)
        if client_id is not None:
            query = query.filter(Invoice.client_id == client_id)
        if currency is not None:
            query = query.filter(Invoice.currency == currency)

        rows = []
        for inv, client_name, company_name in query.all():
            inv_date = inv.issued
            if inv_date is None and inv.created_at is not None:
                inv_date = inv.created_at.date() if isinstance(inv.created_at, datetime) else inv.created_at
            rows.append((inv_date or date.min, inv.id, inv, client_name, company_name))
        rows.sort(key=lambda r: (r[0], r[1]))

        totals = _blank()
        by_company: dict = {}
        by_currency: dict = {}
        invoices = []

        for inv_date, _id, inv, client_name, company_name in rows:
            subtotal, vat, total = D(inv.subtotal), D(inv.vat), D(inv.total)
            parts = {
                "subtotal": subtotal,
                "discount": D(subtotal + vat - total),
                "vat": vat,
                "taxable": D(total - vat),
                "total": total,
            }
            cur = inv.currency or "USD"
            co_name = company_name or f"Company #{inv.company_id}"

            _add(totals, parts)
            entry = by_company.setdefault(
                inv.company_id, {"name": co_name, "count": 0, "acc": _blank()})
            entry["count"] += 1
            _add(entry["acc"], parts)
            cur_entry = by_currency.setdefault(cur, {"count": 0, "acc": _blank()})
            cur_entry["count"] += 1
            _add(cur_entry["acc"], parts)

            invoices.append({
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "date": inv_date.isoformat() if inv_date != date.min else None,
                "client_name": client_name,
                "company_name": co_name,
                "currency": cur,
                **_as_strings(parts),
            })

        return ok({
            "period": {"from": d_from.isoformat(), "to": d_to.isoformat()},
            "totals": {**_as_strings(totals), "invoice_count": len(invoices)},
            "by_company": [
                {"company_id": cid, "company_name": e["name"],
                 "invoice_count": e["count"], **_as_strings(e["acc"])}
                for cid, e in sorted(by_company.items(), key=lambda kv: (kv[0] is None, kv[0]))
            ],
            "by_currency": [
                {"currency": c, "invoice_count": e["count"], **_as_strings(e["acc"])}
                for c, e in sorted(by_currency.items())
            ],
            "invoices": invoices,
        })

    except Exception as e:  # keep the {data,error,message} envelope on crashes
        return fail(500, "server_error", f"Could not build VAT report: {e}")
