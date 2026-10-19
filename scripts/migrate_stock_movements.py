"""One-time migration: create the stock_movements table.

Run from the project folder with the venv Python:
    C:\\Users\\farha\\OneDrive\\Pictures\\invoicing_project\\venv\\Scripts\\python.exe scripts\\migrate_stock_movements.py

It only ADDS a new table. It never touches existing tables or data.
Safe to run twice (it stops if the table already exists).
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

DDL = """
CREATE TABLE stock_movements (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    inventory_id    INT UNSIGNED NOT NULL,
    product_name    VARCHAR(255) NOT NULL,
    change_qty      DECIMAL(10,2) NOT NULL,
    balance_after   DECIMAL(10,2) NOT NULL,
    reason          ENUM('initial','invoice_created','invoice_deleted',
                         'invoice_edited','quotation_converted',
                         'stock_in','stock_out','damage','expiry',
                         'return','rtv','correction') NOT NULL,
    reference_id    INT UNSIGNED NULL,
    reference_label VARCHAR(255) NULL,
    notes           TEXT NULL,
    created_by      INT UNSIGNED NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sm_inventory (inventory_id),
    INDEX idx_sm_reason (reason),
    INDEX idx_sm_created (created_at),
    INDEX idx_sm_reference (reference_id)
) ENGINE=InnoDB
"""


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: create table 'stock_movements'")
        print(f"Database host: {url.host}   name: {url.database}")
        print("This only ADDS a new table. Existing data is not touched.")
        print("=" * 60)

        if "stock_movements" in inspect(db.engine).get_table_names():
            print("Table 'stock_movements' already exists. Nothing to do.")
            return

        try:
            answer = input("Type YES (capitals) to continue: ")
        except EOFError:
            answer = ""
        if answer.strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        try:
            db.session.execute(text(DDL))
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 - show any DB error plainly
            db.session.rollback()
            print(f"ERROR: could not create the table: {exc}")
            sys.exit(1)

        if "stock_movements" in inspect(db.engine).get_table_names():
            print("SUCCESS: table 'stock_movements' created.")
        else:
            print("ERROR: the command ran but the table was not found afterwards.")
            sys.exit(1)


if __name__ == "__main__":
    main()
