"""Routes for /api/invoices — the big one.

Business rules enforced HERE, never trusted from the client:
  - subtotal/vat/total are always recomputed server-side from the items
    list. If a client sends its own totals anyway, they're simply never
    read (not whitelisted as top-level fields — see unknown_fields below).
  - Each item's `amount` is always unit_price * qty, computed here. A
    client-sent `amount` on an item is silently ignored (items are NOT
    field-whitelisted the way top-level invoice fields are, specifically so
    a stray/wrong `amount` doesn't trip an error — it's just never read).
  - Every multi-table write (invoice + items + audit + inventory) goes
    through exactly ONE db.session.commit() per request. A db.session.flush()
    is used once, on create, purely to obtain the new invoice's
    auto-increment id before the audit row references it — flush() does not
    end the transaction, so this doesn't violate "commit once per request";
    everything still rolls back together if the final commit fails.

Audit trail (invoice_audit):
  - POST   -> one row, action='create'.
  - PUT    -> one row PER changed column (client_id, lpo_number,
              invoice_number, issued, due, status, vat_percent, notes,
              subtotal, vat, total), plus one more if the items list itself
              changed (field_name='items'). Note that changing vat_percent
              (or items) can cascade into subtotal/vat/total also changing,
              which means their own audit rows too — that's intentional:
              "one row per changed field" is applied literally to every
              actually-changed column, derived or not.
  - DELETE -> one row, action='delete', added to the session BEFORE the
              invoice is deleted, then both committed together.

  A changed `status` field is logged as action='status_change' rather than
  'update' — the schema defines that action specifically for this, and it
  was left to my discretion in the spec. Every other changed field uses
  'update'. Flagging the reasoning rather than picking silently.

Inventory: decremented on POST only, matched by case-insensitive product
name, clamped at 0 (never negative), and a missing match never fails the
invoice — it's silently skipped. Never touched on PUT.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import Blueprint, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Client, Inventory, Invoice, InvoiceAudit, InvoiceItem, InvoicePayment
from utils.responses import api_response

invoices_bp = Blueprint("invoices", __name__)

# DELETE /api/payments/<id> lives at a URL that is NOT nested under
# /api/invoices, unlike every other route in this file — so it can't be
# registered on invoices_bp (which app.py mounts with url_prefix="/api/invoices").
# It's defined here anyway, on its own blueprint, to keep all payment logic
# in one file. app.py needs one extra line to register it — see that file.
payments_bp = Blueprint("payments", __name__)

ALLOWED_STATUSES = {"Draft", "Sent", "Paid", "Overdue"}
ALLOWED_PAYMENT_METHODS = {"cash", "bank_transfer", "card", "cheque", "other"}
TOP_LEVEL_EDITABLE_FIELDS = {
    "client_id", "lpo_number", "invoice_number", "issued", "due",
    "status", "vat_percent", "notes", "items",
}
# Stored scalar columns diffed for the per-field audit trail on PUT.
AUDITED_SCALAR_FIELDS = (
    "client_id", "lpo_number", "invoice_number", "issued", "due",
    "status", "vat_percent", "notes", "subtotal", "vat", "total",
)
TWO_DP = Decimal("0.01")


# --- small helpers -----------------------------------------------------


def _to_decimal(value, field_name: str) -> Decimal:
    """Convert via str() first — never float() — to keep binary rounding
    error out of money math. Raises ValueError on anything unparseable."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"'{field_name}': '{value}' is not a valid number.")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def _parse_date(value, field_name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{field_name}' must be an ISO date string ('YYYY-MM-DD').")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"'{field_name}': '{value}' is not a valid ISO date ('YYYY-MM-DD').")


def _audit_str(value) -> str | None:
    """Serialize a column value to text for invoice_audit.old_value/new_value."""
    return None if value is None else str(value)


def _items_signature(items) -> list[dict]:
    """Comparable snapshot of an item list (ORM objects or cleaned dicts), for audit diffing."""
    return [
        {
            "product_name": getattr(i, "product_name", None) or i.get("product_name"),
            "brand": getattr(i, "brand", None) if hasattr(i, "brand") else i.get("brand"),
            "unit_price": str(getattr(i, "unit_price", None) or i.get("unit_price")),
            "qty": str(getattr(i, "qty", None) or i.get("qty")),
        }
        for i in items
    ]


