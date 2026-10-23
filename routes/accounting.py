"""routes/accounting.py
Financial Accounting - Stage 1: chart of accounts + manual journal entries.

Registered in app.py with url_prefix="/api/accounting":
  GET    /api/accounting/accounts
  POST   /api/accounting/accounts
  PUT    /api/accounting/accounts/<id>
  DELETE /api/accounting/accounts/<id>            409 if any journal line uses it
  GET    /api/accounting/journals                 ?status=&from=&to=
  GET    /api/accounting/journals/<id>            with lines
  POST   /api/accounting/journals                 creates a Draft
  PUT    /api/accounting/journals/<id>            edits a Draft
  DELETE /api/accounting/journals/<id>            deletes a Draft
  POST   /api/accounting/journals/<id>/post       Draft -> Posted (then locked)
  POST   /api/accounting/journals/<id>/reverse    Posted -> Reversed + new reversing journal

Rules
  - ADMIN ONLY, for reading too. A valid admin token is always required (checked
    even when AUTH_ENABLED=false, like routes/users.py).
  - A journal needs 2+ lines and total debits must equal total credits.
  - Every line has either a debit or a credit (never both, never both zero, never negative).
  - A reversal swaps debit and credit on every line (amounts stay positive, which is
    how accountants do it), is created as Posted, and the original becomes Reversed.
  - Nothing here touches invoices, inventory, purchase orders or clients.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Blueprint, g, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Account, JournalEntry, JournalLine
from utils.auth import get_current_user
from utils.responses import api_response

accounting_bp = Blueprint("accounting", __name__)

ACCOUNT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")
CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,19}$")
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")  # DECIMAL(12,2)
LINE_FIELDS = {"account_id", "debit", "credit", "description"}


# ---------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------
def _err(status: int, error: str, message: str):
    return api_response(error=error, message=message), status


def admin_only(fn):
    """Valid admin token required (checked even when AUTH_ENABLED=false)."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        actor = get_current_user()
        if actor is None:
            return _err(401, "unauthorized", "Authentication required.")
        if actor.role != "admin":
            return _err(403, "forbidden", "Admin access required.")
        g.current_user = actor
        return fn(*args, **kwargs)

    return wrapper


