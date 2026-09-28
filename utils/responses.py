"""Consistent JSON response envelope: {data, error, message}.

Every route returns through this, so the frontend never has to guess the
shape of a response — success and failure look identical at the top level,
differing only in which fields are populated.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify


def api_response(data: Any = None, error: str | None = None, message: str | None = None):
    """Build the standard {data, error, message} JSON body.

    Args:
        data: Payload on success. None on error.
        error: Short machine-readable error code (e.g. 'not_found',
            'invalid_type', 'db_error'). None on success.
        message: Human-readable detail, for either success or error.

    Returns:
        A Flask Response wrapping the JSON body. The caller supplies the
        HTTP status code separately, e.g.:
            return api_response(data=obj.to_dict()), 200
            return api_response(error="not_found", message="..."), 404
    """
    return jsonify({"data": data, "error": error, "message": message})
