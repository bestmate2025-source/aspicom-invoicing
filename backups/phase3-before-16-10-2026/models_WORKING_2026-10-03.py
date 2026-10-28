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

    id = db.Column(db.Integer, primary_key=True)
    product = db.Column(db.String(255), nullable=False, unique=True)
    brand = db.Column(db.String(255))
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vat_percent = db.Column(db.Numeric(5, 2), nullable=False, default=5)
    par_level = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    balance = db.Column(db.Numeric(10, 2), nullable=False, default=0, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
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

    id = db.Column(db.SmallInteger, primary_key=True, default=1)
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
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