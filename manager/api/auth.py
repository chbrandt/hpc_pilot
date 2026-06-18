"""
api/auth.py — Bearer-token authentication helpers for the REST API layer.

Extracts the EGI Check-in access token from the ``Authorization: Bearer``
HTTP header, validates it via ``lib.token_auth``, and provides a route
decorator for protecting API endpoints.

Usage in routes::

    from api.auth import require_token, get_request_claims

    @bp.route("/example")
    @require_token
    def example():
        claims = get_request_claims()
        namespace = claims["namespace"]
        ...
"""

import json
import logging
import time
from functools import wraps
from typing import Optional

from flask import g, request

from api.site_config import load_site_config
from lib.token_auth import check_group_access, derive_namespace, validate_token

logger = logging.getLogger(__name__)


def _json_error(message: str, code: int):
    """Return a plain JSON error tuple suitable for Flask route returns."""
    body = json.dumps({"error": message, "code": code})
    return body, code, {"Content-Type": "application/json"}


def extract_bearer_token() -> Optional[str]:
    """
    Parse the ``Authorization`` header and return the raw Bearer token string.

    Returns ``None`` if the header is absent or not a Bearer token.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None


def get_request_claims() -> Optional[dict]:
    """
    Return the validated JWT claims stored in Flask's ``g`` for this request.

    Returns ``None`` if ``require_token`` was not applied (should not happen
    in protected routes).
    """
    return getattr(g, "_api_claims", None)


def require_token(f):
    """
    Flask route decorator that enforces Bearer-token authentication.

    On success, the verified claims dict (plus a ``"namespace"`` key derived
    from the ``sub`` claim) is stored in ``flask.g._api_claims`` and can be
    retrieved with :func:`get_request_claims`.

    On failure, returns a JSON 401 response.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        token = extract_bearer_token()
        if not token:
            return _json_error(
                "Missing or malformed Authorization header. "
                "Expected: Authorization: Bearer <token>",
                401,
            )

        try:
            claims = validate_token(token)
        except ValueError as exc:
            logger.warning("API token validation failed: %s", exc)
            return _json_error(f"Token validation failed: {exc}", 401)

        # Check expiry (validate_token already does this, but be explicit)
        if time.time() > claims.get("exp", 0):
            return _json_error("Token has expired.", 401)

        # Group-access check (no-op when allowed_groups is empty)
        allowed_groups = load_site_config().get("allowed_groups") or []
        try:
            check_group_access(claims, allowed_groups)
        except ValueError as exc:
            logger.warning("API group access denied: %s", exc)
            return _json_error(str(exc), 403)

        # Attach enriched claims to the request context
        claims["namespace"] = derive_namespace(claims["sub"])
        claims["_token"] = token  # some endpoints (hpc) need the raw token
        g._api_claims = claims

        return f(*args, **kwargs)

    return decorated
