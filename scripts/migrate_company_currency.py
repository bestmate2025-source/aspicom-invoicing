"""One-time migration for ERP Phase 4.5 (Company Currency).

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_company_currency.py

What it does:
  1. Adds the column  company.currency  (VARCHAR(3), NOT NULL, default 'USD').
  2. For each company, looks at the currency of its existing invoices and, if
     ALL of them use one currency (USD or AED), sets the company to that
     currency, so old data matches the new "1 company = 1 currency" rule.
     Companies with no invoices stay USD. Companies with mixed old invoices
     stay USD and are listed so you can decide.

It never deletes or changes invoices. Safe to run twice: if the column is
already there, step 1 is skipped (step 2 is then not repeated either).
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

ALLOWED = ("USD", "AED")


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: Phase 4.5 - company currency")
        print(f"Database host: {url.host}   name: {url.database}")
        print("This adds one column to the 'company' table. Invoices are not changed.")
        print("=" * 60)

        insp = inspect(db.engine)
        if "company" not in insp.get_table_names():
            print("ERROR: the 'company' table was not found.")
            sys.exit(1)
        columns = {c["name"] for c in insp.get_columns("company")}
        if "currency" in columns:
            print("Column company.currency already exists. Nothing to do.")
            return

        # Work out what each company should become, from its existing invoices.
        plan = []
        companies = db.session.execute(text("SELECT id, name FROM company ORDER BY id")).fetchall()
        for cid, cname in companies:
            used = [r[0] for r in db.session.execute(
                text("SELECT DISTINCT currency FROM invoices WHERE company_id = :c"), {"c": cid}
            ).fetchall()]
            if len(used) == 1 and used[0] in ALLOWED:
                plan.append((cid, cname, used[0], f"all its invoices are {used[0]}"))
            elif len(used) == 0:
                plan.append((cid, cname, "USD", "no invoices yet, default"))
            else:
                plan.append((cid, cname, "USD", "MIXED old invoices (" + ", ".join(sorted(used)) + ") - set it yourself in the Company tab"))

        print("Will add column  company.currency  VARCHAR(3) NOT NULL DEFAULT 'USD'")
        print("Companies will be set to:")
        for cid, cname, cur, why in plan:
            print(f"  #{cid}  {cname}  ->  {cur}   ({why})")
        try:
            answer = input("Type YES (capitals) to continue: ")
        except EOFError:
            answer = ""
        if answer.strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        try:
            db.session.execute(text("ALTER TABLE company ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'USD'"))
            for cid, _cname, cur, _why in plan:
                db.session.execute(text("UPDATE company SET currency = :cur WHERE id = :c"), {"cur": cur, "c": cid})
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            print(f"ERROR: {exc}")
            print("If the column was added but the update failed, set each company's currency in the Company tab.")
            sys.exit(1)
        print("DONE. company.currency added and filled in.")


if __name__ == "__main__":
    main()
