"""Routes for /api/companies — list, create and edit companies, plus
per-company logo / stamp uploads.

Registered in app.py with url_prefix="/api/companies".

The older single-company routes in routes/company.py (/api/company) are left
untouched; they keep working on company id=1.

Companies are never deleted — there is deliberately no DELETE endpoint.
"""

from __future__ import annotations

from flask import Blueprint, request
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Company
from routes.company import EDITABLE_FIELDS, _save_upload
from utils.responses import api_response

companies_bp = Blueprint("companies", __name__)


def _not_found():
    return api_response(error="not_found", message="Company not found"), 404


def _check_body(body, is_create: bool):
    """Validate a POST/PUT body the same way routes/company.py does.

    Returns (clean_dict, None) on success or (None, error_response) on failure.
    """
    if body is None or not isinstance(body, dict):
        return None, (api_response(error="invalid_json",
                                   message="Request body must be a JSON object."), 400)

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return None, (api_response(
            error="unknown_fields",
            message=f"Unrecognized field(s): {', '.join(sorted(unknown))}.",
        ), 400)

    for field in EDITABLE_FIELDS:
        if field in body and body[field] is not None and not isinstance(body[field], str):
            return None, (api_response(error="invalid_type",
                                       message=f"'{field}' must be a string."), 400)

    clean = dict(body)
    if clean.get("name") is not None:
        clean["name"] = clean["name"].strip()

    # Name is required when creating; when editing it may be omitted, but not blanked.
    if is_create or "name" in clean:
        if not clean.get("name"):
            return None, (api_response(error="bad_request",
                                       message="Company name is required."), 400)
    return clean, None


def _name_taken(name: str, exclude_id: int | None = None) -> bool:
    """Case-insensitive duplicate-name check."""
    query = Company.query.filter(db.func.lower(Company.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Company.id != exclude_id)
    return query.first() is not None


@companies_bp.route("", methods=["GET"])
def list_companies():
    companies = Company.query.order_by(Company.id).all()
    return api_response(data=[c.to_dict() for c in companies]), 200


@companies_bp.route("/<int:company_id>", methods=["GET"])
def get_company(company_id: int):
    company = Company.query.get(company_id)
    if company is None:
        return _not_found()
    return api_response(data=company.to_dict()), 200


@companies_bp.route("", methods=["POST"])
def create_company():
    clean, error = _check_body(request.get_json(silent=True), is_create=True)
    if error:
        return error

    if _name_taken(clean["name"]):
        return api_response(error="duplicate_name",
                            message="A company with this name already exists."), 400

    company = Company(**{f: clean[f] for f in EDITABLE_FIELDS if f in clean})
    db.session.add(company)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=company.to_dict()), 201


@companies_bp.route("/<int:company_id>", methods=["PUT"])
def update_company(company_id: int):
    company = Company.query.get(company_id)
    if company is None:
        return _not_found()

    clean, error = _check_body(request.get_json(silent=True), is_create=False)
    if error:
        return error

    if "name" in clean and _name_taken(clean["name"], exclude_id=company_id):
        return api_response(error="duplicate_name",
                            message="A company with this name already exists."), 400

    for field in EDITABLE_FIELDS:
        if field in clean:
            setattr(company, field, clean[field])

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=company.to_dict()), 200


def _handle_upload(company_id: int, kind: str):
    """kind is 'logo' or 'stamp'. Saves uploads/company-<id>-<kind>.<ext>."""
    company = Company.query.get(company_id)
    if company is None:
        return _not_found()

    if "file" not in request.files:
        return api_response(error="missing_file",
                            message="No file field named 'file' in the request."), 400

    file_storage = request.files["file"]
    if file_storage.filename == "":
        return api_response(error="empty_filename", message="No file selected."), 400

    try:
        relative_path = _save_upload(file_storage, f"company-{company_id}-{kind}")
    except ValueError as exc:
        return api_response(error="invalid_file", message=str(exc)), 400

    setattr(company, f"{kind}_path", relative_path)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    # Full company row (includes logo_path / stamp_path), same as /api/company/logo.
    return api_response(data=company.to_dict()), 200


@companies_bp.route("/<int:company_id>/logo", methods=["POST"])
def upload_company_logo(company_id: int):
    return _handle_upload(company_id, "logo")


@companies_bp.route("/<int:company_id>/stamp", methods=["POST"])
def upload_company_stamp(company_id: int):
    return _handle_upload(company_id, "stamp")
