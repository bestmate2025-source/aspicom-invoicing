"""One-time migration: more user roles + phone, job title, allowed companies.

Run from the project folder with the venv Python:
    .\\venv\\Scripts\\python.exe scripts\\migrate_user_roles.py

What it does to the 'users' table:
  1. Lets  role  be: admin, manager, sales, store, accountant, user.
     Existing admins stay admin and existing view-only users stay 'user'.
  2. Adds three optional columns: phone, job_title, company_ids.

No user, password or invoice is deleted or changed. Safe to run twice.
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

NEW_COLUMNS = (
    ("phone", "VARCHAR(50) NULL"),
    ("job_title", "VARCHAR(100) NULL"),
    ("company_ids", "VARCHAR(255) NULL"),
)
ROLE_SQL = (
    "ALTER TABLE users MODIFY COLUMN role "
    "ENUM('admin','manager','sales','store','accountant','user') NOT NULL DEFAULT 'user'"
)


def main() -> None:
    app = create_app()
    with app.app_context():
        url = db.engine.url
        print("=" * 60)
        print("MIGRATION: user roles + user details")
        print(f"Database host: {url.host}   name: {url.database}")
        print("This changes the 'users' table only. No user is deleted.")
        print("=" * 60)

        insp = inspect(db.engine)
        if "users" not in insp.get_table_names():
            print("ERROR: the 'users' table was not found.")
            sys.exit(1)
        have = {c["name"] for c in insp.get_columns("users")}
        todo = [(n, d) for n, d in NEW_COLUMNS if n not in have]
        print("Will widen the role list: yes")
        print("Will add columns:", ", ".join(n for n, _ in todo) or "(none, already there)")

        if input("\nType YES (capitals) to continue: ").strip() != "YES":
            print("Cancelled. Nothing was changed.")
            return

        with db.engine.begin() as conn:
            conn.execute(text(ROLE_SQL))
            for name, ddl in todo:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
        print("\nDONE. Roles widened; new columns added.")


if __name__ == "__main__":
    main()
