"""Parses a TAX INVOICE PDF into a structured dict.

Digital/text-layer PDFs only — this pipeline never receives scanned invoices,
so there is deliberately no OCR fallback here.

Layout assumptions, baked in from studying the real sample PDF's extracted
tables (not just its rendered appearance):

* pdfplumber sees the header block as its OWN small table:
    [['LPO #', <value>], ['Invoice #', <value>], ['Date:', <value>]]
  This table is the PRIMARY source for those three fields — more reliable
  than regex over flattened text, since layout can reorder text extraction.
  Regex-over-text is only a fallback if that table isn't found.
* The item block is a SECOND table whose first row is the column header
  (S# | PRODUCT NAME | BRAND | TYPE | UNIT PRICE | QTY | AMOUNT), followed by:
    - item rows (S# populated)
    - continuation rows (S# and numeric columns empty, only PRODUCT NAME
      populated) when a long product name wraps to a second visual line
    - fully blank template rows (padding — the template ships extra empty
      bordered rows)
    - trailing "totals" rows (S# AND product name empty, but a label sits in
      the UNIT PRICE column position and a value in the AMOUNT column
      position). There are exactly three of these, in order:
      subtotal, VAT, grand total — identified by POSITION, not by matching
      exact label text, because the sample's own labels are inconsistently
      cased ("TOTAL" vs "Total") and cannot be relied on as a stable string
      match across different suppliers' invoices.
* There are two TRN numbers in the text: the first appears right under the
  "TAX INVOICE" title (before LPO#) — this is the document/header TRN. The
  second appears directly after the named party's address block, just before
  "Subject:" — this is that party's TRN. The functional spec's field names
  ("Vendor block") map onto that second block.
* Each address line in the source PDF ends with a trailing comma (it's a
  hard line-wrap, not a semantic separator), so lines are stripped of
  trailing commas before being rejoined — otherwise you get "LEETAG,,".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

# --- Regex patterns, compiled once ---------------------------------------

_TRN_LINE = re.compile(r"^\s*TRN\.?\s*(?:NO\.?)?\s*:?\s*(\d{6,20})", re.MULTILINE | re.IGNORECASE)
_LPO_PATTERN = re.compile(r"LPO\s*#\s*([A-Za-z0-9\-/]+)")
_INVOICE_PATTERN = re.compile(r"Invoice\s*#\s*([A-Za-z0-9\-/]+)")
_DATE_PATTERN = re.compile(r"Date:?\s*(\d{1,2}[-/][A-Za-z]{3}[-/]\d{4})")
_SUBJECT_PATTERN = re.compile(r"Subject:\s*(.+?)(?:\n|$)")

# Month-abbreviation date format used by this template, e.g. "23-JAN-2026".
_DATE_FMT = "%d-%b-%Y"


@dataclass
class LineItem:
    """One row of the invoice's item table."""

    s_no: str
    product_name: str
    brand: str
    type_: str
    unit_price: Decimal
    qty: Decimal
    amount: Decimal


@dataclass
class PartyBlock:
    """Name/address/TRN for a named party found in the document body."""

    name: str
    address: str
    trn: str | None


