"""Routes for /api/company — single-row settings table (id=1), plus logo/stamp uploads."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Company
from utils.responses import api_response

company_bp = Blueprint("company", __name__)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
EDITABLE_FIELDS = {"name", "address", "po_box", "trn", "phone", "email", "website", "signee"}


def _get_or_create_company() -> Company:
    company = Company.query.get(1)
    if company is None:
        company = Company(id=1)
        db.session.add(company)
        db.session.commit()
    return company


@company_bp.route("", methods=["GET"])
def get_company():
    company = _get_or_create_company()
    return api_response(data=company.to_dict()), 200


@company_bp.route("", methods=["PUT"])
def update_company():
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields",
            message=f"Unrecognized field(s): {', '.join(sorted(unknown))}.",
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
    return _handle_image_upload("logo")


@company_bp.route("/stamp", methods=["POST"])
def upload_stamp():
    return _handle_image_upload("stamp")