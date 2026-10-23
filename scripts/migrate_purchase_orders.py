"""One-time migration for ERP Phase 4 (Purchase Orders and Goods Received Notes).

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_purchase_orders.py

It only ADDS four new tables. It never touches existing tables or data.
Safe to run twice: tables that already exist are skipped.
Needs the 'suppliers' table (Phase 3) to exist first.
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

# Order matters: each table can only point at tables created before it.
TABLES = [
    ("purchase_orders", """
CREATE TABLE purchase_orders (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    supplier_id    INT UNSIGNED NOT NULL,
    po_number      VARCHAR(50) NOT NULL,
    status         ENUM('Draft','Sent','Partial','Received','Cancelled') NOT NULL DEFAULT 'Draft',
    order_date     DATE NOT NULL,
    expected_date  DATE NULL,
    subtotal       DECIMAL(12,2) NOT NULL DEFAULT 0,
    vat            DECIMAL(12,2) NOT NULL DEFAULT 0,
    total          DECIMAL(12,2) NOT NULL DEFAULT 0,
    notes          TEXT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_po_number (po_number),
    INDEX idx_po_supplier (supplier_id),
    INDEX idx_po_status (status),
    CONSTRAINT fk_po_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id) ON DELETE RESTRICT
) ENGINE=InnoDB
"""),
    ("purchase_order_items", """
CREATE TABLE purchase_order_items (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    po_id         INT UNSIGNED NOT NULL,
    product_name  VARCHAR(255) NOT NULL,
    brand         VARCHAR(255) NULL,
    unit_price    DECIMAL(12,2) NOT NULL DEFAULT 0,
    qty_ordered   DECIMAL(10,2) NOT NULL DEFAULT 0,
    qty_received  DECIMAL(10,2) NOT NULL DEFAULT 0,
    vat_percent   DECIMAL(5,2) NOT NULL DEFAULT 0,
    amount        DECIMAL(12,2) NOT NULL DEFAULT 0,
    INDEX idx_poi_po (po_id),
    CONSTRAINT fk_poi_po FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE
) ENGINE=InnoDB
"""),
    ("goods_received_notes", """
CREATE TABLE goods_received_notes (
    id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    po_id          INT UNSIGNED NOT NULL,
    grn_number     VARCHAR(50) NOT NULL,
    received_date  DATE NOT NULL,
    received_by    INT UNSIGNED NULL,
    notes          TEXT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_grn_number (grn_number),
    INDEX idx_grn_po (po_id),
    CONSTRAINT fk_grn_po FOREIGN KEY (po_id) REFERENCES purchase_orders(id) ON DELETE CASCADE
) ENGINE=InnoDB
"""),
    ("goods_received_note_items", """
CREATE TABLE goods_received_note_items (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    grn_id        INT UNSIGNED NOT NULL,
    po_item_id    INT UNSIGNED NOT NULL,
    product_name  VARCHAR(255) NOT NULL,
    qty_received  DECIMAL(10,2) NOT NULL DEFAULT 0,
    INDEX idx_grni_grn (grn_id),
    INDEX idx_grni_item (po_item_id),
    CONSTRAINT fk_grni_grn FOREIGN KEY (grn_id) REFERENCES goods_received_notes(id) ON DELETE CASCADE
) ENGINE=InnoDB
"""),
]


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: Phase 4 - purchase orders and goods received notes")
        print(f"Database host: {url.host}   name: {url.database}")
        print("This only ADDS new tables. Existing data is not touched.")
        print("=" * 60)

        existing = set(inspect(db.engine).get_table_names())
        if "suppliers" not in existing:
            print("ERROR: the 'suppliers' table is missing. Run scripts\\migrate_suppliers.py first.")
            sys.exit(1)

        todo = [name for name, _ in TABLES if name not in existing]
        for name, _ in TABLES:
            if name in existing:
                print(f"- table '{name}' already exists: skipped")
        if not todo:
            print("Nothing to do. All Phase 4 tables already exist.")
            return

        print("Will create: " + ", ".join(todo))
        try:
            answer = input("Type YES (capitals) to continue: ")
        except EOFError:
            answer = ""
        if answer.strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        try:
            for name, ddl in TABLES:
                if name in todo:
                    db.session.execute(text(ddl))
                    db.session.commit()
                    print(f"- table '{name}' created")
        except Exception as exc:  # noqa: BLE001 - show any DB error plainly
            db.session.rollback()
            print(f"ERROR: stopped at a failed step: {exc}")
            print("Tables created before the failure are kept. Fix the problem and run this script again.")
            sys.exit(1)

        after = set(inspect(db.engine).get_table_names())
        if all(name in after for name, _ in TABLES):
            print("SUCCESS: all Phase 4 tables exist.")
        else:
            print("ERROR: the commands ran but some tables were not found afterwards.")
            sys.exit(1)


if __name__ == "__main__":
    main()
