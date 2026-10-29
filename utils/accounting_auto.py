"""Automatic accounting for invoices (Financial Accounting - Wave 2B).

What it does
------------
When a company has accounting switched ON (company.accounting_enabled), every
invoice that is Sent / Paid / Overdue has ONE live journal in that company's books:

    Dr 1200 Accounts Receivable   = invoice total
        Cr 2100 VAT Payable       = invoice VAT
        Cr 4000 Sales Revenue     = invoice total - VAT   (so it always balances)

Quotations and Draft invoices never post.

The rule of the house (same as stock_movements.log_movement):
  * nothing here COMMITS. The invoice route commits once, so the invoice, its stock
    movements and its journal are saved together - or none of them are.
  * if anything is wrong this raises AccountingError and the route rolls everything back.

Changes are never rewritten. If an invoice changes, the old journal is REVERSED (marked
Reversed + a reversing journal) and a fresh one is posted. Both stay in the books.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
import hashlib

from extensions import db
from sqlalchemy import func

from models import Account, Company, Inventory, InvoicePayment, JournalEntry, JournalLine
from utils.stock_movements import find_inventory_by_name, get_current_user_id

TWO_DP = Decimal("0.01")
ZERO = Decimal("0.00")

POSTABLE_STATUSES = ("Sent", "Paid", "Overdue")

# Account codes used for invoices (they come from the starter chart of accounts).
CODE_RECEIVABLE = "1200"
CODE_VAT_PAYABLE = "2100"
CODE_SALES = "4000"
CODE_INVENTORY = "1300"
CODE_RETAINED = "3000"
OPENING_TYPE = "opening_stock"
GRN_TYPE = "goods_received"
COGS_TYPE = "cogs"
ADJUST_TYPE = "stock_adjustment"
CODE_LOSS = "5100"
CODE_ADJUST = "5200"
# Which expense account a manual stock change goes to (anything not listed -> Stock Adjustment Expense).
ADJUSTMENT_ACCOUNT = {"damage": CODE_LOSS, "expiry": CODE_LOSS}
CODE_COGS = "5000"
CODE_VAT_RECEIVABLE = "1400"
CODE_PAYABLE = "2000"
FOUR_DP = Decimal("0.0001")
CODE_CASH = "1000"
CODE_BANK = "1100"
# Where a payment's money lands. Anything not listed here (bank transfer, card, cheque, other,
# or no method) goes to the Bank Account. Change this table if you want a different rule.
PAYMENT_METHOD_ACCOUNT = {"cash": CODE_CASH}
ALL_NAMES = {
    CODE_INVENTORY: "Inventory",
    CODE_COGS: "Cost of Goods Sold",
    CODE_LOSS: "Inventory Loss",
    CODE_ADJUST: "Stock Adjustment Expense",
    CODE_VAT_RECEIVABLE: "VAT Receivable",
    CODE_PAYABLE: "Accounts Payable",
    CODE_RETAINED: "Retained Earnings",
    CODE_CASH: "Cash in Hand",
    CODE_BANK: "Bank Account",
    CODE_RECEIVABLE: "Accounts Receivable",
    CODE_VAT_PAYABLE: "VAT Payable",
    CODE_SALES: "Sales Revenue",
}
NEEDED_CODES = {
    CODE_RECEIVABLE: "Accounts Receivable",
    CODE_VAT_PAYABLE: "VAT Payable",
    CODE_SALES: "Sales Revenue",
}


class AccountingError(Exception):
    """Something stops the invoice from being posted. The message is shown to the user."""


# ---------------------------------------------------------------------
# pure helpers (no database) - these are unit tested
# ---------------------------------------------------------------------
def payment_account_code(method) -> str:
    return PAYMENT_METHOD_ACCOUNT.get(method or "", CODE_BANK)


def build_payment_lines(amount, method) -> list[tuple[str, Decimal, Decimal]]:
    """Dr Cash / Bank, Cr Accounts Receivable."""
    amount = money(amount)
    if amount <= 0:
        raise AccountingError("A payment must be more than zero to be posted to the books.")
    return [(payment_account_code(method), amount, ZERO), (CODE_RECEIVABLE, ZERO, amount)]


def money(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(TWO_DP, rounding=ROUND_HALF_UP)


def invoice_should_post(document_type, status, enabled) -> bool:
    """Only real invoices in Sent / Paid / Overdue, in a company with accounting ON."""
    return bool(enabled) and document_type == "invoice" and status in POSTABLE_STATUSES


def build_invoice_lines(total, vat) -> list[tuple[str, Decimal, Decimal]]:
    """(account code, debit, credit) lines for an invoice. Empty list = nothing to post."""
    total = money(total)
    vat = money(vat)
    if total < 0 or vat < 0:
        raise AccountingError("The invoice total or VAT is negative, so it cannot be posted to the books.")
    if vat > total:
        raise AccountingError("The invoice VAT is bigger than its total, so it cannot be posted to the books.")
    if total == 0:
        return []
    lines = [(CODE_RECEIVABLE, total, ZERO)]
    if vat > 0:
        lines.append((CODE_VAT_PAYABLE, ZERO, vat))
    sales = total - vat
    if sales > 0:
        lines.append((CODE_SALES, ZERO, sales))
    return lines


def normalise_lines(lines) -> list[tuple]:
    """Comparable form of journal lines: sorted (account_id, debit, credit)."""
    return sorted((int(a), money(d), money(c)) for a, d, c in lines)


def plan_sync(live, desired) -> list[tuple]:
    """Decide what to do. Pure.

    live    = journals that are live today, each {"id", "company_id", "entry_date", "lines": [(account_id, debit, credit)]}
    desired = None (the invoice should NOT be in the books) or
              {"company_id", "entry_date", "lines": [(account_id, debit, credit)]}
    Returns a list of ("reverse", journal_id) and / or ("post",).
    """
    actions: list[tuple] = []
    kept = False
    for journal in live:
        same = (
            desired is not None
            and not kept
            and journal["company_id"] == desired["company_id"]
            and journal["entry_date"] == desired["entry_date"]
            and normalise_lines(journal["lines"]) == normalise_lines(desired["lines"])
        )
        if same:
            kept = True
        else:
            actions.append(("reverse", journal["id"]))
    if desired is not None and not kept:
        actions.append(("post",))
    return actions


def _next_entry_number(year: int) -> str:
    prefix = f"JE-{year}-"
    highest = 0
    for (number,) in db.session.query(JournalEntry.entry_number).filter(
        JournalEntry.entry_number.like(prefix + "%")
    ):
        try:
            highest = max(highest, int(number.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return f"{prefix}{highest + 1:04d}"


# ---------------------------------------------------------------------
# database part (never commits)
# ---------------------------------------------------------------------
def enabled_company_ids() -> set[int]:
    return {c.id for c in Company.query.all() if c.accounting_enabled}


def accounting_enabled(company_id) -> bool:
    company = Company.query.get(company_id) if company_id is not None else None
    return bool(company and company.accounting_enabled)


def currency_problem(company_id, currency) -> str | None:
    """Message if accounting is ON for this company and the invoice currency is not the company's."""
    company = Company.query.get(company_id) if company_id is not None else None
    if company is None or not company.accounting_enabled:
        return None
    company_currency = company.currency or "USD"
    if currency != company_currency:
        return (
            f"{company.name or 'This company'} keeps its books in {company_currency}, so its invoices "
            f"must be in {company_currency} (this one is {currency})."
        )
    return None


