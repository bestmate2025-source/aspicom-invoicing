"""Seed the `company` table (id=1) with Aspicom LLC's details.

Run from anywhere:
    python scripts/seed_company.py

Idempotent: safe to run repeatedly. It upserts the single company row (id=1)
and only touches uploads/ when the matching source asset exists.

Two behaviors worth knowing before you re-run it:
  * Every run resets the seven text fields below to the values in
    COMPANY_DEFAULTS. Anything edited later through PUT /api/company will be
    overwritten. That is what "upsert with these values" means, but it is
    easy to forget.
  * If assets/stamp.png or assets/logo.png is missing, the matching
    company.stamp_path / company.logo_path is left exactly as it was
    (never nulled out), and any existing file in uploads/ is not deleted.

Credentials come from .env (DB_USER, DB_PASSWORD, DB_NAME required;
DB_HOST, DB_PORT optional). The upload folder honours UPLOAD_FOLDER (default
"uploads"), so files land where the running app looks for them.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# `python scripts/seed_company.py` puts scripts/ on sys.path, not the project
# root, so `from extensions import db` would fail without this.
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from flask import Flask  # noqa: E402

from extensions import db  # noqa: E402
from models import Company  # noqa: E402

ASSETS_DIR = PROJECT_ROOT / "assets"

COMPANY_DEFAULTS: dict[str, str] = {
    "name": "Aspicom LLC",
    "address": "Office # 10, Level 1, Sharjah Media City, Sharjah, UAE",
    "trn": "100441082300003",
    "phone": "+971 527600033",
    "email": "Info@aspicomllc.com",
    "website": "www.aspicomllc.com",
    "signee": "Accounts",
}


def _build_app() -> Flask:
    """Build a minimal Flask app, just enough to give db.session a context.

    Deliberately NOT app.create_app(): this script has no use for the
    blueprints, CORS, or the PDF routes' config loading.

    Returns:
        A Flask app bound to the MySQL database named in .env.

    Raises:
        RuntimeError: If a required DB_* variable is missing from the environment.
    """
    missing = [key for key in ("DB_USER", "DB_PASSWORD", "DB_NAME") if key not in os.environ]
    if missing:
        raise RuntimeError(f"Missing required variable(s) in .env: {', '.join(missing)}")

    user = os.environ["DB_USER"]
    password = os.environ["DB_PASSWORD"]
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ["DB_NAME"]

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def _copy_asset(source: Path, upload_dir: Path, stem: str) -> str | None:
    """Copy `source` to upload_dir/<stem><ext>, replacing any prior <stem>.* file.

    Mirrors routes/company.py's upload rule (exactly one logo file and one
    stamp file on disk), so a stale stamp.jpg from an earlier UI upload does
    not linger next to the freshly seeded stamp.png.

    Args:
        source: Path to the source asset (e.g. assets/stamp.png).
        upload_dir: Destination folder; created if missing.
        stem: 'stamp' or 'logo'.

    Returns:
        The relative path to store in the DB (e.g. 'uploads/stamp.png'), or
        None if `source` does not exist (nothing is touched in that case).
    """
    if not source.is_file():
        return None

    upload_dir.mkdir(parents=True, exist_ok=True)
    for existing in upload_dir.glob(f"{stem}.*"):
        existing.unlink()

    dest = upload_dir / f"{stem}{source.suffix.lower()}"
    shutil.copyfile(source, dest)
    return f"{upload_dir.name}/{dest.name}"


def main() -> int:
    """Seed company id=1 and copy the stamp/logo assets.

    Returns:
        Process exit code: 0 on success, 1 on any failure.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        app = _build_app()
    except RuntimeError as exc:
        print(f"Cannot seed: {exc}", file=sys.stderr)
        return 1

    upload_dir = Path(os.environ.get("UPLOAD_FOLDER", "uploads"))
    if not upload_dir.is_absolute():
        upload_dir = PROJECT_ROOT / upload_dir

    stamp_path: str | None = None
    logo_path: str | None = None

    with app.app_context():
        company = db.session.get(Company, 1)
        if company is None:
            company = Company(id=1)
            db.session.add(company)

        for field, value in COMPANY_DEFAULTS.items():
            setattr(company, field, value)

        try:
            stamp_path = _copy_asset(ASSETS_DIR / "stamp.png", upload_dir, "stamp")
            logo_path = _copy_asset(ASSETS_DIR / "logo.png", upload_dir, "logo")
            if stamp_path:
                company.stamp_path = stamp_path
            if logo_path:
                company.logo_path = logo_path
            db.session.commit()  # single commit for the whole seed
        except Exception as exc:  # noqa: BLE001 - one-shot script: report any failure, roll back, exit 1
            db.session.rollback()
            print(f"Seeding failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    print(f"Company seeded: {COMPANY_DEFAULTS['name']}")
    print(f"Stamp copied: {stamp_path}" if stamp_path else "Stamp source not found, skipping")
    print(f"Logo copied: {logo_path}" if logo_path else "Logo source not found, skipping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
