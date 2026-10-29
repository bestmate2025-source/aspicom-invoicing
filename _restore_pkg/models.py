"""SQLAlchemy models — mirror db/schema.sql exactly.

Every table, column, type, and constraint here should match schema.sql 1:1.
If they diverge, schema.sql is the source of truth (it's what actually runs
against MySQL) — fix the model, not the schema, unless you're deliberately
changing both together.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from extensions import db


def _dec(value: Decimal | None) -> str | None:
    """Serialize a Decimal to string for JSON (floats would lose precision)."""
    return str(value) if value is not None else None


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.Text)
    po_box = db.Column(db.String(50))
    trn = db.Column(db.String(50))
    email = db.Column(db.String(255), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    invoices = db.relationship("Invoice", back_populates="client")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "po_box": self.po_box,
            "trn": self.trn,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    document_type = db.Column(
        db.Enum("invoice", "quotation", name="document_type"),
        nullable=False, default="invoice", index=True,
    )
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, default=1)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    lpo_number = db.Column(db.String(100))
    invoice_number = db.Column(db.String(100), nullable=False, unique=True)
    issued = db.Column(db.Date)
    due = db.Column(db.Date)
    valid_until = db.Column(db.Date)
    status = db.Column(
        db.Enum("Draft", "Sent", "Paid", "Overdue", "Accepted", "Rejected", name="invoice_status"),
        nullable=False, default="Draft",
    )
    vat_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vat = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount_paid = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    client = db.relationship("Client", back_populates="invoices")
    company = db.relationship("Company")
    items = db.relationship("InvoiceItem", back_populates="invoice",
                             cascade="all, delete-orphan")
    payments = db.relationship("InvoicePayment", back_populates="invoice",
                                cascade="all, delete-orphan",
                                order_by="InvoicePayment.paid_date.desc()")

    def to_dict(self, include_items: bool = True) -> dict:
        balance = (self.total or 0) - (self.amount_paid or 0)
        data = {
            "id": self.id,
            "document_type": self.document_type,
            "client_id": self.client_id,
            "company_id": self.company_id,
            "currency": self.currency or "USD",
            "lpo_number": self.lpo_number,
            "invoice_number": self.invoice_number,
            "issued": self.issued.isoformat() if self.issued else None,
            "due": self.due.isoformat() if self.due else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "status": self.status,
            "vat_percent": _dec(self.vat_percent),
            "notes": self.notes,
            "subtotal": _dec(self.subtotal),
            "vat": _dec(self.vat),
            "total": _dec(self.total),
            "amount_paid": _dec(self.amount_paid) if self.amount_paid is not None else "0.00",
            "balance": _dec(balance),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_items:
            data["items"] = [i.to_dict() for i in self.items]
        return data


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(255))
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    qty = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    foc_qty = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vat_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    vat_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    invoice = db.relationship("Invoice", back_populates="items")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "product_name": self.product_name,
            "brand": self.brand,
            "unit_price": _dec(self.unit_price),
            "qty": _dec(self.qty),
            "foc_qty": _dec(self.foc_qty),
            "discount_percent": _dec(self.discount_percent),
            "discount_amount": _dec(self.discount_amount),
            "amount": _dec(self.amount),
            "vat_percent": _dec(self.vat_percent),
            "vat_amount": _dec(self.vat_amount),
        }


class InvoicePayment(db.Model):
    __tablename__ = "invoice_payments"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_date = db.Column(db.Date, nullable=False)
    payment_method = db.Column(
        db.Enum("cash", "bank_transfer", "card", "cheque", "other", name="payment_method"),
        nullable=True,
    )
    reference = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    invoice = db.relationship("Invoice", back_populates="payments")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "amount": _dec(self.amount),
            "paid_date": self.paid_date.isoformat() if self.paid_date else None,
            "payment_method": self.payment_method,
            "reference": self.reference,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Inventory(db.Model):
    __tablename__ = "inventory"
    __table_args__ = (db.UniqueConstraint("company_id", "product", name="uq_inventory_company_product"),)

    id = db.Column(db.Integer, primary_key=True)
    # Wave 3A: every company has its own stock. A product name is unique PER COMPANY.
    company_id = db.Column(db.Integer, db.ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True)
    product = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(255))
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vat_percent = db.Column(db.Numeric(5, 2), nullable=False, default=5)
    par_level = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    balance = db.Column(db.Numeric(10, 2), nullable=False, default=0, index=True)
    # Wave 3B: what one unit costs us (average cost). Only people who may see costs get it in the API.
    avg_cost = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default=db.text("0"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "avg_cost": _dec(self.avg_cost),
            "product": self.product,
            "brand": self.brand,
            "price": _dec(self.price),
            "vat_percent": _dec(self.vat_percent),
            "par_level": _dec(self.par_level),
            "balance": _dec(self.balance),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Company(db.Model):
    __tablename__ = "company"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    address = db.Column(db.Text)
    po_box = db.Column(db.String(50))
    trn = db.Column(db.String(50))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    website = db.Column(db.String(255))
    signee = db.Column(db.String(255))
    logo_path = db.Column(db.String(500))
    stamp_path = db.Column(db.String(500))
    # 1 company = 1 currency. Every invoice this company issues uses it (ERP Phase 4.5).
    currency = db.Column(db.String(3), nullable=False, default="USD")
    # Wave 2B: when True, this company's invoices post to its books automatically.
    # Switched on only by scripts/accounting_backfill.py (after the old invoices are posted).
    accounting_enabled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text("0"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "currency": self.currency or "USD",
            "address": self.address,
            "po_box": self.po_box,
            "trn": self.trn,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "signee": self.signee,
            "logo_path": self.logo_path,
            "stamp_path": self.stamp_path,
        }


class ParsedInvoice(db.Model):
    __tablename__ = "parsed_invoices"
    __table_args__ = (
        db.UniqueConstraint("supplier_name", "invoice_number", name="uq_supplier_invoice"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Soft link to suppliers.id (no foreign key on purpose: the bill stays if a supplier is deleted).
    supplier_id = db.Column(db.Integer, nullable=True, index=True)
    source_pdf = db.Column(db.String(500), nullable=False)
    supplier_name = db.Column(db.String(255), index=True)
    header_trn = db.Column(db.String(50))
    lpo_number = db.Column(db.String(100))
    invoice_number = db.Column(db.String(100), index=True)
    date = db.Column(db.Date)
    subtotal = db.Column(db.Numeric(12, 2))
    vat = db.Column(db.Numeric(12, 2))
    grand_total = db.Column(db.Numeric(12, 2))
    raw_json = db.Column(db.JSON)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "supplier_id": self.supplier_id,
            "source_pdf": self.source_pdf,
            "supplier_name": self.supplier_name,
            "header_trn": self.header_trn,
            "lpo_number": self.lpo_number,
            "invoice_number": self.invoice_number,
            "date": self.date.isoformat() if self.date else None,
            "subtotal": _dec(self.subtotal),
            "vat": _dec(self.vat),
            "grand_total": _dec(self.grand_total),
            "raw_json": self.raw_json,
            "imported_at": self.imported_at.isoformat() if self.imported_at else None,
        }


class ImportLog(db.Model):
    __tablename__ = "import_log"

    id = db.Column(db.Integer, primary_key=True)
    source_pdf = db.Column(db.String(500), nullable=False)
    status = db.Column(db.Enum("success", "failed", "duplicate", "ocr_fallback", name="import_status"),
                        nullable=False, index=True)
    message = db.Column(db.Text)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_pdf": self.source_pdf,
            "status": self.status,
            "message": self.message,
            "imported_at": self.imported_at.isoformat() if self.imported_at else None,
        }


class InvoiceAudit(db.Model):
    __tablename__ = "invoice_audit"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.Enum("create", "update", "delete", "status_change", name="audit_action"),
                        nullable=False)
    field_name = db.Column(db.String(100))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "action": self.action,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
        }


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100))
    email = db.Column(db.String(255))
    role = db.Column(
        db.Enum("admin", "manager", "sales", "store", "accountant", "user", name="user_role"),
        nullable=False, default="user",
    )
    phone = db.Column(db.String(50))
    job_title = db.Column(db.String(100))
    # Companies this person may use, as "1,3". Empty = all companies.
    company_ids = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_root = db.Column(db.Boolean, nullable=False, default=False)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        # password_hash is deliberately never included.
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "role": self.role,
            "phone": self.phone,
            "job_title": self.job_title,
            "company_ids": [int(x) for x in (self.company_ids or "").split(",") if x.strip().isdigit()],
            "is_root": bool(self.is_root),
            "is_active": bool(self.is_active),
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class StockMovement(db.Model):
    """One row per stock change. Never edited, never deleted — it is the stock history.

    No foreign keys on purpose: the history must survive product / user deletions.
    change_qty is the REAL change applied to the balance (negative = stock out).
    """

    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    inventory_id = db.Column(db.Integer, nullable=False, index=True)
    product_name = db.Column(db.String(255), nullable=False)
    change_qty = db.Column(db.Numeric(10, 2), nullable=False)
    balance_after = db.Column(db.Numeric(10, 2), nullable=False)
    reason = db.Column(db.Enum(
        "initial", "invoice_created", "invoice_deleted",
        "invoice_edited", "quotation_converted",
        "stock_in", "stock_out", "damage", "expiry",
        "return", "rtv", "correction",
        name="stock_movement_reason"), nullable=False, index=True)
    reference_id = db.Column(db.Integer, nullable=True, index=True)
    reference_label = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "inventory_id": self.inventory_id,
            "product_name": self.product_name,
            "change_qty": _dec(self.change_qty),
            "balance_after": _dec(self.balance_after),
            "reason": self.reason,
            "reference_id": self.reference_id,
            "reference_label": self.reference_label,
            "notes": self.notes,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Supplier(db.Model):
    """A vendor we buy from. Parsed PDF bills link to it softly via parsed_invoices.supplier_id."""

    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    trn = db.Column(db.String(50))
    address = db.Column(db.Text)
    po_box = db.Column(db.String(50))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    contact_person = db.Column(db.String(150))
    payment_terms = db.Column(db.String(100))
    lead_time_days = db.Column(db.Integer, nullable=True)
    bank_name = db.Column(db.String(150))
    bank_account = db.Column(db.String(100))
    category = db.Column(db.String(100))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self, include_bank: bool = True) -> dict:
        """include_bank=False leaves bank_name / bank_account OUT of the result entirely
        (used for the read-only 'user' role, so they cannot see them even via the API)."""
        data = {
            "id": self.id,
            "name": self.name,
            "trn": self.trn,
            "address": self.address,
            "po_box": self.po_box,
            "email": self.email,
            "phone": self.phone,
            "contact_person": self.contact_person,
            "payment_terms": self.payment_terms,
            "lead_time_days": self.lead_time_days,
            "category": self.category,
            "notes": self.notes,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_bank:
            data["bank_name"] = self.bank_name
            data["bank_account"] = self.bank_account
        return data


# ---------------------------------------------------------------------------
# Phase 4: Purchase Orders and Goods Received Notes
# ---------------------------------------------------------------------------

PO_STATUSES = ("Draft", "Sent", "Partial", "Received", "Cancelled")


class PurchaseOrder(db.Model):
    """An order we place with a supplier. Stock only changes when goods are received (see GoodsReceivedNote)."""

    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True)
    # Wave 3A: the company that is buying (its stock grows when goods are received)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True)
    po_number = db.Column(db.String(50), nullable=False, unique=True)
    status = db.Column(
        db.Enum(*PO_STATUSES, name="po_status"),
        nullable=False, default="Draft", index=True,
    )
    order_date = db.Column(db.Date, nullable=False)
    expected_date = db.Column(db.Date, nullable=True)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vat = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    supplier = db.relationship("Supplier")
    items = db.relationship(
        "PurchaseOrderItem", back_populates="po",
        cascade="all, delete-orphan", order_by="PurchaseOrderItem.id",
    )
    grns = db.relationship(
        "GoodsReceivedNote", back_populates="po",
        cascade="all, delete-orphan", order_by="GoodsReceivedNote.id",
    )

    def to_dict(self, include_items: bool = False, include_grns: bool = False) -> dict:
        data = {
            "id": self.id,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier.name if self.supplier else None,
            "company_id": self.company_id,
            "po_number": self.po_number,
            "status": self.status,
            "order_date": self.order_date.isoformat() if self.order_date else None,
            "expected_date": self.expected_date.isoformat() if self.expected_date else None,
            "subtotal": _dec(self.subtotal),
            "vat": _dec(self.vat),
            "total": _dec(self.total),
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_items:
            data["items"] = [it.to_dict() for it in self.items]
        if include_grns:
            data["grns"] = [g.to_dict(include_items=True) for g in self.grns]
        return data


class PurchaseOrderItem(db.Model):
    __tablename__ = "purchase_order_items"

    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_name = db.Column(db.String(255), nullable=False)
    brand = db.Column(db.String(255))
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    qty_ordered = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    qty_received = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    vat_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # unit_price x qty_ordered, VAT not included

    po = db.relationship("PurchaseOrder", back_populates="items")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "po_id": self.po_id,
            "product_name": self.product_name,
            "brand": self.brand,
            "unit_price": _dec(self.unit_price),
            "qty_ordered": _dec(self.qty_ordered),
            "qty_received": _dec(self.qty_received),
            "vat_percent": _dec(self.vat_percent),
            "amount": _dec(self.amount),
        }


class GoodsReceivedNote(db.Model):
    """One delivery received against a PO. A PO can have several (partial deliveries)."""

    __tablename__ = "goods_received_notes"

    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    grn_number = db.Column(db.String(50), nullable=False, unique=True)
    received_date = db.Column(db.Date, nullable=False)
    received_by = db.Column(db.Integer, nullable=True)  # users.id, no FK so history survives user deletion
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    po = db.relationship("PurchaseOrder", back_populates="grns")
    items = db.relationship(
        "GoodsReceivedNoteItem", back_populates="grn",
        cascade="all, delete-orphan", order_by="GoodsReceivedNoteItem.id",
    )

    def to_dict(self, include_items: bool = False) -> dict:
        data = {
            "id": self.id,
            "po_id": self.po_id,
            "grn_number": self.grn_number,
            "received_date": self.received_date.isoformat() if self.received_date else None,
            "received_by": self.received_by,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_items:
            data["items"] = [it.to_dict() for it in self.items]
        return data


class GoodsReceivedNoteItem(db.Model):
    """What exactly arrived on one GRN (so partial deliveries stay traceable)."""

    __tablename__ = "goods_received_note_items"

    id = db.Column(db.Integer, primary_key=True)
    grn_id = db.Column(db.Integer, db.ForeignKey("goods_received_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    po_item_id = db.Column(db.Integer, nullable=False, index=True)
    product_name = db.Column(db.String(255), nullable=False)
    qty_received = db.Column(db.Numeric(10, 2), nullable=False, default=0)

    grn = db.relationship("GoodsReceivedNote", back_populates="items")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "grn_id": self.grn_id,
            "po_item_id": self.po_item_id,
            "product_name": self.product_name,
            "qty_received": _dec(self.qty_received),
        }



# ---------------------------------------------------------------------
# Financial Accounting - Stage 1: chart of accounts + manual journals
# ---------------------------------------------------------------------
class Account(db.Model):
    """One account in the chart of accounts (e.g. 1000 Cash, 4000 Sales)."""

    __tablename__ = "accounts"

    __table_args__ = (db.UniqueConstraint("company_id", "code", name="uq_accounts_company_code"),)

    id = db.Column(db.Integer, primary_key=True)
    # each company keeps its own books (and currency): accounts belong to one company
    company_id = db.Column(db.Integer, db.ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(
        db.Enum("Asset", "Liability", "Equity", "Income", "Expense", name="account_type"),
        nullable=False,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "is_active": bool(self.is_active),
            "parent_id": self.parent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class JournalEntry(db.Model):
    """A journal: a dated set of debit / credit lines that must balance.

    Draft can be edited or deleted. Posted is locked. Reversed = a posted journal
    that has been cancelled by a reversing journal (reversal_of_id points back).
    """

    __tablename__ = "journal_entries"

    # Only ONE live journal per invoice / payment per company's books. source_active is 1 on the
    # live journal and NULL on reversed / reversal journals (MySQL allows many NULLs in a unique
    # index), so an edited invoice can be reversed and posted again without breaking the rule.
    __table_args__ = (
        db.UniqueConstraint("source_type", "source_id", "company_id", "source_active", name="uq_journal_live_source"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_type = db.Column(
        db.Enum(
            "manual", "invoice", "payment", "invoice_reversal", "payment_reversal",
            "opening_stock", "opening_stock_reversal",
            "goods_received", "goods_received_reversal",
            "cogs", "cogs_reversal",
            "stock_adjustment", "stock_adjustment_reversal",
            name="journal_source_type",
        ),
        nullable=False, default="manual", index=True,
    )
    source_id = db.Column(db.Integer, nullable=True)       # id of the invoice / payment it came from
    source_ref = db.Column(db.String(100), nullable=True)  # invoice number or payment reference
    source_active = db.Column(db.SmallInteger, nullable=True)  # 1 = live journal of that source, NULL = history
    cost_signature = db.Column(db.String(64), nullable=True)   # Wave 3D: which products/quantities a cost-of-sales entry was built from
    entry_number = db.Column(db.String(50), nullable=False, unique=True)
    entry_date = db.Column(db.Date, nullable=False, index=True)
    reference = db.Column(db.String(100))
    description = db.Column(db.Text)
    status = db.Column(
        db.Enum("Draft", "Posted", "Reversed", name="journal_status"),
        nullable=False, default="Draft", index=True,
    )
    reversal_of_id = db.Column(db.Integer, nullable=True)  # set on a reversing journal
    created_by = db.Column(db.Integer)  # users.id (no foreign key: history must survive user deletion)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    lines = db.relationship(
        "JournalLine", backref="entry", cascade="all, delete-orphan", order_by="JournalLine.id",
    )

    def to_dict(self, include_lines: bool = False) -> dict:
        debit = sum((Decimal(str(ln.debit or 0)) for ln in self.lines), Decimal("0"))
        credit = sum((Decimal(str(ln.credit or 0)) for ln in self.lines), Decimal("0"))
        data = {
            "id": self.id,
            "company_id": self.company_id,
            "source_type": self.source_type or "manual",
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "entry_number": self.entry_number,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "reference": self.reference,
            "description": self.description,
            "status": self.status,
            "reversal_of_id": self.reversal_of_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "total_debit": f"{debit:.2f}",
            "total_credit": f"{credit:.2f}",
        }
        if include_lines:
            data["lines"] = [ln.to_dict() for ln in self.lines]
        return data


class JournalLine(db.Model):
    __tablename__ = "journal_lines"

    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(
        db.Integer, db.ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    debit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    credit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    description = db.Column(db.String(255))

    account = db.relationship("Account")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "journal_entry_id": self.journal_entry_id,
            "account_id": self.account_id,
            "account_code": self.account.code if self.account else None,
            "account_name": self.account.name if self.account else None,
            "debit": f"{Decimal(str(self.debit or 0)):.2f}",
            "credit": f"{Decimal(str(self.credit or 0)):.2f}",
            "description": self.description,
        }
