"""routes/users.py
User management (admin only): list, create, edit, reset password,
deactivate / reactivate, delete.

Registered in app.py with url_prefix="/api/users".

Rules
  - Only a logged-in administrator may use any of these endpoints (read-only
    users get 403 even for GET). Unlike the @require_admin decorator, this file
    ALWAYS asks for a valid admin token, even when AUTH_ENABLED=false, so a
    mis-set flag can never leave "create an admin" open to the world.
  - The root user (users.is_root = 1) cannot be deleted, demoted or deactivated.
    Only root itself can edit root or reset root's password.
  - Nobody can deactivate or delete their own account.
  - The last active administrator can never be demoted, deactivated or deleted.
  - Deleting a user only removes the login; invoices and other data stay.
  - password_hash is never returned. Generated passwords are returned once.
"""

from __future__ import annotations

import re
import secrets
from functools import wraps

from flask import Blueprint, g, request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from extensions import db
from models import User
from utils.auth import get_current_user
from utils.responses import api_response

users_bp = Blueprint("users", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = ("admin", "user")
MIN_PASSWORD_LENGTH = 6
# 12-character passwords made of easy-to-read characters (no 0/O, 1/l/I).
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def _err(status: int, error: str, message: str):
    return api_response(error=error, message=message), status


def admin_only(fn):
    """Valid admin token required (checked even when AUTH_ENABLED=false)."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        actor = get_current_user()
        if actor is None:
            return _err(401, "unauthorized", "Authentication required.")
        if actor.role != "admin":
            return _err(403, "forbidden", "Admin access required.")
        g.current_user = actor
        return fn(*args, **kwargs)

    return wrapper


def _generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(12))


def _find_user(user_id: int):
    return User.query.get(user_id)


def _not_found():
    return _err(404, "not_found", "User not found")


def _other_active_admin_exists(target) -> bool:
    return any(
        u.id != target.id and u.role == "admin" and u.is_active for u in User.query.all()
    )


def _username_taken(username: str) -> bool:
    return any((u.username or "").lower() == username.lower() for u in User.query.all())


def _commit():
    """Commit; returns an error response tuple on failure, else None."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _err(409, "conflict", "That username is already in use.")
    except SQLAlchemyError as exc:
        db.session.rollback()
        return _err(500, "db_error", str(exc))
    return None


def _json_object():
    """(body, error). An empty body is treated as {}."""
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        return {}, None
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _err(400, "invalid_json", "Request body must be a JSON object.")
    return body, None


def _check_fields(body: dict, allowed: set):
    unknown = set(body) - allowed
    if unknown:
        return _err(400, "unknown_fields", f"Unrecognized field(s): {', '.join(sorted(unknown))}.")
    return None


def _clean_full_name(value):
    if not isinstance(value, str) or not value.strip():
        return None, _err(400, "bad_request", "Full name is required.")
    value = value.strip()
    if len(value) > 100:
        return None, _err(400, "bad_request", "Full name must be 100 characters or fewer.")
    return value, None


def _clean_email(value):
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _err(400, "invalid_type", "'email' must be a string.")
    value = value.strip()
    if value == "":
        return None, None
    if len(value) > 255 or not EMAIL_RE.match(value):
        return None, _err(400, "bad_request", "Email address is not valid.")
    return value, None


def _clean_role(value):
    if not isinstance(value, str) or value not in ROLES:
        return None, _err(400, "bad_request", "Role must be 'admin' or 'user'.")
    return value, None


# ---------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------
@users_bp.route("", methods=["GET"])
@admin_only
def list_users():
    users = User.query.order_by(User.id).all()
    return api_response(data=[u.to_dict() for u in users]), 200


@users_bp.route("", methods=["POST"])
@admin_only
def create_user():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "invalid_json", "Request body must be a JSON object.")
    error = _check_fields(body, {"username", "full_name", "email", "role"})
    if error:
        return error

    username = body.get("username")
    if not isinstance(username, str) or not USERNAME_RE.match(username.strip()):
        return _err(400, "bad_request",
                    "Username must be 3-50 characters: letters, numbers and underscore only.")
    username = username.strip()

    full_name, error = _clean_full_name(body.get("full_name"))
    if error:
        return error
    email, error = _clean_email(body.get("email"))
    if error:
        return error
    role, error = _clean_role(body.get("role", "user"))
    if error:
        return error

    if _username_taken(username):
        return _err(409, "conflict", "That username is already in use.")

    password = _generate_password()
    user = User(
        username=username, password_hash=generate_password_hash(password),
        full_name=full_name, email=email, role=role, is_root=False, is_active=True,
    )
    db.session.add(user)
    error = _commit()
    if error:
        return error
    return api_response(data={"user": user.to_dict(), "password": password}), 201