def _commit():
    """Commit; returns an error response tuple on failure, else None."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _err(409, "conflict", "That value is already in use (duplicate code or number).")
    except SQLAlchemyError as exc:
        db.session.rollback()
        return _err(500, "db_error", str(exc))
    return None


def _json_object():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _err(400, "invalid_json", "Request body must be a JSON object.")
    return body, None


def _check_fields(body: dict, allowed: set):
    unknown = set(body) - allowed
    if unknown:
        return _err(400, "unknown_fields", f"Unrecognized field(s): {', '.join(sorted(unknown))}.")
    return None


def _clean_text(value, label: str, max_len: int, required: bool = False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return None, f"{label} is required."
        return None, None
    if not isinstance(value, str):
        return None, f"{label} must be text."
    value = value.strip()
    if len(value) > max_len:
        return None, f"{label} must be {max_len} characters or fewer."
    return value, None


def parse_money(value, label: str):
    """(Decimal rounded to 2 places, error). Accepts numbers or numeric strings, never negative."""
    if value is None or value == "":
        return Decimal("0.00"), None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None, f"{label} must be a number."
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None, f"{label} must be a number."
    if not amount.is_finite():
        return None, f"{label} must be a number."
    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    if amount < 0:
        return None, f"{label} cannot be negative."
    if amount > MAX_AMOUNT:
        return None, f"{label} is too large."
    return amount, None


def parse_date(value):
    """(date, error). Expects YYYY-MM-DD."""
    if not isinstance(value, str):
        return None, "Date is required (YYYY-MM-DD)."
    try:
        d = date.fromisoformat(value.strip())
    except ValueError:
        return None, "Date must look like 2026-10-31."
    if d.year < 2000 or d.year > 2100:
        return None, "Date must be between the years 2000 and 2100."
    return d, None


def check_lines(raw_lines):
    """Validate the lines of a journal without touching the database.

    Returns (clean_lines, error_message). clean_lines is a list of dicts with
    account_id, debit, credit, description. Rules: at least 2 lines, each line has
    exactly one of debit / credit above zero, total debit == total credit.
    """
    if not isinstance(raw_lines, list):
        return None, "Lines must be a list."
    if len(raw_lines) < 2:
        return None, "A journal needs at least 2 lines."
    clean = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for i, raw in enumerate(raw_lines, start=1):
        label = f"Line {i}"
        if not isinstance(raw, dict):
            return None, f"{label} must be an object."
        unknown = set(raw) - LINE_FIELDS
        if unknown:
            return None, f"{label} has unrecognized field(s): {', '.join(sorted(unknown))}."
        account_id = raw.get("account_id")
        if isinstance(account_id, bool) or not isinstance(account_id, int):
            return None, f"{label}: choose an account."
        debit, error = parse_money(raw.get("debit"), f"{label} debit")
        if error:
            return None, error
        credit, error = parse_money(raw.get("credit"), f"{label} credit")
        if error:
            return None, error
        if debit > 0 and credit > 0:
            return None, f"{label} cannot have both a debit and a credit."
        if debit == 0 and credit == 0:
            return None, f"{label} needs a debit or a credit amount."
        desc, error = _clean_text(raw.get("description"), f"{label} description", 255)
        if error:
            return None, error
        total_debit += debit
        total_credit += credit
        clean.append({"account_id": account_id, "debit": debit, "credit": credit, "description": desc})
    if total_debit != total_credit:
        diff = abs(total_debit - total_credit)
        return None, (
            f"The journal does not balance: debits are {total_debit:,.2f} and credits are "
            f"{total_credit:,.2f} (difference {diff:,.2f}). Debits must equal credits."
        )
    return clean, None


def _check_accounts_usable(account_ids):
    """Every account must exist and be active. Returns an error message or None."""
    ids = set(account_ids)
    found = {a.id: a for a in Account.query.filter(Account.id.in_(ids)).all()}
    missing = sorted(ids - set(found))
    if missing:
        return "Unknown account: " + ", ".join(str(i) for i in missing) + "."
    inactive = [a.code for a in found.values() if not a.is_active]
    if inactive:
        return "These accounts are switched off and cannot be used: " + ", ".join(sorted(inactive)) + "."
    return None


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


def _lines_from_clean(clean):
    return [
        JournalLine(
            account_id=c["account_id"], debit=c["debit"], credit=c["credit"], description=c["description"],
        )
        for c in clean
    ]


def _swapped(entry):
    """The lines of `entry` with debit and credit exchanged (for a reversal)."""
    return [
        JournalLine(
            account_id=ln.account_id, debit=ln.credit, credit=ln.debit,
            description=ln.description,
        )
        for ln in entry.lines
    ]


# ---------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------
def _clean_account_body(body: dict, existing=None):
    """Returns (values dict, error response). For an edit, only fields in body are returned."""
    error = _check_fields(body, {"code", "name", "type", "is_active", "parent_id"})
    if error:
        return None, error
    values = {}
    creating = existing is None

    if creating or "code" in body:
        code, msg = _clean_text(body.get("code"), "Code", 20, required=True)
        if msg:
            return None, _err(400, "bad_request", msg)
        if not CODE_RE.match(code):
            return None, _err(400, "bad_request",
                              "Code may use letters, numbers, dot, dash and underscore (up to 20 characters).")
        values["code"] = code
    if creating or "name" in body:
        name, msg = _clean_text(body.get("name"), "Name", 100, required=True)
        if msg:
            return None, _err(400, "bad_request", msg)
        values["name"] = name
    if creating or "type" in body:
        if body.get("type") not in ACCOUNT_TYPES:
            return None, _err(400, "bad_request", "Type must be one of: " + ", ".join(ACCOUNT_TYPES) + ".")
        values["type"] = body["type"]
    if "is_active" in body:
        if not isinstance(body["is_active"], bool):
            return None, _err(400, "bad_request", "is_active must be true or false.")
        values["is_active"] = body["is_active"]
    if "parent_id" in body:
        pid = body["parent_id"]
        if pid is not None:
            if isinstance(pid, bool) or not isinstance(pid, int):
                return None, _err(400, "bad_request", "parent_id must be an account id or null.")
            parent = Account.query.get(pid)
            if parent is None:
                return None, _err(400, "bad_request", "Parent account not found.")
            if existing is not None:
                # no loops: walk up from the chosen parent, we must never reach this account
                node, hops = parent, 0
                while node is not None and hops < 100:
                    if node.id == existing.id:
                        return None, _err(400, "bad_request", "An account cannot be its own parent or sit under its own child.")
                    node = Account.query.get(node.parent_id) if node.parent_id else None
                    hops += 1
        values["parent_id"] = pid
    return values, None


def _code_taken(code: str, except_id=None) -> bool:
    row = Account.query.filter(Account.code == code).first()
    return row is not None and row.id != except_id


def _account_in_use(account_id: int) -> bool:
    return db.session.query(JournalLine.id).filter(JournalLine.account_id == account_id).first() is not None


@accounting_bp.route("/accounts", methods=["GET"])
@admin_only
def list_accounts():
    accounts = Account.query.order_by(Account.code).all()
    return api_response(data=[a.to_dict() for a in accounts]), 200


@accounting_bp.route("/accounts", methods=["POST"])
@admin_only
def create_account():
    body, error = _json_object()
    if error:
        return error
    values, error = _clean_account_body(body)
    if error:
        return error
    if _code_taken(values["code"]):
        return _err(409, "conflict", f"Account code {values['code']} already exists.")
    account = Account(is_active=True, **values)
    db.session.add(account)
    error = _commit()
    if error:
        return error
    return api_response(data=account.to_dict()), 201


@accounting_bp.route("/accounts/<int:account_id>", methods=["PUT"])
@admin_only
def update_account(account_id: int):
    account = Account.query.get(account_id)
    if account is None:
        return _err(404, "not_found", "Account not found.")
    body, error = _json_object()
    if error:
        return error
    values, error = _clean_account_body(body, existing=account)
    if error:
        return error
    if "code" in values and values["code"] != account.code and _code_taken(values["code"], account.id):
        return _err(409, "conflict", f"Account code {values['code']} already exists.")
    if "type" in values and values["type"] != account.type and _account_in_use(account.id):
        return _err(409, "conflict", "This account already has journal lines, so its type cannot change.")
    for field, value in values.items():
        setattr(account, field, value)
    error = _commit()
    if error:
        return error
    return api_response(data=account.to_dict()), 200


@accounting_bp.route("/accounts/<int:account_id>", methods=["DELETE"])
@admin_only
def delete_account(account_id: int):
    account = Account.query.get(account_id)
    if account is None:
        return _err(404, "not_found", "Account not found.")
    if _account_in_use(account.id):
        return _err(409, "conflict",
                    "This account is used in journal lines and cannot be deleted. Switch it off instead.")
    db.session.delete(account)
    error = _commit()
    if error:
        return error
    return api_response(data={"id": account_id, "deleted": True}), 200


# ---------------------------------------------------------------------
# journals
# ---------------------------------------------------------------------
def _find_entry(entry_id: int):
    return JournalEntry.query.get(entry_id)


def _not_found():
    return _err(404, "not_found", "Journal not found.")


@accounting_bp.route("/journals", methods=["GET"])
@admin_only
def list_journals():
    query = JournalEntry.query
    status = request.args.get("status")
    if status:
        if status not in ("Draft", "Posted", "Reversed"):
            return _err(400, "bad_request", "status must be Draft, Posted or Reversed.")
        query = query.filter(JournalEntry.status == status)
    for key, op in (("from", "ge"), ("to", "le")):
        raw = request.args.get(key)
        if raw:
            d, msg = parse_date(raw)
            if msg:
                return _err(400, "bad_request", f"'{key}': {msg}")
            col = JournalEntry.entry_date
            query = query.filter(col >= d if op == "ge" else col <= d)
    entries = query.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).all()
    return api_response(data=[e.to_dict() for e in entries]), 200


@accounting_bp.route("/journals/<int:entry_id>", methods=["GET"])
@admin_only
def get_journal(entry_id: int):
    entry = _find_entry(entry_id)
    if entry is None:
        return _not_found()
    return api_response(data=entry.to_dict(include_lines=True)), 200


def _read_journal_body(body: dict, require_all: bool):
    """Returns (entry_date, reference, description, clean_lines, error response)."""
    error = _check_fields(body, {"entry_date", "reference", "description", "lines"})
    if error:
        return None, None, None, None, error
    entry_date = reference = description = clean = None
    if require_all or "entry_date" in body:
        entry_date, msg = parse_date(body.get("entry_date"))
        if msg:
            return None, None, None, None, _err(400, "bad_request", msg)
    if require_all or "reference" in body:
        reference, msg = _clean_text(body.get("reference"), "Reference", 100)
        if msg:
            return None, None, None, None, _err(400, "bad_request", msg)
    if require_all or "description" in body:
        description, msg = _clean_text(body.get("description"), "Description", 5000)
        if msg:
            return None, None, None, None, _err(400, "bad_request", msg)
    if require_all or "lines" in body:
        clean, msg = check_lines(body.get("lines"))
        if msg:
            return None, None, None, None, _err(400, "bad_request", msg)
        msg = _check_accounts_usable(c["account_id"] for c in clean)
        if msg:
            return None, None, None, None, _err(400, "bad_request", msg)
    return entry_date, reference, description, clean, None


@accounting_bp.route("/journals", methods=["POST"])
@admin_only
def create_journal():
    body, error = _json_object()
    if error:
        return error
    entry_date, reference, description, clean, error = _read_journal_body(body, require_all=True)
    if error:
        return error

    entry = None
    for _attempt in range(3):  # a second admin saving at the same moment could take our number
        entry = JournalEntry(
            entry_number=_next_entry_number(entry_date.year), entry_date=entry_date,
            reference=reference, description=description, status="Draft",
            created_by=g.current_user.id,
        )
        entry.lines = _lines_from_clean(clean)
        db.session.add(entry)
        try:
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()
            entry = None
        except SQLAlchemyError as exc:
            db.session.rollback()
            return _err(500, "db_error", str(exc))
    if entry is None:
        return _err(409, "conflict", "Could not get a free journal number. Please try again.")
    return api_response(data=entry.to_dict(include_lines=True)), 201


@accounting_bp.route("/journals/<int:entry_id>", methods=["PUT"])
@admin_only
def update_journal(entry_id: int):
    entry = _find_entry(entry_id)
    if entry is None:
        return _not_found()
    if entry.status != "Draft":
        return _err(409, "conflict", "Only a Draft journal can be edited. Reverse a posted journal instead.")
    body, error = _json_object()
    if error:
        return error
    entry_date, reference, description, clean, error = _read_journal_body(body, require_all=False)
    if error:
        return error
    if "entry_date" in body:
        entry.entry_date = entry_date
    if "reference" in body:
        entry.reference = reference
    if "description" in body:
        entry.description = description
    if clean is not None:
        entry.lines = _lines_from_clean(clean)
    error = _commit()
    if error:
        return error
    return api_response(data=entry.to_dict(include_lines=True)), 200


@accounting_bp.route("/journals/<int:entry_id>", methods=["DELETE"])
@admin_only
def delete_journal(entry_id: int):
    entry = _find_entry(entry_id)
    if entry is None:
        return _not_found()
    if entry.status != "Draft":
        return _err(409, "conflict", "Only a Draft journal can be deleted. Reverse a posted journal instead.")
    db.session.delete(entry)
    error = _commit()
    if error:
        return error
    return api_response(data={"id": entry_id, "deleted": True}), 200


@accounting_bp.route("/journals/<int:entry_id>/post", methods=["POST"])
@admin_only
def post_journal(entry_id: int):
    entry = _find_entry(entry_id)
    if entry is None:
        return _not_found()
    if entry.status != "Draft":
        return _err(409, "conflict", f"This journal is already {entry.status}.")
    # check again from what is stored, so a posted journal is always balanced
    raw = [
        {"account_id": ln.account_id, "debit": str(ln.debit or 0), "credit": str(ln.credit or 0)}
        for ln in entry.lines
    ]
    clean, msg = check_lines(raw)
    if msg:
        return _err(400, "bad_request", msg)
    msg = _check_accounts_usable(c["account_id"] for c in clean)
    if msg:
        return _err(400, "bad_request", msg)
    entry.status = "Posted"
    error = _commit()
    if error:
        return error
    return api_response(data=entry.to_dict(include_lines=True)), 200


@accounting_bp.route("/journals/<int:entry_id>/reverse", methods=["POST"])
@admin_only
def reverse_journal(entry_id: int):
    original = _find_entry(entry_id)
    if original is None:
        return _not_found()
    if original.status == "Reversed":
        return _err(409, "conflict", "This journal has already been reversed.")
    if original.status != "Posted":
        return _err(409, "conflict", "Only a Posted journal can be reversed. Delete a Draft instead.")
    if original.reversal_of_id is not None:
        return _err(409, "conflict", "This journal is itself a reversal and cannot be reversed again.")

    raw = request.get_data(cache=True)
    body = {}
    if raw and raw.strip():
        body, error = _json_object()
        if error:
            return error
    error = _check_fields(body, {"entry_date", "reason"})
    if error:
        return error
    if "entry_date" in body:
        rev_date, msg = parse_date(body["entry_date"])
        if msg:
            return _err(400, "bad_request", msg)
    else:
        rev_date = date.today()
    reason, msg = _clean_text(body.get("reason"), "Reason", 500)
    if msg:
        return _err(400, "bad_request", msg)

    description = f"Reversal of {original.entry_number}"
    if reason:
        description += f": {reason}"
    reversal = None
    for _attempt in range(3):
        reversal = JournalEntry(
            entry_number=_next_entry_number(rev_date.year), entry_date=rev_date,
            reference=f"REV {original.entry_number}"[:100], description=description,
            status="Posted", reversal_of_id=original.id, created_by=g.current_user.id,
        )
        reversal.lines = _swapped(original)
        original.status = "Reversed"
        db.session.add(reversal)
        try:
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()
            original = _find_entry(entry_id)
            reversal = None
        except SQLAlchemyError as exc:
            db.session.rollback()
            return _err(500, "db_error", str(exc))
    if reversal is None:
        return _err(409, "conflict", "Could not get a free journal number. Please try again.")
    return api_response(data={
        "reversal": reversal.to_dict(include_lines=True),
        "original": original.to_dict(),
    }), 201
