"""utils/auth.py
Authentication helpers: JWT creation / checking and the route decorators.

The server keeps no session store. A token is a signed JWT; logging out
simply means the browser throws its copy away.

Environment variables (read when used, so .env is already loaded):
  AUTH_ENABLED      "true" turns enforcement on. Anything else = off (dev mode).
  JWT_SECRET        long random string used to sign tokens (required for login).
  JWT_EXPIRES_DAYS  optional, token lifetime in days (default 30).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, request

from models import User
from utils.responses import api_response

JWT_ALGORITHM = "HS256"


def auth_enabled() -> bool:
    """True only when AUTH_ENABLED=true in the environment."""
    return os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET is not set.")
    return secret


def generate_token(user) -> str:
    """Create a signed JWT for `user` (valid for JWT_EXPIRES_DAYS, default 30)."""
    days = int(os.environ.get("JWT_EXPIRES_DAYS", "30"))
    payload = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(days=days),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Return the token's payload if the signature and expiry are good, else None."""
    try:
        return jwt.decode(
            token, _secret(), algorithms=[JWT_ALGORITHM], options={"require": ["exp"]}
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _token_from_request() -> str | None:
    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def get_current_user():
    """The logged-in User for this request, or None.

    None when the token is missing / invalid / expired, when the user no longer
    exists, or when the account has been deactivated (so switching a user off
    also kills their existing tokens). Role is read from the database, not the
    token, so a role change takes effect immediately.
    """
    token = _token_from_request()
    if not token:
        return None
    payload = verify_token(token)
    if payload is None:
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = User.query.get(user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_auth(fn):
    """Route decorator: needs a valid token. Does nothing when AUTH_ENABLED is off.

    Afterwards g.current_user is the User (or None in dev mode, when auth is off).
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not auth_enabled():
            g.current_user = None  # dev mode: nobody is logged in, but the attribute exists
            return fn(*args, **kwargs)
        user = get_current_user()
        if user is None:
            return api_response(error="unauthorized", message="Authentication required."), 401
        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """Route decorator: needs a valid token AND role 'admin'."""

    @require_auth
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if auth_enabled():
            user = getattr(g, "current_user", None)
            if user is None or user.role != "admin":
                return api_response(error="forbidden", message="Admin access required."), 403
        return fn(*args, **kwargs)

    return wrapper