def _validate_items_payload(raw_items) -> tuple[list[dict] | None, str | None]:
    """Validate the nested items list.

    Returns:
        (cleaned_items, None) on success — a list of dicts with keys
        product_name/brand/unit_price/qty, validated and Decimal-typed.
        (None, error_message) on validation failure.
    """
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        return None, "'items' must be a non-empty list."

    cleaned = []
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            return None, f"items[{idx}] must be an object."

        product_name = raw.get("product_name")
        if not isinstance(product_name, str) or not product_name.strip():
            return None, f"items[{idx}].product_name is required and must be a non-empty string."

        brand = raw.get("brand")
        if brand is not None and not isinstance(brand, str):
            return None, f"items[{idx}].brand must be a string."

        if "unit_price" not in raw:
            return None, f"items[{idx}].unit_price is required."
        try:
            unit_price = _to_decimal(raw["unit_price"], f"items[{idx}].unit_price")
        except ValueError as exc:
            return None, str(exc)
        if unit_price < 0:
            return None, f"items[{idx}].unit_price must be >= 0."

        if "qty" not in raw:
            return None, f"items[{idx}].qty is required."
        try:
            qty = _to_decimal(raw["qty"], f"items[{idx}].qty")
        except ValueError as exc:
            return None, str(exc)
        if qty <= 0:
            return None, f"items[{idx}].qty must be > 0."

        # NOTE: any other key (e.g. a client-sent 'amount') is silently
        # dropped here — only these four are ever read. This is the
        # "server recomputes anyway" rule applied at the item level.
        cleaned.append({
            "product_name": product_name.strip(),
            "brand": brand,
            "unit_price": unit_price,
            "qty": qty,
        })

    return cleaned, None


