"""One-time backfill: give every existing product an 'initial' stock movement.

Run from the project folder with the venv Python, AFTER the migration and
BEFORE creating any new invoice:
    C:\\Users\\farha\\OneDrive\\Pictures\\invoicing_project\\venv\\Scripts\\python.exe scripts\\backfill_movements.py

It does NOT change any inventory balance — it only records today's balance as
the starting point of each product's history.
Safe to run twice: products that already have an 'initial' row are skipped.
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

from sqlalchemy import inspect  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Inventory, StockMovement  # noqa: E402


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("BACKFILL: add 'initial' stock movements for existing products")
        print(f"Database host: {url.host}   name: {url.database}")
        print("Inventory balances are NOT changed.")
        print("=" * 60)

        if "stock_movements" not in inspect(db.engine).get_table_names():
            print("ERROR: table 'stock_movements' does not exist. Run migrate_stock_movements.py first.")
            sys.exit(1)

        try:
            answer = input("Type YES (capitals) to continue: ")
        except EOFError:
            answer = ""
        if answer.strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        added = 0
        skipped = 0
        try:
            for inv in Inventory.query.order_by(Inventory.id).all():
                already = StockMovement.query.filter_by(
                    inventory_id=inv.id, reason="initial"
                ).first()
                if already is not None:
                    skipped += 1
                    continue
                db.session.add(StockMovement(
                    inventory_id=inv.id,
                    product_name=inv.product,
                    change_qty=inv.balance,
                    balance_after=inv.balance,
                    reason="initial",
                    notes="Baseline at Phase 1 introduction",
                ))
                added += 1
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 - show any DB error plainly
            db.session.rollback()
            print(f"ERROR: backfill failed, nothing was saved: {exc}")
            sys.exit(1)

        total = StockMovement.query.count()
        print(f"SUCCESS: backfilled {added} product(s), skipped {skipped} (already had an initial row).")
        print(f"Total rows now in stock_movements: {total}")


if __name__ == "__main__":
    main()
