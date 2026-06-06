"""
api/checkin.py — EGI Check-in OIDC authentication endpoint.

Provides a single entry-point (``GET /api/auth/checkin``) that detects whether
the caller is a browser or a terminal/curl client and branches into the
appropriate OAuth 2.0 flow:

Browser (Accept: text/html)
    Authorization Code + PKCE flow.  The user is redirected to EGI Check-in;
    on approval the IdP redirects back to ``GET /api/auth/checkin/callback``
    which exchanges the code for tokens and stores them in the Flask session.

Terminal / curl (no text/html Accept)
    Device Authorization Grant (RFC 8628).  The endpoint immediately returns a
    JSON payload with ``user_code``, ``verification_uri``, and ``device_code``
    so the operator can authorise in a browser while the client polls
    ``POST /api/auth/checkin/device/poll`` for the final tokens.

Endpoints
---------
GET  /api/auth/checkin
    Detect client type and initiate the appropriate flow.

GET  /api/auth/checkin/callback
    Browser-only: receive the authorization code from EGI Check-in,
    validate the ``state`` parameter (CSRF), exchange the code for tokens,
    and store them in the Flask session.

POST /api/auth/checkin/device/poll
    Terminal-only: single-step poll of the EGI token endpoint using a
    ``device_code`` obtained from ``GET /api/auth/checkin``.
    Body: ``{"device_code": "<code>", "client_id": "<id>"}``

Configuration
-------------
All OIDC settings are read from ``site_config.yaml`` (``oidc`` section).
Environment variables take precedence over file settings for runtime overrides:

+-------------------------+------------------+----------------------------------------------------+
| site_config.yaml key    | Env-var override | Description                                        |
+=========================+==================+====================================================+
| oidc.issuer             | CHECKIN_ISSUER   | OIDC realm base URL (discovery fetched from it)    |
| oidc.client_id          | CHECKIN_CLIENT_ID| OIDC public client identifier                      |
| oidc.scope              | CHECKIN_SCOPE    | Space-separated scopes                             |
| oidc.redirect_uri       | CHECKIN_REDIRECT_URI | Callback URL for Authorization Code flow       |
+-------------------------+------------------+----------------------------------------------------+

See ``site_config.yaml`` for full documentation of each key.
"""

import json
import logging
import secrets

import requests as _requests

from flask import Blueprint, redirect, request, session, url_for

from api.site_config import get_oidc_config
from lib.token_checkin import (
    _get_token_endpoint,
    build_auth_code_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    start_device_flow,
)

logger = logging.getLogger(__name__)

checkin_bp = Blueprint("api_checkin", __name__, url_prefix="/api/auth")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _json(data: dict, code: int = 200):
    """Return a plain JSON Flask response tuple."""
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _is_browser() -> bool:
    """
    Return ``True`` when the caller appears to be a web browser.

    Heuristic: the ``Accept`` header contains ``text/html``.  curl and other
    HTTP clients either omit ``Accept`` or send ``*/*`` without ``text/html``.
    """
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


# ── Routes ────────────────────────────────────────────────────────────────────


@checkin_bp.route("/checkin", methods=["GET"])
def checkin():
    """
    EGI Check-in OIDC entry-point.

    Branches on client type:

    * **Browser** → Authorization Code + PKCE: generates a PKCE pair and a
      random ``state`` token, stores them in the Flask session for later
      validation, and redirects the user to the EGI Check-in authorization
      endpoint.

    * **Terminal / curl** → Device Authorization Grant: starts a device flow
      with EGI Check-in and returns the JSON payload (``user_code``,
      ``verification_uri``, ``verification_uri_complete``, ``device_code``,
      ``interval``, ``expires_in``) so the operator can authorize in a browser.
      Use ``POST /api/auth/checkin/device/poll`` to poll for completion.
    """
    if _is_browser():
        return _handle_browser_auth_code()
    return _handle_terminal_device_flow()


def _handle_browser_auth_code():
    """Initiate Authorization Code + PKCE; redirect the browser to EGI Check-in."""
    oidc = get_oidc_config()

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    # Persist PKCE verifier and state in the session for the callback.
    session["_checkin_state"] = state
    session["_checkin_verifier"] = code_verifier

    auth_url = build_auth_code_url(
        client_id=oidc["client_id"],
        redirect_uri=oidc["redirect_uri"],
        scope=oidc["scope"],
        state=state,
        code_challenge=code_challenge,
        issuer=oidc["issuer"],
    )
    logger.debug("Redirecting browser to auth URL: %s", auth_url)
    return redirect(auth_url)


def _handle_terminal_device_flow():
    """Start device flow and return the device grant JSON to the curl client."""
    oidc = get_oidc_config()

    try:
        resp = start_device_flow(
            client_id=oidc["client_id"],
            scope=oidc["scope"],
            issuer=oidc["issuer"],
        )
    except Exception as exc:
        logger.error("Device flow start failed: %s", exc)
        return _json({"error": f"Failed to start device flow: {exc}"}, 502)

    payload = {
        "flow": "device",
        "client_id": oidc["client_id"],
        "device_code": resp.get("device_code"),
        "user_code": resp.get("user_code"),
        "verification_uri": resp.get("verification_uri"),
        "verification_uri_complete": resp.get("verification_uri_complete"),
        "interval": resp.get("interval", 5),
        "expires_in": resp.get("expires_in"),
        "poll_url": "/api/auth/checkin/device/poll",
        "instructions": (
            "Open the verification_uri in a browser, enter the user_code, "
            "then POST {\"device_code\": \"...\", \"client_id\": \"...\"} "
            "to poll_url to retrieve your tokens."
        ),
    }
    logger.info(
        "Device flow started: user_code=%s verification_uri=%s",
        payload["user_code"],
        payload["verification_uri"],
    )
    return _json(payload, 200)