def _accounts_for(company: Company, codes=None) -> dict[str, Account]:
    codes = list(codes) if codes else list(NEEDED_CODES)
    found = {
        a.code: a
        for a in Account.query.filter_by(company_id=company.id, is_active=True).all()
        if a.code in codes
    }
    missing = [f"{code} {ALL_NAMES.get(code, '')}".strip() for code in codes if code not in found]
    if missing:
        raise AccountingError(
            f"Accounting is ON for {company.name or 'this company'}, but these accounts are missing or "
            f"inactive: {', '.join(missing)}. Load the starter accounts (Accounting > Chart of Accounts) "
            f"or ask the administrator to switch accounting off for this company."
        )
    return found


def _live_journals(invoice_id) -> list[JournalEntry]:
    return JournalEntry.query.filter_by(source_type="invoice", source_id=invoice_id, source_active=1).all()


def _new_entry(company, source_type, source_id, source_ref, entry_date, reference, description, accounts, spec):
    entry = JournalEntry(
        company_id=company.id, source_type=source_type, source_id=source_id,
        source_ref=source_ref, source_active=1,
        entry_number=_next_entry_number(entry_date.year), entry_date=entry_date,
        reference=(reference or "")[:100], description=description,
        status="Posted", created_by=get_current_user_id(),
    )
    entry.lines = [
        JournalLine(account_id=accounts[code].id, debit=debit, credit=credit, description=accounts[code].name)
        for code, debit, credit in spec
    ]
    db.session.add(entry)
    db.session.flush()
    return entry


