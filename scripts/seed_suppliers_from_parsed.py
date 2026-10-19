"""One-time script: create a supplier for every supplier name found in your imported PDF bills.

Run from the project folder with the venv Python, AFTER migrate_suppliers.py:
    .\\venv\\Scripts\\python.exe scripts\\seed_suppliers_from_parsed.py

For each distinct supplier_name in parsed_invoices (capital letters ignored):
    - name = the spelling used most often
    - trn  = the bill TRN, but ONLY if every bill from that supplier has the same one
    - everything else empty
Suppliers that already exist (same name, any capitals) are skipped.
Names shorter than 3 characters are skipped (the Suppliers screen would refuse to save them).
Afterwards it links the bills (that have no supplier yet) to their supplier by name.

It shows what it is about to create and asks for YES first. Safe to run twice.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

# Make "import app / models / extensions" work no matter where the script is run from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from sqlalchemy import inspect  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import ParsedInvoice, Supplier  # noqa: E402

MIN_NAME = 3


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("SEED: create suppliers from imported PDF bills")
        print(f"Database host: {url.host}   name: {url.database}")
        print("Only ADDS supplier rows and fills the empty supplier_id on bills.")
        print("=" * 60)

        insp = inspect(db.engine)
        if "suppliers" not in insp.get_table_names() \
                or "supplier_id" not in [c["name"] for c in insp.get_columns("parsed_invoices")]:
            print("ERROR: run scripts\\migrate_suppliers.py first (table or column is missing).")
            sys.exit(1)

        bills = ParsedInvoice.query.order_by(ParsedInvoice.id).all()
        groups: dict[str, dict] = {}
        no_name = 0
        for bill in bills:
            raw = (bill.supplier_name or "").strip()
            if not raw:
                no_name += 1
                continue
            grp = groups.setdefault(raw.lower(), {"names": Counter(), "trns": set()})
            grp["names"][raw] += 1
            trn = (bill.header_trn or "").strip()
            if trn:
                grp["trns"].add(trn)

        existing = {(s.name or "").strip().lower() for s in Supplier.query.all()}

        to_create = []
        skipped_existing = 0
        skipped_short = []
        for key in sorted(groups):
            grp = groups[key]
            name = grp["names"].most_common(1)[0][0]
            if key in existing:
                skipped_existing += 1
            elif len(name) < MIN_NAME:
                skipped_short.append(name)
            else:
                trn = next(iter(grp["trns"])) if len(grp["trns"]) == 1 else None
                to_create.append((name, trn))

        print(f"Bills read: {len(bills)}   (without a supplier name: {no_name})")
        print(f"Distinct supplier names: {len(groups)}")
        print(f"Already exist as suppliers (skipped): {skipped_existing}")
        if skipped_short:
            print(f"Too short to save (skipped): {', '.join(skipped_short)}")
        print(f"Will create {len(to_create)} supplier(s):")
        for name, trn in to_create:
            print(f"  - {name}   (TRN: {trn or '-'})")

        if not to_create:
            print("Nothing new to create.")
        elif ask("Type YES (capitals) to create them: ") != "YES":
            print("Cancelled. Nothing was changed.")
            return

        try:
            for name, trn in to_create:
                db.session.add(Supplier(name=name, trn=trn))
            db.session.flush()

            by_name: dict[str, int] = {}
            duplicated: set[str] = set()
            for sup in Supplier.query.all():
                key = (sup.name or "").strip().lower()
                if key in by_name:
                    duplicated.add(key)
                else:
                    by_name[key] = sup.id

            linked = 0
            for bill in ParsedInvoice.query.filter(ParsedInvoice.supplier_id.is_(None)).all():
                key = (bill.supplier_name or "").strip().lower()
                if key and key not in duplicated and key in by_name:
                    bill.supplier_id = by_name[key]
                    linked += 1
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 - show any DB error plainly
            db.session.rollback()
            print(f"ERROR: nothing was saved: {exc}")
            sys.exit(1)

        print(f"SUCCESS: created {len(to_create)} supplier(s), linked {linked} bill(s).")
        print(f"Total suppliers now: {Supplier.query.count()}")


if __name__ == "__main__":
    main()
