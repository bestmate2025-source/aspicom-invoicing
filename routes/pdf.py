"""Routes for /api/parse-pdf and /api/import-pdf.

/parse-pdf is a pure preview: runs Stage 2's parse_invoice() and returns the
structured result. No database write of any kind.

/import-pdf does the real work: parses, checks the (supplier_name,
invoice_number) unique key for a duplicate, inserts into parsed_invoices,
best-effort decrements matching inventory rows, and always logs the attempt
to import_log — success, failure, or duplicate.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import yaml
from flask import Blueprint, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import ImportLog, Inventory, ParsedInvoice
from parsers.pdf_parser import parse_invoice
from utils.logger import get_logger
from utils.responses import api_response

pdf_bp = Blueprint("pdf", __name__)


def _json_safe(obj):
    """Convert Decimal/date/path objects to strings for JSON columns.

    SQLAlchemy's JSON column uses Python's raw json.dumps(), which doesn't
    know how to serialize Decimal or date. This recursively walks the
    dict and stringifies anything non-native.
    """
    from datetime import date, datetime

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
with open(_PROJECT_ROOT / "config.yaml") as _config_file:
    _CONFIG = yaml.safe_load(_config_file)

_logger_instance = None


def _logger():
    """Lazily construct (and cache) the logger used for PDF import attempts."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = get_logger("pdf_import", _CONFIG)
    return _logger_instance


def _validate_upload():
    """Common upload validation for both routes.

    Returns:
        (file_storage, None) on success, or (None, (response, status)) to
        return directly from the caller on failure.
    """
    if "file" not in request.files:
        return None, (api_response(error="missing_file", message="No file field named 'file' in the request."), 400)

    file_storage = request.files["file"]
    if file_storage.filename == "":
        return None, (api_response(error="missing_file", message="No file selected."), 400)

    if not file_storage.filename.lower().endswith(".pdf"):
        return None, (api_response(error="invalid_file", message="Only .pdf files are accepted."), 400)

    return file_storage, None


def _decrement_inventory_from_line_items(line_items) -> None:
    """Best-effort stock decrement from parsed LineItem objects.

    Same matching rule as routes/invoices.py: case-insensitive product name,
    balance clamped at 0, a missing match silently skipped.
    """
    for item in line_items:
        match = Inventory.query.filter(
            func.lower(Inventory.product) == item.product_name.lower()
        ).first()
        if match is None:
            continue
        new_balance = match.balance - item.qty
        match.balance = new_balance if new_balance > 0 else Decimal("0")


@pdf_bp.route("/parse-pdf", methods=["POST"])
def parse_pdf():
    """POST /api/parse-pdf — parse an uploaded supplier invoice, no DB write."""
    file_storage, err = _validate_upload()
    if err:
        return err

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / file_storage.filename
        file_storage.save(temp_path)
        try:
            parsed = parse_invoice(temp_path, _CONFIG, _logger())
        except Exception as exc:  # noqa: BLE001
            return api_response(error="parser_error", message=f"Failed to parse PDF: {exc}"), 500

    return api_response(data=parsed.as_dict()), 200


@pdf_bp.route("/import-pdf", methods=["POST"])
def import_pdf():
    """POST /api/import-pdf — parse + persist to parsed_invoices.

    Always writes to import_log, whatever the outcome: 'failed' on a parser
    exception, 'duplicate' if the (supplier_name, invoice_number) pair was
    already imported, 'success' otherwise.
    """
    file_storage, err = _validate_upload()
    if err:
        return err

    filename = file_storage.filename

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / filename
        file_storage.save(temp_path)
        try:
            parsed = parse_invoice(temp_path, _CONFIG, _logger())
        except Exception as exc:  # noqa: BLE001
            db.session.add(ImportLog(source_pdf=filename, status="failed", message=str(exc)))
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
            return api_response(error="parser_error", message=f"Failed to parse PDF: {exc}"), 500

    supplier_name = parsed.vendor.name if parsed.vendor else None
    invoice_number = parsed.invoice_number

    existing = None
    if invoice_number is not None:
        existing = ParsedInvoice.query.filter_by(
            supplier_name=supplier_name, invoice_number=invoice_number
        ).first()

    if existing is not None:
        db.session.add(ImportLog(
            source_pdf=filename, status="duplicate",
            message=f"Already imported as parsed_invoices.id={existing.id} "
                    f"(supplier='{supplier_name}', invoice_number='{invoice_number}').",
        ))
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            return api_response(error="db_error", message=str(exc)), 500
        return api_response(data=existing.to_dict(), error=None, message="duplicate"), 200

    parsed_row = ParsedInvoice(
        source_pdf=filename,
        supplier_name=supplier_name,
        header_trn=parsed.header_trn,
        lpo_number=parsed.lpo_number,
        invoice_number=invoice_number,
        date=parsed.date_parsed,
        subtotal=parsed.subtotal,
        vat=parsed.vat_amount,
        grand_total=parsed.grand_total,
        raw_json=_json_safe(parsed.as_dict()),
    )
    db.session.add(parsed_row)
    db.session.flush()

    _decrement_inventory_from_line_items(parsed.line_items)

    db.session.add(ImportLog(
        source_pdf=filename, status="success",
        message=f"Imported invoice_number='{invoice_number}' from supplier='{supplier_name}'.",
    ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        db.session.add(ImportLog(
            source_pdf=filename, status="duplicate",
            message="Duplicate detected at commit time (race with another import).",
        ))
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
        return api_response(error=None, message="duplicate"), 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=parsed_row.to_dict()), 201


@pdf_bp.route("/parsed-invoices", methods=["GET"])
def list_parsed_invoices():
    """GET /api/parsed-invoices — list all imported supplier invoices."""
    invoices = ParsedInvoice.query.order_by(ParsedInvoice.imported_at.desc()).all()
    return api_response(data=[inv.to_dict() for inv in invoices]), 200


@pdf_bp.route("/parsed-invoices/<int:parsed_id>", methods=["GET"])
def get_parsed_invoice(parsed_id: int):
    """GET /api/parsed-invoices/<id> — fetch one imported invoice."""
    invoice = ParsedInvoice.query.get(parsed_id)
    if invoice is None:
        return api_response(error="not_found", message=f"No parsed invoice with id {parsed_id}."), 404
    return api_response(data=invoice.to_dict()), 200


@pdf_bp.route("/import-log", methods=["GET"])
def list_import_log():
    """GET /api/import-log — list import attempts."""
    logs = ImportLog.query.order_by(ImportLog.imported_at.desc()).limit(100).all()
    return api_response(data=[log.to_dict() for log in logs]), 200