def _reverse(entry: JournalEntry, reason: str) -> JournalEntry:
    today = date.today()
    reversal = JournalEntry(
        company_id=entry.company_id,
        source_type=f"{entry.source_type}_reversal",
        source_id=entry.source_id, source_ref=entry.source_ref, source_active=None,
        entry_number=_next_entry_number(today.year), entry_date=today,
        reference=f"REV {entry.entry_number}"[:100],
        description=f"Reversal of {entry.entry_number}: {reason}",
        status="Posted", reversal_of_id=entry.id, created_by=get_current_user_id(),
    )
    # debit and credit swapped, amounts stay positive
    reversal.lines = [
        JournalLine(account_id=ln.account_id, debit=ln.credit, credit=ln.debit, description=ln.description)
        for ln in entry.lines
    ]
    entry.status = "Reversed"
    entry.source_active = None
    db.session.add(reversal)
    db.session.flush()
    return reversal


def _view(journals):
    return [
        {"id": j.id, "company_id": j.company_id, "entry_date": j.entry_date,
         "lines": [(ln.account_id, ln.debit, ln.credit) for ln in j.lines]}
        for j in journals
    ]


def _sync_payments(invoice, company, enabled_ids, in_books: bool, reason: str) -> dict:
    """Payments of an invoice that is in the books are in the books too (Dr Cash/Bank, Cr Receivable)."""
    live_all = [
        j for j in JournalEntry.query.filter_by(
            source_type="payment", source_ref=invoice.invoice_number, source_active=1
        ).all()
        if j.company_id in enabled_ids
    ]
    payments = []
    if in_books:
        payments = [p for p in InvoicePayment.query.filter_by(invoice_id=invoice.id).all() if money(p.amount) > 0]
    if in_books:
        reason = "payment changed or removed"
    by_id = {j.id: j for j in live_all}
    result = {"payments_posted": 0, "payments_reversed": 0}

    accounts = None
    if payments:
        codes = {CODE_RECEIVABLE} | {payment_account_code(p.payment_method) for p in payments}
        accounts = _accounts_for(company, codes)

    for p in payments:
        spec = build_payment_lines(p.amount, p.payment_method)
        entry_date = p.paid_date or date.today()
        desired = {
            "company_id": company.id, "entry_date": entry_date,
            "lines": [(accounts[code].id, d, c) for code, d, c in spec],
        }
        mine = [j for j in live_all if j.source_id == p.id]
        for action in plan_sync(_view(mine), desired):
            if action[0] == "reverse":
                _reverse(by_id[action[1]], reason)
                result["payments_reversed"] += 1
            else:
                label = (p.payment_method or "payment").replace("_", " ")
                ref = p.reference or invoice.invoice_number
                _new_entry(
                    company, "payment", p.id, invoice.invoice_number, entry_date, ref,
                    f"Payment ({label}) for invoice {invoice.invoice_number}"
                    + (f" - {p.reference}" if p.reference else ""),
                    accounts, spec,
                )
                result["payments_posted"] += 1

    # live payment journals whose payment is gone (deleted) or no longer belongs here
    current_ids = {p.id for p in payments}
    for j in live_all:
        if j.source_id not in current_ids:
            _reverse(j, reason)
            result["payments_reversed"] += 1
    return result


def cogs_signature(rows) -> str:
    """Pure. rows = (inventory_id, qty, has_cost). Same products + quantities -> same signature."""
    parts = sorted((int(i), f"{Decimal(str(q)):.2f}", bool(h)) for i, q, h in rows)
    return hashlib.sha1(repr(parts).encode()).hexdigest()


def cogs_total(rows) -> Decimal:
    """Pure. rows = (qty, avg_cost) -> cost of sales, each line rounded to 2 dp."""
    total = ZERO
    for qty, cost in rows:
        total += (Decimal(str(qty)) * Decimal(str(cost or 0))).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    return total


