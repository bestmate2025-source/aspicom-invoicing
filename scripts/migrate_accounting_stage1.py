"""One-time migration: Financial Accounting Stage 1.

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_accounting_stage1.py

Creates three NEW tables: accounts, journal_entries, journal_lines.
It does not read, change or delete any existing table. Safe to run twice:
a table that already exists is skipped.
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from sqlalchemy import inspect  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Account, JournalEntry, JournalLine  # noqa: E402

# order matters: journal_lines points at the other two
TABLES = (Account.__table__, JournalEntry.__table__, JournalLine.__table__)


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: Financial Accounting - Stage 1")
        print(f"Database host: {url.host}   name: {url.database}")
        print("This creates 3 new tables. No existing table is touched.")
        print("=" * 60)

        existing = set(inspect(db.engine).get_table_names())
        todo = [t for t in TABLES if t.name not in existing]
        for t in TABLES:
            print(f"  {t.name:18} {'already exists, skipped' if t.name in existing else 'will be created'}")
        if not todo:
            print("\nNothing to do.")
            return

        if input("\nType YES (capitals) to continue: ").strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        for t in todo:
            t.create(bind=db.engine, checkfirst=True)
            print(f"  created {t.name}")
        print("\nDONE. Accounting tables are ready.")


if __name__ == "__main__":
    main()
