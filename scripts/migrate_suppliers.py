"""One-time migration for ERP Phase 3 (Supplier Management).

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_suppliers.py

Step 1 (asks for YES):
    - creates the new table 'suppliers'
    - adds an empty column 'supplier_id' (+ index) to 'parsed_invoices'
    Nothing existing is changed or deleted. Safe to run twice: parts that are
    already done are skipped.

Step 2 (optional, asks for YES, separate question):
    - for every parsed_invoices row with no supplier_id yet, finds the supplier
      with the same name (ignoring capital letters) and links it.
    Rows that don't match are left alone. Safe to run twice.
"""

from __future__ import annotations

import os
import sys

# Make "import app / models / extensions" work no matter where the script is run from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from sqlalchemy import inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import ParsedInvoice, Supplier  # noqa: E402

CREATE_SUPPLIERS = """
CREATE TABLE suppliers (
    id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    trn               VARCHAR(50),
    address           TEXT,
    po_box            VARCHAR(50),
    email             VARCHAR(255),
    phone             VARCHAR(50),
    contact_person    VARCHAR(150),
    payment_terms     VARCHAR(100),
    lead_time_days    INT NULL,
    bank_name         VARCHAR(150),
    bank_account      VARCHAR(100),
    category          VARCHAR(100),
    notes             TEXT,
    is_active         TINYINT(1) NOT NULL DEFAULT 1,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_supplier_name (name),
    INDEX idx_supplier_active (is_active)
) ENGINE=InnoDB
"""

ADD_COLUMN = "ALTER TABLE parsed_invoices ADD COLUMN supplier_id INT UNSIGNED NULL AFTER id"
ADD_INDEX = "ALTER TABLE parsed_invoices ADD INDEX idx_parsed_supplier (supplier_id)"


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def link_parsed_by_name() -> None:
    """Set supplier_id on parsed_invoices rows that have none, matching supplier_name to suppliers.name."""
    by_name: dict[str, int] = {}
    duplicated: set[str] = set()
    for sup in Supplier.query.all():
        key = (sup.name or "").strip().lower()
        if not key:
            continue
        if key in by_name:
            duplicated.add(key)  # two suppliers share a name: too risky to guess, skip those
        else:
            by_name[key] = sup.id

    linked = 0
    no_match = 0
    skipped_dupe = 0
    no_name = 0
    for bill in ParsedInvoice.query.filter(ParsedInvoice.supplier_id.is_(None)).all():
        key = (bill.supplier_name or "").strip().lower()
        if not key:
            no_name += 1
        elif key in duplicated:
            skipped_dupe += 1
        elif key in by_name:
            bill.supplier_id = by_name[key]
            linked += 1
        else:
            no_match += 1
    db.session.commit()

    print(f"SUCCESS: linked {linked} bill(s) to a supplier.")
    print(f"  not matched (no supplier with that name yet): {no_match}")
    print(f"  skipped (bill has no supplier name): {no_name}")
    if skipped_dupe:
        print(f"  skipped (two suppliers share that name): {skipped_dupe}")


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: Phase 3 - suppliers")
        print(f"Database host: {url.host}   name: {url.database}")
        print("Adds a new table and one EMPTY column. Existing data is not changed.")
        print("=" * 60)

        if ask("Type YES (capitals) to run step 1 (create table + column): ") != "YES":
            print("Cancelled. Nothing was changed.")
            return

        try:
            insp = inspect(db.engine)
            if "suppliers" in insp.get_table_names():
                print("- table 'suppliers' already exists: skipped")
            else:
                db.session.execute(text(CREATE_SUPPLIERS))
                db.session.commit()
                print("- table 'suppliers' created")

            columns = [c["name"] for c in insp.get_columns("parsed_invoices")]
            if "supplier_id" in columns:
                print("- column parsed_invoices.supplier_id already exists: skipped")
            else:
                db.session.execute(text(ADD_COLUMN))
                db.session.commit()
                print("- column parsed_invoices.supplier_id added")

            index_names = [i["name"] for i in inspect(db.engine).get_indexes("parsed_invoices")]
            if "idx_parsed_supplier" in index_names:
                print("- index idx_parsed_supplier already exists: skipped")
            else:
                db.session.execute(text(ADD_INDEX))
                db.session.commit()
                print("- index idx_parsed_supplier added")
        except Exception as exc:  # noqa: BLE001 - show any DB error plainly
            db.session.rollback()
            print(f"ERROR: step 1 failed: {exc}")
            sys.exit(1)

        check = inspect(db.engine)
        if ("suppliers" in check.get_table_names()
                and "supplier_id" in [c["name"] for c in check.get_columns("parsed_invoices")]):
            print("SUCCESS: step 1 done.")
        else:
            print("ERROR: the commands ran but the table/column was not found afterwards.")
            sys.exit(1)

        print()
        print("Step 2 (optional): link existing PDF bills to suppliers by name.")
        print("Only bills with no supplier yet are touched. If you have no suppliers yet, nothing will match.")
        if ask("Type YES to run step 2, or just press Enter to skip: ") != "YES":
            print("Step 2 skipped. You can run this script again later to do it.")
            return

        try:
            link_parsed_by_name()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            print(f"ERROR: step 2 failed, nothing was linked: {exc}")
            sys.exit(1)


if __name__ == "__main__":
    main()
