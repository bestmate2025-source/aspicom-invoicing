"""Tests for /api/parse-pdf and /api/import-pdf.

parse_invoice() is mocked throughout — no real PDF parsing happens here, per
the instruction to keep these tests fast and self-contained rather than
shipping the NMC sample PDF into the test suite. Patched at
"routes.pdf.parse_invoice" (where it's looked up, i.e. imported into that
module's namespace), not "parsers.pdf_parser.parse_invoice" (where it's
defined) — the standard mock.patch gotcha, called out here because getting
it backwards silently no-ops the patch instead of erroring.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from models import ImportLog
from models import ParsedInvoice as ParsedInvoiceModel
from parsers.pdf_parser import LineItem, ParsedInvoice, PartyBlock


def _fake_parsed_invoice(invoice_number="14142026", supplier_name="NMC Wholesales",
                          product_name="Widget", qty="5") -> ParsedInvoice:
    qty_dec = Decimal(qty)
    unit_price = Decimal("600.00")
    return ParsedInvoice(
        source_path=Path("fake.pdf"),
        header_trn="100441082300003",
        lpo_number="PO72000000046762",
        invoice_number=invoice_number,
        date_raw="23-JAN-2026",
        date_parsed=date(2026, 1, 23),
        subject="INVOICE OF BELOW MENTION PRODUCT.",
        vendor=PartyBlock(name=supplier_name, address="Al Ain, UAE", trn="100551136300003"),
        buyer=None,
        line_items=[LineItem(
            s_no="1", product_name=product_name, brand="ASPICOMLLC", type_="VIALS",
            unit_price=unit_price, qty=qty_dec, amount=unit_price * qty_dec,
        )],
        subtotal=Decimal("1200.00"),
        vat_percent=Decimal("0.00"),
        vat_amount=Decimal("0.00"),
        vat_percent_derived=True,
        grand_total=Decimal("1200.00"),
        footer_contact="Best regards,\nAccounts\nAspicom LLC",
        warnings=[],
    )


def _upload(client, route, filename="invoice.pdf", content=b"%PDF-1.4 fake"):
    return client.post(route, data={"file": (BytesIO(content), filename)}, content_type="multipart/form-data")


def _create_inventory(client, **overrides):
    payload = {"product": "Widget", "brand": "X", "price": 10, "par_level": 5, "balance": 50}
    payload.update(overrides)
    return client.post("/api/inventory", json=payload).get_json()["data"]


@patch("routes.pdf.parse_invoice")
def test_parse_pdf_endpoint_returns_structured_data(mock_parse, client):
    mock_parse.return_value = _fake_parsed_invoice()
    resp = _upload(client, "/api/parse-pdf")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert body["data"]["invoice_number"] == "14142026"
    assert body["data"]["vendor"]["name"] == "NMC Wholesales"

    # confirm this really was preview-only — no row landed in the DB
    assert ParsedInvoiceModel.query.count() == 0


@patch("routes.pdf.parse_invoice")
def test_import_pdf_inserts_parsed_invoice(mock_parse, client):
    mock_parse.return_value = _fake_parsed_invoice()
    resp = _upload(client, "/api/import-pdf")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["error"] is None
    assert body["data"]["invoice_number"] == "14142026"
    assert body["data"]["supplier_name"] == "NMC Wholesales"
    assert ParsedInvoiceModel.query.count() == 1


@patch("routes.pdf.parse_invoice")
def test_import_pdf_duplicate_returns_duplicate_status(mock_parse, client):
    mock_parse.return_value = _fake_parsed_invoice()
    first = _upload(client, "/api/import-pdf", filename="invoice1.pdf")
    assert first.status_code == 201

    mock_parse.return_value = _fake_parsed_invoice()  # same supplier + invoice_number again
    second = _upload(client, "/api/import-pdf", filename="invoice2.pdf")
    assert second.status_code == 200
    body = second.get_json()
    assert body["error"] is None
    assert body["message"] == "duplicate"

    assert ParsedInvoiceModel.query.count() == 1  # not inserted twice


@patch("routes.pdf.parse_invoice")
def test_import_pdf_logs_to_import_log(mock_parse, client):
    mock_parse.return_value = _fake_parsed_invoice()
    _upload(client, "/api/import-pdf", filename="invoice.pdf")

    logs = ImportLog.query.filter_by(source_pdf="invoice.pdf").all()
    assert len(logs) == 1
    assert logs[0].status == "success"


@patch("routes.pdf.parse_invoice")
def test_import_pdf_updates_inventory_balances(mock_parse, client):
    inv_item = _create_inventory(client, product="Widget", balance=50)
    mock_parse.return_value = _fake_parsed_invoice(product_name="widget", qty="5")  # case mismatch, on purpose

    resp = _upload(client, "/api/import-pdf")
    assert resp.status_code == 201

    updated = client.get("/api/inventory").get_json()["data"]
    match = next(i for i in updated if i["id"] == inv_item["id"])
    assert match["balance"] == "45.00"  # 50 - 5


def test_parse_pdf_missing_file(client):
    resp = client.post("/api/parse-pdf", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_file"


def test_parse_pdf_non_pdf(client):
    resp = _upload(client, "/api/parse-pdf", filename="invoice.txt", content=b"not a pdf")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_file"


def test_import_pdf_missing_file(client):
    resp = client.post("/api/import-pdf", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_file"


@patch("routes.pdf.parse_invoice")
def test_import_pdf_parser_error_returns_500_and_logs_failed(mock_parse, client):
    mock_parse.side_effect = ValueError("could not read table structure")
    resp = _upload(client, "/api/import-pdf", filename="broken.pdf")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "parser_error"

    logs = ImportLog.query.filter_by(source_pdf="broken.pdf").all()
    assert len(logs) == 1
    assert logs[0].status == "failed"