@dataclass
class ParsedInvoice:
    """Structured result of :func:`parse_invoice`."""

    source_path: Path
    header_trn: str | None
    lpo_number: str | None
    invoice_number: str | None
    date_raw: str | None
    date_parsed: date | None
    subject: str | None
    vendor: PartyBlock | None
    buyer: PartyBlock | None            # None for single-party templates like this one
    line_items: list[LineItem] = field(default_factory=list)
    subtotal: Decimal | None = None
    vat_percent: Decimal | None = None
    vat_amount: Decimal | None = None
    vat_percent_derived: bool = False   # True if we computed % from amount, not parsed literally
    grand_total: Decimal | None = None
    footer_contact: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Flatten to a plain dict, e.g. for logging or JSON serialization."""
        return {
            "source_path": str(self.source_path),
            "header_trn": self.header_trn,
            "lpo_number": self.lpo_number,
            "invoice_number": self.invoice_number,
            "date_raw": self.date_raw,
            "date_parsed": self.date_parsed.isoformat() if self.date_parsed else None,
            "subject": self.subject,
            "vendor": vars(self.vendor) if self.vendor else None,
            "buyer": vars(self.buyer) if self.buyer else None,
            "line_items": [vars(li) for li in self.line_items],
            "subtotal": str(self.subtotal) if self.subtotal is not None else None,
            "vat_percent": str(self.vat_percent) if self.vat_percent is not None else None,
            "vat_amount": str(self.vat_amount) if self.vat_amount is not None else None,
            "vat_percent_derived": self.vat_percent_derived,
            "grand_total": str(self.grand_total) if self.grand_total is not None else None,
            "footer_contact": self.footer_contact,
            "warnings": self.warnings,
        }


# --- Helpers ---------------------------------------------------------------


def _to_decimal(raw: str | None) -> Decimal | None:
    """Convert a money string like '1,200.00' or '00.00' to Decimal."""
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "")
    if cleaned == "":
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(raw: str | None) -> date | None:
    """Parse a 'DD-MMM-YYYY' date string, tolerant of '/' separators."""
    if not raw:
        return None
    normalized = raw.replace("/", "-").upper()
    try:
        return datetime.strptime(normalized, _DATE_FMT).date()
    except ValueError:
        return None


def _cell(value: str | None) -> str:
    """Normalize a table cell: None -> '', strip whitespace."""
    return value.strip() if isinstance(value, str) else ""


def _is_totals_row(row: list[str | None]) -> bool:
    """True if a table row looks like a trailing totals row (label + value, no item data).

    Note: pdfplumber is inconsistent about this on the real sample — the
    first two totals rows ('TOTAL', 'VAT%') have '' in the S#/product
    columns, but the third ('Total', the grand total) has None instead:
        ['', '', '', '', 'TOTAL', '', '1,200.00']
        ['', '', '', '', 'VAT%', '', '00.00']
        [None, None, None, None, 'Total', None, '1,200.00']
    _cell() below normalizes both None and '' to the same falsy empty
    string, so this check catches all three uniformly without needing to
    special-case which one pdfplumber decided to emit for a given row.
    """
    label = row[4] if len(row) > 4 else None
    return (not _cell(row[0])) and (not _cell(row[1])) and bool(_cell(label))


def _find_header_table(tables: list[list[list[str | None]]]) -> dict[str, str]:
    """Locate the small [label, value] header table and return it as a dict.

    Keys are normalized (lowercased, trailing '#'/':' stripped) so callers can
    look up 'lpo', 'invoice', 'date' regardless of minor punctuation differences.
    """
    for t in tables:
        if not t or len(t) < 2:
            continue
        looks_like_header = all(len(row) == 2 and _cell(row[0]) for row in t) and len(t) <= 5
        if looks_like_header:
            out = {}
            for row in t:
                key = _cell(row[0]).lower().rstrip("#:").strip()
                out[key] = _cell(row[1])
            return out
    return {}


def _parse_vendor_block(full_text: str) -> tuple[PartyBlock | None, str | None]:
    """Extract the named party's name/address/TRN and the header TRN.

    Returns:
        (vendor_block_or_None, header_trn_or_None)
    """
    trn_matches = _TRN_LINE.findall(full_text)
    header_trn = trn_matches[0] if trn_matches else None
    vendor_trn = trn_matches[1] if len(trn_matches) > 1 else None

    lines = full_text.split("\n")
    date_idx = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("date")), None)
    subject_idx = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("subject")), None)

    if date_idx is None or subject_idx is None or subject_idx <= date_idx:
        return None, header_trn

    # Strip trailing commas per line before rejoining — each line in the
    # source is a hard line-wrap that happens to end in a comma; joining
    # with ", " verbatim produces doubled commas ("LEETAG,,").
    block_lines = [
        ln.strip().rstrip(",").strip()
        for ln in lines[date_idx + 1 : subject_idx]
        if ln.strip() and not ln.strip().upper().startswith("TRN")
    ]
    if not block_lines:
        return None, header_trn

    first_line = block_lines[0]
    if "," in first_line:
        name, _, rest_of_first = first_line.partition(",")
    else:
        name, rest_of_first = first_line, ""
    address_parts = ([rest_of_first.strip()] if rest_of_first.strip() else []) + block_lines[1:]
    address = ", ".join(p for p in address_parts if p)

    return PartyBlock(name=name.strip(), address=address, trn=vendor_trn), header_trn


def _extract_line_items_and_totals(
    tables: list[list[list[str | None]]],
    warnings: list[str],
) -> tuple[list[LineItem], Decimal | None, Decimal | None, Decimal | None]:
    """Parse the item table into items + totals.

    Returns:
        (line_items, subtotal, vat_amount, grand_total). Any of the three
        totals may be None if the trailing totals rows weren't found in the
        expected shape (3 rows, in order: subtotal, VAT, grand total).
    """
    item_table = None
    for t in tables:
        if t and _cell(t[0][0]).upper() in ("S #", "S#"):
            item_table = t
            break
    if item_table is None:
        return [], None, None, None

    items: list[LineItem] = []
    totals_values: list[Decimal | None] = []
    current: LineItem | None = None

    for row in item_table[1:]:
        row = (list(row) + [None] * 7)[:7]
        s_no, product, brand, type_, unit_price, qty, amount = row

        if _is_totals_row(row):
            totals_values.append(_to_decimal(amount))
            continue

        if not any(_cell(c) for c in row):
            continue  # blank template padding row

        if _cell(s_no):
            if current:
                items.append(current)
            current = LineItem(
                s_no=_cell(s_no),
                product_name=_cell(product),
                brand=_cell(brand),
                type_=_cell(type_),
                unit_price=_to_decimal(unit_price) or Decimal("0"),
                qty=_to_decimal(qty) or Decimal("0"),
                amount=_to_decimal(amount) or Decimal("0"),
            )
        elif current and _cell(product):
            current.product_name = f"{current.product_name} {_cell(product)}".strip()

    if current:
        items.append(current)

    if len(totals_values) != 3:
        warnings.append(
            f"Expected 3 trailing totals rows (subtotal, VAT, grand total); found {len(totals_values)}. "
            "Totals below may be incomplete."
        )

    subtotal = totals_values[0] if len(totals_values) > 0 else None
    vat_amount = totals_values[1] if len(totals_values) > 1 else None
    grand_total = totals_values[2] if len(totals_values) > 2 else None

    return items, subtotal, vat_amount, grand_total


def _extract_footer_contact(full_text: str) -> str | None:
    """Grab the trailing 'Best regards ...' contact block, if present."""
    match = re.search(r"(Best regards.*)$", full_text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


# --- Public entry point -----------------------------------------------------


def parse_invoice(pdf_path: Path | str, config: dict[str, Any], logger: logging.Logger) -> ParsedInvoice:
    """Parse a TAX INVOICE PDF into structured data.

    Args:
        pdf_path: Path to the source PDF.
        config: Parsed config.yaml dict (reads `vat.fallback_percent`).
        logger: Logger for warnings (missing fields, ambiguous totals, etc.).

    Returns:
        A populated ParsedInvoice. Fields the source PDF didn't contain are
        left as None / empty rather than guessed — see `.warnings` for what
        couldn't be found.

    Raises:
        FileNotFoundError: If `pdf_path` does not exist.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Invoice PDF not found: {pdf_path}")

    warnings: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        if len(pdf.pages) > 1:
            warnings.append(
                f"PDF has {len(pdf.pages)} pages; only page 1 is parsed in this version "
                "(multi-page item concatenation not yet implemented)."
            )

        text = page.extract_text() or ""
        tables = page.extract_tables()

        header_fields = _find_header_table(tables)
        lpo_number = header_fields.get("lpo")
        invoice_number = header_fields.get("invoice")
        date_raw = header_fields.get("date")

        # Fallback to regex-over-text only for anything the header table
        # didn't give us (or if no such table was found at all).
        if not lpo_number:
            m = _LPO_PATTERN.search(text)
            lpo_number = m.group(1) if m else None
        if not invoice_number:
            m = _INVOICE_PATTERN.search(text)
            invoice_number = m.group(1) if m else None
        if not date_raw:
            m = _DATE_PATTERN.search(text)
            date_raw = m.group(1) if m else None

        subject_match = _SUBJECT_PATTERN.search(text)
        subject = subject_match.group(1).strip() if subject_match else None

        vendor, header_trn = _parse_vendor_block(text)
        line_items, subtotal, vat_amount, grand_total = _extract_line_items_and_totals(tables, warnings)

        # VAT%: respected exactly when derivable from the parsed amounts (even
        # 0%). Only falls back to config when we truly cannot tell.
        vat_percent: Decimal | None
        vat_percent_derived = False
        if subtotal is not None and vat_amount is not None and subtotal != 0:
            vat_percent = (vat_amount / subtotal * 100).quantize(Decimal("0.01"))
            vat_percent_derived = True
        elif subtotal == 0 and vat_amount == 0:
            vat_percent = Decimal("0")
            vat_percent_derived = True
        else:
            fallback = Decimal(str(config.get("vat", {}).get("fallback_percent", 5)))
            vat_percent = fallback
            warnings.append(
                f"VAT% could not be derived from the document; applied config fallback of {fallback}%."
            )

        footer_contact = _extract_footer_contact(text)

        for field_name, value in (("TRN", header_trn), ("LPO #", lpo_number), ("Invoice #", invoice_number)):
            if not value:
                warnings.append(f"{field_name} not found in source document.")

        for w in warnings:
            logger.warning(w)

        return ParsedInvoice(
            source_path=pdf_path,
            header_trn=header_trn,
            lpo_number=lpo_number,
            invoice_number=invoice_number,
            date_raw=date_raw,
            date_parsed=_parse_date(date_raw),
            subject=subject,
            vendor=vendor,
            buyer=None,
            line_items=line_items,
            subtotal=subtotal,
            vat_percent=vat_percent,
            vat_amount=vat_amount,
            vat_percent_derived=vat_percent_derived,
            grand_total=grand_total,
            footer_contact=footer_contact,
            warnings=warnings,
        )
