"""Routes for /api/clients — full CRUD.

Every return goes through api_response() — including list_clients, which is
the bug this revision fixes (it previously returned a bare JSON array
instead of the {data, error, message} envelope).
"""

from __future__ import annotations

from flask import Blueprint, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import Client
from utils.responses import api_response

clients_bp = Blueprint("clients", __name__)

EDITABLE_FIELDS = {"name", "address", "trn", "email"}


@clients_bp.route("", methods=["GET"])
def list_clients():
    """GET /api/clients — return every client, newest first."""
    clients = Client.query.order_by(Client.created_at.desc()).all()
    return api_response(data=[c.to_dict() for c in clients]), 200


@clients_bp.route("", methods=["POST"])
def create_client():
    """POST /api/clients — create a client. `name` required; others optional."""
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return api_response(
            error="missing_field", message="'name' is required and must be a non-empty string."
        ), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    for field in ("address", "trn", "email"):
        if field in body and body[field] is not None and not isinstance(body[field], str):
            return api_response(error="invalid_type", message=f"'{field}' must be a string."), 400

    client_obj = Client(
        name=name.strip(),
        address=body.get("address"),
        trn=body.get("trn"),
        email=body.get("email"),
    )
    db.session.add(client_obj)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=client_obj.to_dict()), 201


@clients_bp.route("/<int:client_id>", methods=["GET"])
def get_client(client_id: int):
    """GET /api/clients/<id> — fetch one client, 404 if missing."""
    client_obj = Client.query.get(client_id)
    if client_obj is None:
        return api_response(error="not_found", message=f"No client with id {client_id}."), 404
    return api_response(data=client_obj.to_dict()), 200


@clients_bp.route("/<int:client_id>", methods=["PUT"])
def update_client(client_id: int):
    """PUT /api/clients/<id> — update a client. Whitelist: name, address, trn, email."""
    client_obj = Client.query.get(client_id)
    if client_obj is None:
        return api_response(error="not_found", message=f"No client with id {client_id}."), 404

    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    unknown = set(body.keys()) - EDITABLE_FIELDS
    if unknown:
        return api_response(
            error="unknown_fields", message=f"Unrecognized field(s): {', '.join(sorted(unknown))}."
        ), 400

    if "name" in body and (not isinstance(body["name"], str) or not body["name"].strip()):
        return api_response(error="invalid_type", message="'name' must be a non-empty string."), 400

    for field in ("address", "trn", "email"):
        if field in body and body[field] is not None and not isinstance(body[field], str):
            return api_response(error="invalid_type", message=f"'{field}' must be a string."), 400

    for field in EDITABLE_FIELDS:
        if field in body:
            value = body[field].strip() if field == "name" else body[field]
            setattr(client_obj, field, value)

    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data=client_obj.to_dict()), 200


@clients_bp.route("/<int:client_id>", methods=["DELETE"])
def delete_client(client_id: int):
    """DELETE /api/clients/<id> — delete a client.

    Blocked (409) if the client still has invoices: invoices.client_id has
    ON DELETE RESTRICT, so the DB itself refuses the delete. We catch that
    IntegrityError specifically (before the broader SQLAlchemyError) and
    turn it into a helpful 409 instead of a raw 500.
    """
    client_obj = Client.query.get(client_id)
    if client_obj is None:
        return api_response(error="not_found", message=f"No client with id {client_id}."), 404

    db.session.delete(client_obj)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return api_response(
            error="fk_conflict",
            message="This client has existing invoices and can't be deleted. "
                    "Reassign or delete those invoices first.",
        ), 409
    except SQLAlchemyError as exc:
        db.session.rollback()
        return api_response(error="db_error", message=str(exc)), 500

    return api_response(data={"id": client_id, "deleted": True}), 200