@users_bp.route("/<int:user_id>", methods=["PUT"])
@admin_only
def update_user(user_id: int):
    actor = g.current_user
    target = _find_user(user_id)
    if target is None:
        return _not_found()
    if target.is_root and actor.id != target.id:
        return _err(403, "forbidden", "Only the root user can edit the root account.")

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "invalid_json", "Request body must be a JSON object.")
    error = _check_fields(body, {"username", "full_name", "email", "role"})
    if error:
        return error
    if "username" in body and str(body["username"]).strip().lower() != target.username.lower():
        return _err(400, "bad_request", "Username cannot be changed.")

    changes = {}
    if "full_name" in body:
        changes["full_name"], error = _clean_full_name(body["full_name"])
        if error:
            return error
    if "email" in body:
        changes["email"], error = _clean_email(body["email"])
        if error:
            return error
    if "role" in body:
        new_role, error = _clean_role(body["role"])
        if error:
            return error
        if new_role != target.role:
            if target.is_root:
                return _err(403, "forbidden", "The root user must stay an administrator.")
            if target.role == "admin" and not _other_active_admin_exists(target):
                return _err(400, "bad_request", "You cannot demote the last active administrator.")
            changes["role"] = new_role

    for field, value in changes.items():
        setattr(target, field, value)
    error = _commit()
    if error:
        return error
    return api_response(data=target.to_dict()), 200


@users_bp.route("/<int:user_id>/password", methods=["PUT"])
@admin_only
def reset_password(user_id: int):
    actor = g.current_user
    target = _find_user(user_id)
    if target is None:
        return _not_found()
    if target.is_root and actor.id != target.id:
        return _err(403, "forbidden", "Only the root user can reset the root password.")

    body, error = _json_object()
    if error:
        return error
    error = _check_fields(body, {"new_password"})
    if error:
        return error

    if body.get("new_password") is not None:
        password = body["new_password"]
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH or len(password) > 128:
            return _err(400, "bad_request",
                        f"Password must be {MIN_PASSWORD_LENGTH}-128 characters long.")
    else:
        password = _generate_password()

    target.password_hash = generate_password_hash(password)
    error = _commit()
    if error:
        return error
    return api_response(data={"password": password}), 200


@users_bp.route("/<int:user_id>/deactivate", methods=["PUT"])
@admin_only
def deactivate_user(user_id: int):
    actor = g.current_user
    target = _find_user(user_id)
    if target is None:
        return _not_found()
    if target.is_root:
        return _err(403, "forbidden", "The root user cannot be deactivated.")
    if target.id == actor.id:
        return _err(400, "bad_request", "You cannot deactivate your own account.")
    if target.role == "admin" and target.is_active and not _other_active_admin_exists(target):
        return _err(400, "bad_request", "You cannot deactivate the last active administrator.")

    target.is_active = False
    error = _commit()
    if error:
        return error
    return api_response(data=target.to_dict()), 200


@users_bp.route("/<int:user_id>/activate", methods=["PUT"])
@admin_only
def activate_user(user_id: int):
    target = _find_user(user_id)
    if target is None:
        return _not_found()
    target.is_active = True
    error = _commit()
    if error:
        return error
    return api_response(data=target.to_dict()), 200


@users_bp.route("/<int:user_id>", methods=["DELETE"])
@admin_only
def delete_user(user_id: int):
    actor = g.current_user
    target = _find_user(user_id)
    if target is None:
        return _not_found()
    if target.is_root:
        return _err(403, "forbidden", "The root user cannot be deleted.")
    if target.id == actor.id:
        return _err(400, "bad_request", "You cannot delete your own account.")
    if target.role == "admin" and target.is_active and not _other_active_admin_exists(target):
        return _err(400, "bad_request", "You cannot delete the last active administrator.")

    deleted_id = target.id
    db.session.delete(target)
    error = _commit()
    if error:
        return error
    return api_response(data={"id": deleted_id, "deleted": True}), 200
