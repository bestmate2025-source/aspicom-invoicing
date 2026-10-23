"""One-time migration: Financial Accounting - Wave 2A (company books).

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_accounting_stage2a.py

BACK UP YOUR DATABASE FIRST (a dump). This changes the accounts and
journal_entries tables. Invoices, payments, clients, stock are not touched.

What it does:
  1. accounts + journal_entries get a company_id column. Existing rows (your
     Stage 1 test accounts / journals) are moved to the FIRST company, nothing is deleted.
  2. journal_entries get source_type (manual / invoice / payment / invoice_reversal /
     payment_reversal), source_id and source_ref (used from Wave 2B on).
  3. Account codes become unique PER COMPANY (not across the whole database), so every
     company can have its own 1000 Cash in Hand.
  4. One journal per source: unique (source_type, source_id, company_id).

Safe to run twice: every step checks first and skips what is already done.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from sqlalchemy import inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402

SOURCE_ENUM = "ENUM('manual','invoice','payment','invoice_reversal','payment_reversal')"


def insp():
    return inspect(db.engine)  # fresh each time, so it sees our own changes


def columns(table):
    return {c["name"]: c for c in insp().get_columns(table)}


def run(sql, **params):
    with db.engine.begin() as conn:
        return conn.execute(text(sql), params)


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 64)
        print("MIGRATION: Accounting Wave 2A - company books")
        print(f"Database host: {url.host}   name: {url.database}")
        print("Changes the accounts and journal_entries tables only.")
        print("BACK UP THE DATABASE BEFORE YOU CONTINUE.")
        print("=" * 64)

        tables = set(insp().get_table_names())
        for t in ("accounts", "journal_entries", "journal_lines", "company"):
            if t not in tables:
                print(f"ERROR: table '{t}' not found. Run the Stage 1 migration first.")
                sys.exit(1)

        first = run("SELECT MIN(id) FROM company").scalar()
        if first is None:
            print("ERROR: there is no company yet. Create a company first.")
            sys.exit(1)
        n_acc = run("SELECT COUNT(*) FROM accounts").scalar()
        n_jrn = run("SELECT COUNT(*) FROM journal_entries").scalar()
        print(f"Existing accounts: {n_acc}   existing journals: {n_jrn}")
        print(f"They will be assigned to company id {first} (the first company) if they have none.")

        if input("\nType YES (capitals) to continue: ").strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        # 1) new columns
        if "company_id" not in columns("accounts"):
            run("ALTER TABLE accounts ADD COLUMN company_id INT NULL")
            print("  accounts.company_id added")
        jcols = columns("journal_entries")
        if "company_id" not in jcols:
            run("ALTER TABLE journal_entries ADD COLUMN company_id INT NULL")
            print("  journal_entries.company_id added")
        if "source_type" not in jcols:
            run(f"ALTER TABLE journal_entries ADD COLUMN source_type {SOURCE_ENUM} NOT NULL DEFAULT 'manual'")
            print("  journal_entries.source_type added")
        if "source_id" not in jcols:
            run("ALTER TABLE journal_entries ADD COLUMN source_id INT NULL")
            print("  journal_entries.source_id added")
        if "source_ref" not in jcols:
            run("ALTER TABLE journal_entries ADD COLUMN source_ref VARCHAR(100) NULL")
            print("  journal_entries.source_ref added")

        # 2) fill existing rows, then make company_id required
        a = run("UPDATE accounts SET company_id = :c WHERE company_id IS NULL", c=first).rowcount
        j = run("UPDATE journal_entries SET company_id = :c WHERE company_id IS NULL", c=first).rowcount
        print(f"  moved {a} account(s) and {j} journal(s) to company {first}")
        for table in ("accounts", "journal_entries"):
            if columns(table)["company_id"]["nullable"]:
                run(f"ALTER TABLE {table} MODIFY COLUMN company_id INT NOT NULL")
                print(f"  {table}.company_id is now required")

        # 3) foreign keys to company
        for table, fk in (("accounts", "fk_accounts_company"), ("journal_entries", "fk_journal_entries_company")):
            has = any(f["constrained_columns"] == ["company_id"] for f in insp().get_foreign_keys(table))
            if not has:
                run(f"ALTER TABLE {table} ADD CONSTRAINT {fk} FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE RESTRICT")
                print(f"  foreign key added on {table}")

        # 4) account code: unique per company instead of unique everywhere
        for idx in insp().get_indexes("accounts"):
            if idx.get("unique") and list(idx["column_names"]) == ["code"]:
                run(f"ALTER TABLE accounts DROP INDEX `{idx['name']}`")
                print(f"  old rule 'code unique everywhere' removed ({idx['name']})")
        if not any(i["name"] == "uq_accounts_company_code" for i in insp().get_indexes("accounts")):
            run("ALTER TABLE accounts ADD UNIQUE INDEX uq_accounts_company_code (company_id, code)")
            print("  rule added: account code unique per company")

        # 5) one journal per source document
        if not any(i["name"] == "uq_journal_source" for i in insp().get_indexes("journal_entries")):
            run("ALTER TABLE journal_entries ADD UNIQUE INDEX uq_journal_source (source_type, source_id, company_id)")
            print("  rule added: one journal per invoice / payment")

        print("\nDONE. Accounting is ready for company books.")


if __name__ == "__main__":
    main()