def _cogs_rows(invoice, company):
    """Paid quantity per product of this company's stock (same rule as stock: free-of-charge qty is not counted).
    Returns {inventory_id: [qty, avg_cost]}; products not in stock are left out."""
    rows = {}
    for item in (getattr(invoice, "items", None) or []):
        qty = Decimal(str(item.qty or 0))
        if qty <= 0:
            continue
        inv = find_inventory_by_name(item.product_name, company.id)
        if inv is None:
            continue
        slot = rows.setdefault(inv.id, [ZERO, Decimal(str(inv.avg_cost or 0))])
        slot[0] += qty
    return rows


def _sync_cogs(invoice, company, enabled_ids, in_books: bool, entry_date, reason: str) -> dict:
    """Cost of sales: Dr 5000 Cost of Goods Sold, Cr 1300 Inventory at the product's average cost.
    An unchanged invoice keeps its entry (cost is NOT re-priced later); a changed one is reversed and re-posted."""
    live = [
        j for j in JournalEntry.query.filter_by(source_type=COGS_TYPE, source_id=invoice.id, source_active=1).all()
        if j.company_id in enabled_ids
    ]
    total, sig = ZERO, None
    if in_books and company is not None:
        rows = _cogs_rows(invoice, company)
        total = cogs_total((q, c) for q, c in rows.values())
        sig = cogs_signature((i, q, c > 0) for i, (q, c) in rows.items())
    result = {"cogs_posted": 0, "cogs_reversed": 0}
    if total > 0 and len(live) == 1 and live[0].company_id == company.id \
            and live[0].cost_signature == sig and live[0].entry_date == entry_date:
        return result
    for entry in live:
        _reverse(entry, reason)
        result["cogs_reversed"] += 1
    if total > 0:
        accounts = _accounts_for(company, (CODE_COGS, CODE_INVENTORY))
        entry = _new_entry(
            company, COGS_TYPE, invoice.id, invoice.invoice_number, entry_date, invoice.invoice_number,
            f"Cost of sales {invoice.invoice_number}", accounts,
            [(CODE_COGS, total, ZERO), (CODE_INVENTORY, ZERO, total)],
        )
        entry.cost_signature = sig
        result["cogs_posted"] += 1
    return result


def sync_invoice_journal(invoice, deleting: bool = False) -> dict:
    """Make the books match the invoice AND its payments. Safe to call after ANY change.

    Does nothing for companies with accounting OFF. Never commits.
    Raises AccountingError when something cannot be posted (the route must roll back).
    Returns {"posted", "reversed", "payments_posted", "payments_reversed"}.
    """
    enabled_ids = enabled_company_ids()
    # Journals sitting in a company whose accounting was switched OFF are frozen: left alone.
    live = [j for j in _live_journals(invoice.id) if j.company_id in enabled_ids]

    company = Company.query.get(invoice.company_id)
    should = (not deleting) and invoice_should_post(
        invoice.document_type, invoice.status, company is not None and company.id in enabled_ids
    )

    desired = None
    spec = None
    accounts = None
    entry_date = invoice.issued or date.today()
    if should:
        if (invoice.currency or "USD") != (company.currency or "USD"):
            raise AccountingError(
                f"{company.name or 'This company'} keeps its books in {company.currency or 'USD'}, so its "
                f"invoices must be in {company.currency or 'USD'} (this one is {invoice.currency})."
            )
        spec = build_invoice_lines(invoice.total, invoice.vat)
        if spec:
            accounts = _accounts_for(company, NEEDED_CODES)
            desired = {
                "company_id": company.id, "entry_date": entry_date,
                "lines": [(accounts[code].id, d, c) for code, d, c in spec],
            }

    by_id = {j.id: j for j in live}
    reason = "invoice deleted" if deleting else "invoice changed"

    result = {"posted": 0, "reversed": 0}
    for action in plan_sync(_view(live), desired):
        if action[0] == "reverse":
            _reverse(by_id[action[1]], reason)
            result["reversed"] += 1
        else:
            _new_entry(
                company, "invoice", invoice.id, invoice.invoice_number, entry_date,
                invoice.invoice_number, f"Invoice {invoice.invoice_number}", accounts, spec,
            )
            result["posted"] += 1

    in_books = should and desired is not None
    result.update(_sync_cogs(invoice, company, enabled_ids, in_books, entry_date, reason))
    result.update(_sync_payments(invoice, company, enabled_ids, in_books, reason))
    return result