@checkin_bp.route("/checkin/callback", methods=["GET"])
def checkin_callback():
    """
    Authorization Code callback — browser only.

    EGI Check-in redirects back here after the user approves the request.
    This handler:

    1. Validates the ``state`` parameter against the one stored in the session
       (CSRF protection).
    2. Exchanges the ``code`` for tokens via the token endpoint (PKCE).
    3. Stores the access token and claims in the Flask session (compatible with
       the existing ``app/auth.py`` session format).
    4. Redirects the user to the application home page.

    On error, returns a 400 or 502 JSON response.
    """
    error = request.args.get("error")
    if error:
        error_description = request.args.get("error_description", "")
        logger.warning("Auth code callback received error: %s — %s", error, error_description)
        return _json(
            {"error": error, "error_description": error_description},
            400,
        )

    code = request.args.get("code", "").strip()
    returned_state = request.args.get("state", "")

    if not code:
        return _json({"error": "Missing 'code' parameter in callback."}, 400)

    # ── CSRF check ────────────────────────────────────────────────────────────
    expected_state = session.pop("_checkin_state", None)
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
        logger.warning("State mismatch in auth code callback (possible CSRF).")
        return _json({"error": "State mismatch. Possible CSRF attack."}, 400)

    code_verifier = session.pop("_checkin_verifier", None)
    if not code_verifier:
        return _json({"error": "Missing PKCE verifier in session."}, 400)

    oidc = get_oidc_config()

    # ── Exchange code for tokens ──────────────────────────────────────────────
    try:
        tokens = exchange_code_for_tokens(
            code=code,
            client_id=oidc["client_id"],
            redirect_uri=oidc["redirect_uri"],
            code_verifier=code_verifier,
            issuer=oidc["issuer"],
        )
    except RuntimeError as exc:
        logger.error("Token exchange failed: %s", exc)
        return _json({"error": f"Token exchange failed: {exc}"}, 502)

    # ── Store tokens in the session (mirrors app/auth.py format) ─────────────
    _store_tokens_in_session(tokens)

    logger.info("Auth code flow completed; tokens stored in session.")

    # Redirect to application home (the GUI entry point).
    return redirect(url_for("app_k8s.index"))


@checkin_bp.route("/checkin/device/poll", methods=["POST"])
def device_poll():
    """
    Single-step device flow poll — terminal / curl clients.

    Accepts a JSON body with ``device_code`` and (optionally) ``client_id``,
    calls the EGI token endpoint **once**, and returns one of:

    * ``{"status": "pending"}`` — user has not yet approved (HTTP 202).
    * ``{"status": "slow_down"}`` — increase polling interval (HTTP 202).
    * Token dict (``access_token``, ``refresh_token``, …) on success (HTTP 200).
    * ``{"error": "..."}`` on terminal failures (HTTP 400 / 502).

    The client is responsible for respecting the ``interval`` from the initial
    device flow response and retrying until it receives a 200 or an error.
    """
    body = request.get_json(silent=True) or {}

    device_code = body.get("device_code", "").strip()
    if not device_code:
        return _json({"error": "'device_code' is required."}, 400)

    oidc = get_oidc_config()
    client_id = body.get("client_id", "").strip() or oidc["client_id"]

    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": client_id,
    }

    try:
        resp = _requests.post(
            _get_token_endpoint(issuer=oidc["issuer"]),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=30,
        )
    except Exception as exc:
        logger.error("Device poll network error: %s", exc)
        return _json({"error": f"Network error: {exc}"}, 502)

    if resp.status_code == 200:
        tokens = resp.json()
        logger.info("Device flow completed; returning tokens.")
        return _json(tokens, 200)

    try:
        err_body = resp.json()
    except Exception:
        return _json({"error": f"Unexpected response ({resp.status_code})"}, 502)

    err = err_body.get("error", "")

    if err == "authorization_pending":
        return _json({"status": "pending"}, 202)
    if err == "slow_down":
        return _json({"status": "slow_down"}, 202)
    if err in ("access_denied", "expired_token", "invalid_grant"):
        return _json({"error": err, "description": err_body.get("error_description", "")}, 400)

    return _json({"error": f"Unexpected error from token endpoint: {err_body!r}"}, 502)


# ── Session helpers ───────────────────────────────────────────────────────────


def _store_tokens_in_session(tokens: dict) -> None:
    """
    Store token response in the Flask session using the same format as
    ``app/auth.py`` so the rest of the GUI layer works without changes.

    Args:
        tokens: Token response dict from the OIDC token endpoint.
    """
    from lib.token_auth import validate_token, derive_namespace

    access_token = tokens.get("access_token", "")
    try:
        claims = validate_token(access_token)
    except ValueError as exc:
        logger.warning("Could not validate access token from auth code flow: %s", exc)
        # Store the raw token anyway — session expiry will handle it.
        claims = {}

    namespace = derive_namespace(claims.get("sub", "")) if claims.get("sub") else ""

    session["token"] = access_token
    session["claims"] = claims
    session["namespace"] = namespace
    session["tokens"] = tokens  # includes refresh_token / id_token