def _compute_totals(items_for_totals: list[dict], vat_percent: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """subtotal/vat/total, always derived here — never trusted from the client."""
    subtotal = _quantize(sum((i["unit_price"] * i["qty"] for i in items_for_totals), Decimal("0")))
    vat = _quantize(subtotal * vat_percent / Decimal("100"))
    total = _quantize(subtotal + vat)
    return subtotal, vat, total


def _decrement_inventory(cleaned_items: list[dict]) -> None:
    """Best-effort stock decrement, matched by case-insensitive product name.

    A missing match is silently skipped. Balance is clamped at 0.
    """
    for item in cleaned_items:
        match = Inventory.query.filter(
            func.lower(Inventory.product) == item["product_name"].lower()
        ).first()
        if match is None:
            continue
        new_balance = match.balance - item["qty"]
        match.balance = new_balance if new_balance > 0 else Decimal("0")


# --- routes --------------------------------------------------------------


@invoices_bp.route("", methods=["GET"])
def list_invoices():
    """GET /api/invoices — list all invoices, newest first.

    Query params:
        status — filter to one of Draft/Sent/Paid/Overdue.
    """
    query = Invoice.query
    status_filter = request.args.get("status")
    if status_filter:
        if status_filter not in ALLOWED_STATUSES:
            return api_response(
                error="invalid_type", message=f"'status' must be one of {sorted(ALLOWED_STATUSES)}."
            ), 400
        query = query.filter(Invoice.status == status_filter)

    invoices = query.order_by(Invoice.created_at.desc()).all()
    return api_response(data=[inv.to_dict() for inv in invoices]), 200


@invoices_bp.route("", methods=["POST"])
def create_invoice():
    """POST /api/invoices — create an invoice with nested items."""
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - TOP_LEVEL_EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    client_id = body.get("client_id")
    if not isinstance(client_id, int):
        return api_response(error="missing_field", message="'client_id' is required and must be an integer."), 400

    invoice_number = body.get("invoice_number")
    if not isinstance(invoice_number, str) or not invoice_number.strip():
        return api_response(
            error="missing_field", message="'invoice_number' is required and must be a non-empty string."
        ), 400
    invoice_number = invoice_number.strip()

    status = body.get("status", "Draft")
    if status not in ALLOWED_STATUSES:
        return api_response(error="invalid_type", message=f"'status' must be one of {sorted(ALLOWED_STATUSES)}."), 400

    try:
        vat_percent = _to_decimal(body.get("vat_percent", 0), "vat_percent")
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400
    if not (Decimal("0") <= vat_percent <= Decimal("100")):
        return api_response(error="invalid_type", message="'vat_percent' must be between 0 and 100."), 400

    try:
        issued = _parse_date(body.get("issued"), "issued")
        due = _parse_date(body.get("due"), "due")
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    lpo_number = body.get("lpo_number")
    if lpo_number is not None and not isinstance(lpo_number, str):
        return api_response(error="invalid_type", message="'lpo_number' must be a string."), 400

    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        return api_response(error="invalid_type", message="'notes' must be a string."), 400

    cleaned_items, err = _validate_items_payload(body.get("items"))
    if err:
        return api_response(error="invalid_type", message=err), 400

    # Proactive checks, before touching the session — gives specific,
    # helpful messages instead of parsing a raw driver-level IntegrityError.
    if Client.query.get(client_id) is None:
        return api_response(
            error="fk_conflict", message=f"No client with id {client_id}. Create the client first."
        ), 409

    if Invoice.query.filter_by(invoice_number=invoice_number).first() is not None:
        return api_response(error="conflict", message=f"Invoice number '{invoice_number}' already exists."), 409

    subtotal, vat, total = _compute_totals(cleaned_items, vat_percent)

    invoice = Invoice(
        client_id=client_id, lpo_number=lpo_number, invoice_number=invoice_number,
        issued=issued, due=due, status=status, vat_percent=vat_percent, notes=notes,
        subtotal=subtotal, vat=vat, total=total,
    )
    for item in cleaned_items:
        invoice.items.append(InvoiceItem(
            product_name=item["product_name"], brand=item["brand"],
            unit_price=item["unit_price"], qty=item["qty"],
            amount=_quantize(item["unit_price"] * item["qty"]),
        ))

    db.session.add(invoice)
    db.session.flush()  # assigns invoice.id — needed by the audit row below; not a commit

    db.session.add(InvoiceAudit(
        invoice_id=invoice.id, action="create", field_name=None, old_value=None,
        new_value=f"invoice_number={invoice_number}, total={total}",
    ))

    _decrement_inventory(cleaned_items)  # best-effort, never blocks the invoice

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(
            error="conflict",
            message=f"Invoice number '{invoice_number}' already exists (race with another request).",
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=invoice.to_dict()), 201


@invoices_bp.route("/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id: int):
    """GET /api/invoices/<id> — fetch one invoice with its items."""
    invoice = Invoice.query.get(invoice_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No invoice with id {invoice_id}."), 404
    return api_response(data=invoice.to_dict()), 200


@invoices_bp.route("/<int:invoice_id>", methods=["PUT"])
def update_invoice(invoice_id: int):
    """PUT /api/invoices/<id> — update. Any subset of the editable fields may
    be sent. subtotal/vat/total are always recomputed from the (possibly
    unchanged) items + vat_percent after applying whatever was sent, so
    they're always internally consistent — never left stale.
    """
    invoice = Invoice.query.get(invoice_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No invoice with id {invoice_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - TOP_LEVEL_EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    new_client_id = invoice.client_id
    if "client_id" in body:
        if not isinstance(body["client_id"], int):
            return api_response(error="invalid_type", message="'client_id' must be an integer."), 400
        if Client.query.get(body["client_id"]) is None:
            return api_response(error="fk_conflict", message=f"No client with id {body['client_id']}."), 409
        new_client_id = body["client_id"]

    new_invoice_number = invoice.invoice_number
    if "invoice_number" in body:
        if not isinstance(body["invoice_number"], str) or not body["invoice_number"].strip():
            return api_response(error="invalid_type", message="'invoice_number' must be a non-empty string."), 400
        new_invoice_number = body["invoice_number"].strip()
        conflict = Invoice.query.filter(
            Invoice.invoice_number == new_invoice_number, Invoice.id != invoice_id
        ).first()
        if conflict is not None:
            return api_response(
                error="conflict", message=f"Invoice number '{new_invoice_number}' already exists."
            ), 409

    new_status = invoice.status
    if "status" in body:
        if body["status"] not in ALLOWED_STATUSES:
            return api_response(
                error="invalid_type", message=f"'status' must be one of {sorted(ALLOWED_STATUSES)}."
            ), 400
        new_status = body["status"]

    new_vat_percent = invoice.vat_percent
    if "vat_percent" in body:
        try:
            new_vat_percent = _to_decimal(body["vat_percent"], "vat_percent")
        except ValueError as exc:
            return api_response(error="invalid_type", message=str(exc)), 400
        if not (Decimal("0") <= new_vat_percent <= Decimal("100")):
            return api_response(error="invalid_type", message="'vat_percent' must be between 0 and 100."), 400

    try:
        new_issued = _parse_date(body["issued"], "issued") if "issued" in body else invoice.issued
        new_due = _parse_date(body["due"], "due") if "due" in body else invoice.due
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    new_lpo_number = invoice.lpo_number
    if "lpo_number" in body:
        if body["lpo_number"] is not None and not isinstance(body["lpo_number"], str):
            return api_response(error="invalid_type", message="'lpo_number' must be a string."), 400
        new_lpo_number = body["lpo_number"]

    new_notes = invoice.notes
    if "notes" in body:
        if body["notes"] is not None and not isinstance(body["notes"], str):
            return api_response(error="invalid_type", message="'notes' must be a string."), 400
        new_notes = body["notes"]

    old_items_snapshot = _items_signature(invoice.items)
    new_cleaned_items = None
    new_signature = None
    if "items" in body:
        new_cleaned_items, err = _validate_items_payload(body["items"])
        if err:
            return api_response(error="invalid_type", message=err), 400
        new_signature = _items_signature(new_cleaned_items)

    # --- capture "before" state for audit diffing, then apply everything ---
    before = {field: getattr(invoice, field) for field in AUDITED_SCALAR_FIELDS}

    invoice.client_id = new_client_id
    invoice.invoice_number = new_invoice_number
    invoice.status = new_status
    invoice.vat_percent = new_vat_percent
    invoice.issued = new_issued
    invoice.due = new_due
    invoice.lpo_number = new_lpo_number
    invoice.notes = new_notes

    items_changed = False
    if new_cleaned_items is not None:
        items_changed = new_signature != old_items_snapshot
        if items_changed:
            invoice.items.clear()  # cascade="all, delete-orphan" issues the DELETEs
            for item in new_cleaned_items:
                invoice.items.append(InvoiceItem(
                    product_name=item["product_name"], brand=item["brand"],
                    unit_price=item["unit_price"], qty=item["qty"],
                    amount=_quantize(item["unit_price"] * item["qty"]),
                ))

    items_for_totals = new_cleaned_items if new_cleaned_items is not None else [
        {"unit_price": i.unit_price, "qty": i.qty} for i in invoice.items
    ]
    subtotal, vat, total = _compute_totals(items_for_totals, new_vat_percent)
    invoice.subtotal = subtotal
    invoice.vat = vat
    invoice.total = total

    after = {field: getattr(invoice, field) for field in AUDITED_SCALAR_FIELDS}
    for field in AUDITED_SCALAR_FIELDS:
        if before[field] != after[field]:
            action = "status_change" if field == "status" else "update"
            db.session.add(InvoiceAudit(
                invoice_id=invoice.id, action=action, field_name=field,
                old_value=_audit_str(before[field]), new_value=_audit_str(after[field]),
            ))
    if items_changed:
        db.session.add(InvoiceAudit(
            invoice_id=invoice.id, action="update", field_name="items",
            old_value=str(old_items_snapshot), new_value=str(new_signature),
        ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(error="conflict", message="Update conflicts with an existing invoice_number."), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=invoice.to_dict()), 200


@invoices_bp.route("/<int:invoice_id>", methods=["DELETE"])
def delete_invoice(invoice_id: int):
    """DELETE /api/invoices/<id> — delete an invoice.

    The audit row is added to the session BEFORE the invoice is deleted,
    then both go out together in one commit. invoice_audit.invoice_id has no
    FK (see models.py), so this row correctly outlives the invoice it
    references once the transaction lands.
    """
    invoice = Invoice.query.get(invoice_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No invoice with id {invoice_id}."), 404

    db.session.add(InvoiceAudit(
        invoice_id=invoice.id, action="delete", field_name=None,
        old_value=f"invoice_number={invoice.invoice_number}, total={invoice.total}", new_value=None,
    ))
    db.session.delete(invoice)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"id": invoice_id, "deleted": True}), 200


@invoices_bp.route("/<int:invoice_id>/pdf", methods=["GET"])
def invoice_pdf(invoice_id: int):
    """GET /api/invoices/<id>/pdf — placeholder until Stage 4 (reportlab)."""
    return api_response(error="not_implemented", message="PDF generation lands in Stage 4."), 501


@invoices_bp.route("/<int:invoice_id>/payments", methods=["POST"])
def add_payment(invoice_id: int):
    """POST /api/invoices/<id>/payments — record a partial (or final) payment.

    invoice.amount_paid is quantized explicitly after adding the payment.
    This is not decorative: Decimal("0") + Decimal("3000") == Decimal("3000"),
    which serializes as "3000", not "3000.00" — a real precision gap between
    in-memory Decimal arithmetic and the column's actual DECIMAL(12,2)
    storage, confirmed by running it in Stage 1's review. Skipping the
    quantize here would make the invoice object returned by THIS response
    inconsistent with what a subsequent GET returns.

    One row is written to invoice_audit here (action='update',
    field_name='amount_paid'), exactly as specified — even when this payment
    pushes the invoice to fully paid and its status flips to 'Paid'. That
    status flip is NOT separately audited with its own action='status_change'
    row, unlike PUT /api/invoices/<id>, which does log status transitions
    that way. Flagging the inconsistency rather than silently picking a side:
    say the word if you want a second audit row here to match PUT's behavior.
    """
    invoice = Invoice.query.get(invoice_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No invoice with id {invoice_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - {"amount", "paid_date", "payment_method", "reference", "notes"}
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    if "amount" not in body:
        return api_response(error="missing_field", message="'amount' is required."), 400
    try:
        amount = _to_decimal(body["amount"], "amount")
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400
    if amount <= 0:
        return api_response(error="invalid_type", message="'amount' must be greater than 0."), 400
    amount = _quantize(amount)

    raw_paid_date = body.get("paid_date")
    if raw_paid_date is None:
        return api_response(error="missing_field", message="'paid_date' is required."), 400
    try:
        paid_date = _parse_date(raw_paid_date, "paid_date")
    except ValueError as exc:
        return api_response(error="invalid_type", message=str(exc)), 400

    payment_method = body.get("payment_method")
    if payment_method is not None and payment_method not in ALLOWED_PAYMENT_METHODS:
        return api_response(
            error="invalid_type",
            message=f"'payment_method' must be one of {sorted(ALLOWED_PAYMENT_METHODS)} or null.",
        ), 400

    reference = body.get("reference")
    if reference is not None and not isinstance(reference, str):
        return api_response(error="invalid_type", message="'reference' must be a string."), 400

    notes = body.get("notes")
    if notes is not None and not isinstance(notes, str):
        return api_response(error="invalid_type", message="'notes' must be a string."), 400

    current_balance = _quantize((invoice.total or Decimal("0")) - (invoice.amount_paid or Decimal("0")))
    if amount > current_balance:
        return api_response(
            error="overpayment",
            message=f"Payment of {amount} exceeds the remaining balance of {current_balance}.",
        ), 409

    payment = InvoicePayment(
        invoice_id=invoice.id, amount=amount, paid_date=paid_date,
        payment_method=payment_method, reference=reference, notes=notes,
    )
    db.session.add(payment)

    old_amount_paid = invoice.amount_paid
    invoice.amount_paid = _quantize((invoice.amount_paid or Decimal("0")) + amount)
    new_balance = _quantize((invoice.total or Decimal("0")) - invoice.amount_paid)
    if new_balance <= 0:
        invoice.status = "Paid"

    db.session.add(InvoiceAudit(
        invoice_id=invoice.id, action="update", field_name="amount_paid",
        old_value=_audit_str(old_amount_paid), new_value=_audit_str(invoice.amount_paid),
    ))

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"payment": payment.to_dict(), "invoice": invoice.to_dict()}), 201


@invoices_bp.route("/<int:invoice_id>/payments", methods=["GET"])
def list_payments(invoice_id: int):
    """GET /api/invoices/<id>/payments — list payments for this invoice, newest first."""
    invoice = Invoice.query.get(invoice_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No invoice with id {invoice_id}."), 404

    payments = (
        InvoicePayment.query.filter_by(invoice_id=invoice_id)
        .order_by(InvoicePayment.paid_date.desc())
        .all()
    )
    return api_response(data=[p.to_dict() for p in payments]), 200


@payments_bp.route("/payments/<int:payment_id>", methods=["DELETE"])
def delete_payment(payment_id: int):
    """DELETE /api/payments/<id> — remove a payment, reversing its effect on the invoice.

    Status reversal is deliberately narrow: only flips 'Paid' back to 'Sent',
    and only when this specific delete brings the balance back above zero.
    An invoice sitting at 'Draft' or 'Overdue' when a payment is deleted
    stays exactly as it was — this route never invents a status transition
    the spec didn't ask for.
    """
    payment = InvoicePayment.query.get(payment_id)
    if payment is None:
        return api_response(error="not_found", message=f"No payment with id {payment_id}."), 404

    invoice = payment.invoice

    old_amount_paid = invoice.amount_paid
    new_amount_paid = _quantize((invoice.amount_paid or Decimal("0")) - payment.amount)
    invoice.amount_paid = new_amount_paid if new_amount_paid > 0 else Decimal("0.00")

    new_balance = _quantize((invoice.total or Decimal("0")) - invoice.amount_paid)
    if invoice.status == "Paid" and new_balance > 0:
        invoice.status = "Sent"

    db.session.add(InvoiceAudit(
        invoice_id=invoice.id, action="delete", field_name="amount_paid",
        old_value=_audit_str(old_amount_paid), new_value=_audit_str(invoice.amount_paid),
    ))
    db.session.delete(payment)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"id": payment_id, "deleted": True}), 200
