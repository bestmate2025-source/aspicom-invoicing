"""Shared pytest fixtures — runs against a REAL MySQL test database.

This deliberately does NOT use SQLite: it connects to an actual MySQL
database reserved for testing, so schema.sql's real ENGINE=InnoDB, foreign
key, and DECIMAL behavior is exercised exactly, not approximated.

Safety is enforced in code, not just by convention — _assert_safe_test_db()
below is a hard stop, not a warning, because this fixture calls
db.drop_all() / db.create_all(). A misconfigured TEST_DB_NAME pointing at
the real database would be destructive.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

# Load .env explicitly here (not just relying on app.py's own load_dotenv())
# because we need the real DB_USER/DB_PASSWORD as potential fallback values
# BEFORE we override them with placeholders below.
load_dotenv()

_dev_db_user = os.environ.get("DB_USER")
_dev_db_password = os.environ.get("DB_PASSWORD")
_dev_db_name = os.environ.get("DB_NAME")

# Placeholders so app.py's os.environ["DB_USER"/"DB_PASSWORD"/"DB_NAME"]
# lookups don't KeyError during create_app(). Immediately irrelevant once we
# override SQLALCHEMY_DATABASE_URI with the real TEST database URI below —
# these values are never actually connected to.
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")
os.environ.setdefault("DB_NAME", "unused")

from app import create_app  # noqa: E402
from extensions import db as _db  # noqa: E402

# --- Resolve the TEST database connection ---
# TEST_DB_NAME: env var, defaults to 'aspicom_invoicing_test'.
# TEST_DB_USER / TEST_DB_PASSWORD: env var, falling back to the dev DB_USER
# / DB_PASSWORD if not separately set (per spec).
# TEST_DB_HOST / TEST_DB_PORT: not explicitly in the spec, but a real
# connection needs them — same fallback pattern, then a sane default.
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "aspicom_invoicing_test")
TEST_DB_USER = os.environ.get("TEST_DB_USER") or _dev_db_user or "root"
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD") or _dev_db_password or ""
TEST_DB_HOST = os.environ.get("TEST_DB_HOST") or os.environ.get("DB_HOST") or "localhost"
TEST_DB_PORT = os.environ.get("TEST_DB_PORT") or os.environ.get("DB_PORT") or "3306"


def _assert_safe_test_db(test_db_name: str, dev_db_name: str | None) -> None:
    """Refuse to run if the resolved test database could plausibly be the real one.

    Raises:
        RuntimeError: If test_db_name matches the dev DB_NAME exactly, or
            doesn't contain 'test' at all.
    """
    if dev_db_name and test_db_name == dev_db_name:
        raise RuntimeError(
            f"TEST_DB_NAME ('{test_db_name}') is identical to your dev DB_NAME. "
            "Refusing to run — this would drop/create tables on your real database."
        )
    if "test" not in test_db_name.lower():
        raise RuntimeError(
            f"Refusing to run tests against database '{test_db_name}' — its name "
            "doesn't contain 'test'. Set TEST_DB_NAME to something clearly reserved "
            "for testing."
        )


_assert_safe_test_db(TEST_DB_NAME, _dev_db_name)

TEST_DATABASE_URI = (
    f"mysql+pymysql://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
)

# Deleted in child-before-parent order between tests, respecting the FK
# graph in schema.sql (invoice_items -> invoices -> clients).
_TABLES_IN_DELETE_ORDER = (
    "invoice_audit", "import_log", "invoice_items", "invoices",
    "parsed_invoices", "inventory", "clients", "company",
)


@pytest.fixture(scope="session")
def _app_with_schema():
    """Build the Flask app once per test session, schema created fresh
    against the TEST database (never the dev one — see _assert_safe_test_db).
    """
    flask_app = create_app()
    flask_app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=TEST_DATABASE_URI)

    ctx = flask_app.app_context()
    ctx.push()
    _db.drop_all()   # clean slate in case a prior run left tables behind
    _db.create_all()
    yield flask_app
    _db.drop_all()
    ctx.pop()


@pytest.fixture()
def app(_app_with_schema):
    """Per-test: delete every table's rows after the test runs.

    Faster than drop_all()/create_all() per test against a real MySQL
    server, while still giving each test a clean slate — no leftover rows
    from the previous test.
    """
    yield _app_with_schema
    _db.session.rollback()
    for table_name in _TABLES_IN_DELETE_ORDER:
        _db.session.execute(text(f"DELETE FROM {table_name}"))
    _db.session.commit()


@pytest.fixture()
def client(app):
    """Flask test client bound to the fixture app."""
    return app.test_client()


@pytest.fixture()
def db(app):
    """Direct SQLAlchemy access, for tests that need to write through a
    model with no route yet (e.g. inserting an Invoice directly, ahead of
    routes/invoices.py landing, for the FK-conflict test in test_clients.py)."""
    return _db
