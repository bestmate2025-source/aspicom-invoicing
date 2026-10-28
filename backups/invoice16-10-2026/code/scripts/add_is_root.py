"""scripts/add_is_root.py
One-time migration for user management.

  1. Adds the column users.is_root (only if it is missing).
  2. Marks the user 'farhan' as the root user.

Safe to run more than once. It uses the same DB_* settings as the app (.env),
shows which database it is about to change, and waits for you to type YES.

Run it from the project folder:   python scripts\add_is_root.py
"""

import os
import sys

import pymysql
from dotenv import load_dotenv

ROOT_USERNAME = "farhan"

load_dotenv()

host = os.environ.get("DB_HOST", "localhost")
port = int(os.environ.get("DB_PORT", "3306"))
name = os.environ["DB_NAME"]

print(f"This will change the database '{name}' on {host}:{port}.")
print("  - add users.is_root if missing")
print(f"  - set is_root = 1 for user '{ROOT_USERNAME}'")
if input("Type YES to continue: ").strip() != "YES":
    print("Cancelled. Nothing was changed.")
    sys.exit(0)

conn = pymysql.connect(
    host=host, port=port, user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"], database=name,
)
try:
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM users LIKE 'is_root'")
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE users ADD COLUMN is_root TINYINT(1) NOT NULL DEFAULT 0")
            print("Added column users.is_root.")
        else:
            print("Column users.is_root already exists - skipped.")

        cur.execute("UPDATE users SET is_root = 1 WHERE username = %s", (ROOT_USERNAME,))
        if cur.rowcount == 0:
            cur.execute("SELECT is_root FROM users WHERE username = %s", (ROOT_USERNAME,))
            if cur.fetchone() is None:
                print(f"WARNING: no user named '{ROOT_USERNAME}' was found, so nobody is root yet.")
            else:
                print(f"'{ROOT_USERNAME}' was already root.")
        else:
            print(f"'{ROOT_USERNAME}' is now the root user.")
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT id, username, role, is_root, is_active FROM users ORDER BY id")
        print("\nUsers now:")
        for row in cur.fetchall():
            print(f"  id={row[0]}  {row[1]:<15} role={row[2]:<6} is_root={row[3]}  is_active={row[4]}")
finally:
    conn.close()
print("\nDone.")
