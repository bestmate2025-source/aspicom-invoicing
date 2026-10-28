"""routes/auth.py
Login / who-am-I / logout.

Registered in app.py with url_prefix="/api/auth":
  POST /api/auth/login    { "username": "...", "password": "..." } -> token + user
  GET  /api/auth/me       needs "Authorization: Bearer <token>"
  POST /api/auth/logout   no-op (the browser just forgets its token)

password_hash is never put in any response.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import User
from utils.auth import generate_token, get_current_user
from utils.responses import api_response

auth_bp = Blueprint("auth", __name__)

_dummy_hash = None


def _burn_hash_time(password: str) -> None:
    """Do a password check against a throw-away hash.

    Used when the username is unknown or the account is switched off, so those
    attempts take about as long as a real wrong password and can't be used to
    discover which usernames exist.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = generate_password_hash("placeholder-not-a-real-password")
    check_password_hash(_dummy_hash, password)


def _invalid_credentials():
    return api_response(error="invalid_credentials", message="Invalid username or password."), 401


@auth_bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_response(error="invalid_json", message="Request body must be a JSON object."), 400

    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username.strip() or not password:
        return api_response(error="bad_request", message="Username and password are required."), 400

    user = User.query.filter_by(username=username.strip()).first()

    if user is None or not user.is_active:
        _burn_hash_time(password)
        return _invalid_credentials()
    if not check_password_hash(user.password_hash, password):
        return _invalid_credentials()

    try:
        token = generate_token(user)
    except RuntimeError as exc:  # JWT_SECRET missing
        current_app.logger.error("Login failed: %s", exc)
        return api_response(
            error="server_error",
            message="Login is not configured on the server (JWT_SECRET is missing).",
        ), 500

    try:
        user.last_login = datetime.utcnow()
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.warning("Could not update last_login for %s: %s", user.username, exc)

    return api_response(data={
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
        },
    }), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    user = get_current_user()
    if user is None:
        return api_response(error="unauthorized", message="Authentication required."), 401
    return api_response(data={
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    return api_response(data={"ok": True}), 200
