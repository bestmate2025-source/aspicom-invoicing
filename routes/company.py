"""Routes for /api/company — single-row settings table (id=1), plus the
logo/stamp upload endpoints.

Upsert note: the spec describes this as INSERT ... ON DUPLICATE KEY UPDATE.
What's built here is functionally the same upsert (fetch-or-create, then
update-and-commit) but done through the SQLAlchemy ORM rather than raw SQL —
consistent with the "use SQLAlchemy, not raw mysql-connector" decision from
the schema stage. Flagging the deviation from the literal SQL phrasing
rather than silently picking one: say so if you specifically want the raw
"INSERT ... ON DUPLICATE KEY UPDATE" statement instead (e.g. to avoid the
extra SELECT round-trip) and I'll swap it for a db.session.execute() with
MySQL's ON DUPLICATE KEY UPDATE clause.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Company
from utils.responses import api_response

company_bp = Blueprint("company", __name__)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
EDITABLE_FIELDS = {"name", "address", "trn", "phone", "email", "website", "signee"}


def _get_or_create_company() -> Company:
    """Fetch the single company row (id=1), creating an empty one if missing."""
    company = Company.query.get(1)
    if company is None:
        company = Company(id=1)
        db.session.add(company)
        db.session.commit()
    return company


@company_bp.route("", methods=["GET"])
def get_company():
    """GET /api/company — return the single settings row (id=1)."""
    company = _get_or_create_company()
    return api_response(data=company.to_dict()), 200


@company_bp.route("", methods=["PUT"])
def update_company():
    """PUT /api/company — upsert id=1.

    Body: any subset of {name, address, trn, phone, email, website, signee}.
    `logo_path`/`stamp_path` are deliberately NOT settable here — they are
    only ever written by the /logo and /stamp upload endpoints below, so the
    on-disk file and the DB path can never drift apart.
    """
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields",
            message=f"Unrecognized field(s): {', '.join(sorted(unknown))}. "
                    f"logo_path/stamp_path are set via the upload endpoints, not here.",
        ), 400

    for field in EDITABLE_FIELDS:
        if field in body and body[field] is not None and not isinstance(body[field], str):
            return api_response(error="invalid_type", message=f"'{field}' must be a string."), 400

    company = _get_or_create_company()
    for field in EDITABLE_FIELDS:
        if field in body:
            setattr(company, field, body[field])

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=company.to_dict()), 200


def _save_upload(file_storage, stem: str) -> str:
    """Validate and save an uploaded image, fixed-named by `stem` ('logo' or 'stamp').

    Any existing file for this stem is removed first, regardless of its old
    extension, so there is always exactly ONE logo and ONE stamp file on
    disk — switching from stamp.png to stamp.jpg doesn't leave both behind.

    Args:
        file_storage: werkzeug FileStorage from request.files['file'].
        stem: 'logo' or 'stamp'.

    Returns:
        The relative path (e.g. 'uploads/stamp.png') to store in the DB.

    Raises:
        ValueError: If the file has no name or a disallowed extension.
    """
    filename = file_storage.filename or ""
    if "." not in filename:
        raise ValueError("Uploaded file has no extension.")

    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(
            f"'.{ext}' is not an allowed image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}."
        )

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    for existing in upload_dir.glob(f"{stem}.*"):
        existing.unlink()

    dest = upload_dir / f"{stem}.{ext}"
    file_storage.save(dest)

    return f"{upload_dir.name}/{dest.name}"


def _handle_image_upload(stem: str):
    """Shared body for the /logo and /stamp POST handlers."""
    if "file" not in request.files:
        return api_response(error="missing_file", message="No file field named 'file' in the request."), 400

    file_storage = request.files["file"]
    if file_storage.filename == "":
        return api_response(error="empty_filename", message="No file selected."), 400

    try:
        relative_path = _save_upload(file_storage, stem)
    except ValueError as exc:
        return api_response(error="invalid_file", message=str(exc)), 400

    company = _get_or_create_company()
    setattr(company, f"{stem}_path", relative_path)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=company.to_dict()), 200


@company_bp.route("/logo", methods=["POST"])
def upload_logo():
    """POST /api/company/logo — multipart upload, saves to uploads/logo.<ext>."""
    return _handle_image_upload("logo")


@company_bp.route("/stamp", methods=["POST"])
def upload_stamp():
    """POST /api/company/stamp — multipart upload, saves to uploads/stamp.<ext>."""
    return _handle_image_upload("stamp")