# ---------------------------------------------------------------------
# Wave 3B: opening stock (counts the stock you already have into the books, once)
# ---------------------------------------------------------------------
def stock_value_rows(rows):
    """Pure. rows = (product, balance, avg_cost). Returns (included, missing_cost, total).

    included = [(product, value)] for products with stock AND a cost; value = balance x cost, 2 dp.
    missing_cost = names of products that have stock but no cost yet (they are NOT counted).
    """
    included, missing, total = [], [], ZERO
    for product, balance, cost in rows:
        bal = Decimal(str(balance if balance is not None else 0))
        unit = Decimal(str(cost if cost is not None else 0))
        if bal <= 0:
            continue
        if unit <= 0:
            missing.append(product)
            continue
        value = (bal * unit).quantize(TWO_DP, rounding=ROUND_HALF_UP)
        included.append((product, value))
        total += value
    return included, missing, total


def opening_live(company_id):
    return JournalEntry.query.filter_by(
        source_type=OPENING_TYPE, source_id=company_id, company_id=company_id, source_active=1
    ).first()


def opening_posted(company_id) -> bool:
    return opening_live(company_id) is not None


def account_book_balance(company_id, code) -> Decimal:
    """Debit minus credit of one account in the company's books (Posted + Reversed journals)."""
    row = (
        db.session.query(func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .filter(
            JournalEntry.company_id == company_id, Account.company_id == company_id, Account.code == code,
            JournalEntry.status.in_(("Posted", "Reversed")),
        )
        .first()
    )
    debit, credit = (row or (0, 0))
    return money(Decimal(str(debit or 0)) - Decimal(str(credit or 0)))


def _company_stock(company_id):
    return stock_value_rows(
        (i.product, i.balance, i.avg_cost) for i in Inventory.query.filter_by(company_id=company_id).all()
    )


def opening_preview(company: Company) -> dict:
    included, missing, total = _company_stock(company.id)
    live = opening_live(company.id)
    have = {
        a.code for a in Account.query.filter_by(company_id=company.id, is_active=True).all()
        if a.code in (CODE_INVENTORY, CODE_RETAINED)
    }
    return {
        "company_id": company.id,
        "company_name": company.name,
        "currency": company.currency or "USD",
        "accounting_enabled": bool(company.accounting_enabled),
        "already_posted": live is not None,
        "posted_entry_number": live.entry_number if live else None,
        "posted_date": live.entry_date.isoformat() if live and live.entry_date else None,
        "products_counted": len(included),
        "products_missing_cost": missing,
        "stock_value": f"{total:.2f}",
        "inventory_in_books": f"{account_book_balance(company.id, CODE_INVENTORY):.2f}",
        "accounts_missing": [f"{c} {ALL_NAMES[c]}" for c in (CODE_INVENTORY, CODE_RETAINED) if c not in have],
    }


def post_opening_stock(company: Company, entry_date: date) -> JournalEntry:
    """Dr 1300 Inventory, Cr 3000 Retained Earnings for quantity x average cost. Once per company. No commit."""
    if not company.accounting_enabled:
        raise AccountingError(f"Accounting is OFF for {company.name or 'this company'}. Switch it on first.")
    if opening_posted(company.id):
        raise AccountingError("The opening stock for this company is already in the books. Undo it first if you want to post it again.")
    for later_type in (GRN_TYPE, "cogs", "stock_adjustment"):
        if JournalEntry.query.filter_by(source_type=later_type, company_id=company.id, source_active=1).first():
            raise AccountingError(
                "Stock has already moved through the books (purchases or sales were posted). "
                "Opening stock must be posted BEFORE any of that, otherwise stock is counted twice."
            )
    included, _missing, total = _company_stock(company.id)
    if total <= 0:
        raise AccountingError("There is no stock with a cost to count yet. Give your products an average cost first.")
    accounts = _accounts_for(company, (CODE_INVENTORY, CODE_RETAINED))
    spec = [(CODE_INVENTORY, total, ZERO), (CODE_RETAINED, ZERO, total)]
    return _new_entry(
        company, OPENING_TYPE, company.id, "Opening stock", entry_date, "Opening stock",
        f"Opening stock ({len(included)} product{'s' if len(included) != 1 else ''})", accounts, spec,
    )


def undo_opening_stock(company: Company) -> JournalEntry:
    live = opening_live(company.id)
    if live is None:
        raise AccountingError("There is no opening stock entry to undo for this company.")
    return _reverse(live, "opening stock undone")


# ---------------------------------------------------------------------
# Wave 3C: goods received (GRN) -> stock cost + books
# ---------------------------------------------------------------------
def new_average_cost(old_qty, old_cost, add_qty, add_unit_cost) -> Decimal:
    """Pure. Weighted average: (old value + new value) / (old qty + new qty), 4 decimals.
    Stock that is zero (or less) has no weight, so the new cost simply becomes the cost."""
    oq = Decimal(str(old_qty if old_qty is not None else 0))
    oc = Decimal(str(old_cost if old_cost is not None else 0))
    aq = Decimal(str(add_qty))
    ac = Decimal(str(add_unit_cost))
    if aq <= 0:
        return oc.quantize(FOUR_DP, rounding=ROUND_HALF_UP)
    if oq <= 0:
        return ac.quantize(FOUR_DP, rounding=ROUND_HALF_UP)
    return ((oq * oc + aq * ac) / (oq + aq)).quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def grn_amounts(lines):
    """Pure. lines = (qty, unit_price, vat_percent) -> (value, vat), each line rounded to 2 dp."""
    value, vat = ZERO, ZERO
    for qty, price, pct in lines:
        line = (Decimal(str(qty)) * Decimal(str(price))).quantize(TWO_DP, rounding=ROUND_HALF_UP)
        value += line
        vat += (line * Decimal(str(pct or 0)) / Decimal("100")).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    return value, vat


def build_grn_lines(value: Decimal, vat: Decimal):
    """Pure. Dr Inventory (value) + Dr VAT Receivable (vat), Cr Accounts Payable (value + vat)."""
    value, vat = money(value), money(vat)
    if value < 0 or vat < 0:
        raise AccountingError("A goods received amount cannot be negative.")
    if value == 0 and vat == 0:
        return []
    out = [(CODE_INVENTORY, value, ZERO)]
    if vat > 0:
        out.append((CODE_VAT_RECEIVABLE, vat, ZERO))
    out.append((CODE_PAYABLE, ZERO, value + vat))
    return out


def post_goods_received(company: Company, grn_id: int, grn_number: str, po_number: str, entry_date: date, lines):
    """Post one GRN to the books. Returns the entry, or None when accounting is off / nothing to post. No commit."""
    if not company.accounting_enabled:
        return None
    value, vat = grn_amounts(lines)
    spec = build_grn_lines(value, vat)
    if not spec:
        return None
    accounts = _accounts_for(company, tuple(code for code, _d, _c in spec))
    return _new_entry(
        company, GRN_TYPE, grn_id, grn_number, entry_date, f"{grn_number} / {po_number}",
        f"Goods received {grn_number} on {po_number}", accounts, spec,
    )


# ---------------------------------------------------------------------
# Wave 3E: manual stock adjustments (damage, expiry, corrections, ...)
# ---------------------------------------------------------------------
def build_adjustment_lines(change_qty, avg_cost, reason):
    """Pure. Value = |qty| x average cost (2 dp). Returns (value, spec); spec is [] when there is nothing to book.
    Stock goes DOWN: Dr loss/adjustment expense, Cr 1300 Inventory.  Stock goes UP: Dr 1300 Inventory, Cr 5200."""
    qty = Decimal(str(change_qty))
    cost = Decimal(str(avg_cost if avg_cost is not None else 0))
    value = (abs(qty) * cost).quantize(TWO_DP, rounding=ROUND_HALF_UP)
    if qty == 0 or value <= 0:
        return value, []
    if qty < 0:
        expense = ADJUSTMENT_ACCOUNT.get(reason, CODE_ADJUST)
        return value, [(expense, value, ZERO), (CODE_INVENTORY, ZERO, value)]
    return value, [(CODE_INVENTORY, value, ZERO), (CODE_ADJUST, ZERO, value)]


def post_stock_adjustment(company, movement_id, product, reason, change_qty, avg_cost, entry_date):
    """Book one manual stock change at the product's average cost. Returns the entry, or None when
    accounting is off for the company or there is nothing to book (no cost set / zero change). No commit."""
    if company is None or not company.accounting_enabled:
        return None
    value, spec = build_adjustment_lines(change_qty, avg_cost, reason)
    if not spec:
        return None
    accounts = _accounts_for(company, tuple(code for code, _d, _c in spec))
    qty = Decimal(str(change_qty))
    return _new_entry(
        company, ADJUST_TYPE, movement_id, f"ADJ-{movement_id}", entry_date, (product or "")[:100],
        f"Stock adjustment ({reason}): {product} {qty:+.2f}", accounts, spec,
    )
