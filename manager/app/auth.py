"""
app/auth.py — Flask session-based authentication for the Web GUI layer.

Handles:
- Login / logout routes (HTML form with token paste)
- EGI Check-in OAuth authorization-code login (GA4)
- Session helpers (get_session_user)
- require_login route decorator

The pure JWT validation logic lives in lib/token_auth.py.
"""

import base64
import hashlib
import logging
import os
import secrets
import time
from functools import wraps
from typing import Optional
from urllib.parse import urlencode

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from api.site_config import load_site_config
from lib.token_auth import (
    check_group_access,
    derive_namespace,
    fetch_userinfo,
    validate_token,
)

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Default EGI Check-in issuer (Keycloak realm). Override via site.oidc.issuer.
_DEFAULT_ISSUER = "https://aai.egi.eu/auth/realms/egi"


# ── Session helpers ───────────────────────────────────────────────────


def get_session_user() -> Optional[dict]:
    """
    Return current authenticated user info from the Flask session.

    Returns None if:
    - No token is stored in the session, or
    - The stored token's 'exp' claim is in the past.
    """
    claims = session.get("claims")
    if not claims:
        return None
    exp = claims.get("exp", 0)
    if time.time() > exp:
        return None
    return {
        "sub": claims.get("sub", ""),
        "namespace": session.get("namespace", ""),
        "exp": exp,
        "iss": claims.get("iss", ""),
    }


def require_login(f):
    """
    Flask route decorator that enforces session authentication.

    - HTML requests: redirects to /login with a flash message.
    - JSON / AJAX requests: returns HTTP 401 with a JSON error body.
    """
    import json

    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_session_user()
        if user is None:
            is_json = (
                "application/json" in request.headers.get("Accept", "")
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            )
            if is_json:
                return (
                    json.dumps({"error": "Authentication required", "code": 401}),
                    401,
                    {"Content-Type": "application/json"},
                )
            flash("Please log in with your EGI Check-in access token.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


# ── Routes ────────────────────────────────────────────────────────────


def _oidc_config() -> dict:
    """
    Return the OIDC client configuration from the ``site.oidc`` section of
    the unified config, or an empty dict when not configured.

    Keys: ``client_id``, ``client_secret`` (optional), ``issuer``
    (optional, defaults to the EGI Check-in production realm), and
    ``redirect_uri`` (optional explicit override).

    The ``PILOT_OIDC_CLIENT_SECRET`` environment variable, when set to a
    non-empty value, overrides ``client_secret`` so the secret can be kept
    out of the configuration file (same pattern as ``FLASK_SECRET_KEY``).
    """
    oidc = load_site_config().get("oidc")
    if not isinstance(oidc, dict):
        return {}
    env_secret = os.environ.get("PILOT_OIDC_CLIENT_SECRET")
    if env_secret:
        return {**oidc, "client_secret": env_secret}
    return oidc


def _establish_session(token: str, next_url: str):
    """
    Validate *token*, set up the Flask session and bootstrap the user's
    namespace / default configs.

    Shared by the token-paste login (POST /login) and the EGI Check-in
    OAuth callback (GET /oauth/callback).

    Returns a redirect response on success, or ``None`` on failure (a
    flash message describing the error has already been queued).
    """
    from app.api_client import api_post

    # Validate the token (JWKS signature + expiry + trusted issuer; the
    # 'aud' claim only when site.oidc.audience is configured)
    audience = _oidc_config().get("audience") or None
    try:
        claims = validate_token(token, audience=audience)
        logger.debug(f"Token claims: {claims}")
    except ValueError as exc:
        flash(f"Token validation failed: {exc}", "error")
        return None

    # Group-access check (no-op when allowed_groups is empty)
    allowed_groups = load_site_config().get("allowed_groups") or []
    if allowed_groups:
        # Entitlements are not in the JWT — fetch from the UserInfo endpoint
        try:
            userinfo = fetch_userinfo(token, claims["iss"])
            claims = {**claims, **userinfo}
        except ValueError as exc:
            logger.warning("UserInfo fetch failed: %s", exc)
            flash(
                f"Could not verify group membership "
                f"(UserInfo endpoint unavailable): {exc}",
                "error",
            )
            return None
    try:
        check_group_access(claims, allowed_groups)
    except ValueError as exc:
        logger.warning("Web login group access denied: %s", exc)
        flash(str(exc), "error")
        return None

    # Derive the user's personal namespace from the sub claim
    sub = claims["sub"]
    namespace = derive_namespace(sub)

    # Store validated credentials in the session
    session.clear()
    session["token"] = token
    session["claims"] = claims
    session["namespace"] = namespace

    # Auto-create the user's namespace if it doesn't exist yet (via API)
    try:
        result = api_post("/api/userspace/")
        if result.get("created"):
            logger.info(
                "Auto-created namespace '%s' for %s...", namespace, sub[:20]
            )
    except Exception as exc:
        # Non-fatal: namespace may be created on first deploy
        logger.warning("Namespace pre-creation skipped: %s", exc)

    # Seed global default chart configs (e.g. interlink) for this user
    try:
        api_post("/api/saved/seed")
    except Exception as exc:
        logger.warning("Could not seed default chart configs: %s", exc)

    flash(f"Welcome! Your namespace is {namespace}.", "success")
    return redirect(next_url or url_for("app_k8s.home"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page: accepts and validates an EGI Check-in access token."""
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if not token:
            flash("Please paste your EGI Check-in access token.", "error")
            return redirect(url_for("auth.login"))

        next_url = request.form.get("next") or url_for("app_k8s.home")
        result = _establish_session(token, next_url)
        return result if result is not None else redirect(url_for("auth.login"))

    # GET — render the login form
    reason = request.args.get("reason")   # "expired"
    refresh = request.args.get("refresh")  # "1"
    next_url = request.args.get("next", "")
    oidc_enabled = bool(_oidc_config().get("client_id"))
    return render_template(
        "login.html",
        reason=reason,
        refresh=refresh,
        next_url=next_url,
        oidc_enabled=oidc_enabled,
    )


# ── EGI Check-in OAuth authorization-code flow (GA4) ──────────────────


def _pkce_challenge(code_verifier: str) -> str:
    """
    Compute the ``S256`` PKCE code challenge (RFC 7636) for *code_verifier*.

    Returns the base64url-encoded SHA-256 digest of the verifier, without
    padding, as required by the ``S256`` method.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@auth_bp.route("/login/egi")
def egi_login():
    """
    Redirect the user to the EGI Check-in (Keycloak) authorization endpoint.

    Requires an OIDC client registered in EGI Check-in with this manager's
    ``/oauth/callback`` as an allowed redirect URI; configure it under
    ``site.oidc`` in pilot_config.yaml.

    The request carries a random CSRF ``state`` and a PKCE (RFC 7636, S256)
    ``code_challenge``; the matching verifier is kept in the session and
    sent to the token endpoint by :func:`egi_callback`. EGI Check-in
    clients are registered with PKCE required, so the flow fails without
    them.
    """
    oidc = _oidc_config()
    client_id = oidc.get("client_id")
    if not client_id:
        flash(
            "EGI Check-in login is not configured. "
            "Please paste an access token instead.",
            "error",
        )
        return redirect(url_for("auth.login"))

    issuer = oidc.get("issuer", _DEFAULT_ISSUER).rstrip("/")
    redirect_uri = oidc.get("redirect_uri") or (
        request.host_url.rstrip("/") + url_for("auth.egi_callback")
    )

    # CSRF protection: random state echoed back by the IdP
    state = secrets.token_urlsafe(32)
    session["oidc_state"] = state
    session["oidc_next"] = request.args.get("next", "")

    # PKCE (RFC 7636, S256): EGI Check-in rejects authorization requests
    # without a code_challenge/code_challenge_method pair.
    code_verifier = secrets.token_urlsafe(64)
    session["oidc_code_verifier"] = code_verifier

    authorize_url = (
        f"{issuer}/protocol/openid-connect/auth?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid",
                "state": state,
                "code_challenge": _pkce_challenge(code_verifier),
                "code_challenge_method": "S256",
            }
        )
    )
    return redirect(authorize_url)


@auth_bp.route("/oauth/callback")
def egi_callback():
    """
    Exchange the authorization code for tokens and establish the session.

    Verifies the ``state`` parameter against the one stored by
    :func:`egi_login` (CSRF protection) and, when present, the RFC 9207
    ``iss`` parameter against the configured issuer (IdP mix-up
    protection), then POSTs the code and the PKCE ``code_verifier`` to the
    IdP token endpoint and feeds the resulting access token through the
    same validation and session logic as the token-paste login.
    """
    oidc = _oidc_config()
    client_id = oidc.get("client_id")
    if not client_id:
        flash("EGI Check-in login is not configured.", "error")
        return redirect(url_for("auth.login"))

    # ── Verify state (CSRF) ───────────────────────────────────────────
    expected_state = session.pop("oidc_state", None)
    next_url = session.pop("oidc_next", "") or None
    received_state = request.args.get("state", "")
    if not expected_state or expected_state != received_state:
        logger.warning("OAuth state mismatch — possible CSRF attempt.")
        flash("Login failed: invalid state. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # ── Verify the issuer (RFC 9207, IdP mix-up protection) ──────────
    issuer = oidc.get("issuer", _DEFAULT_ISSUER).rstrip("/")
    received_iss = request.args.get("iss", "")
    if received_iss and received_iss.rstrip("/") != issuer:
        logger.warning(
            "OAuth issuer mismatch: expected %s, got %s.", issuer, received_iss
        )
        flash("Login failed: unexpected issuer. Please try again.", "error")
        return redirect(url_for("auth.login"))

    code = request.args.get("code", "")
    if not code:
        flash(
            f"Login failed: no authorization code "
            f"({request.args.get('error', 'unknown error')}).",
            "error",
        )
        return redirect(url_for("auth.login"))

    # ── PKCE verifier (matches the challenge sent by /login/egi) ─────
    code_verifier = session.pop("oidc_code_verifier", None)
    if not code_verifier:
        flash("Login failed: login session expired. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # ── Exchange the code for tokens ──────────────────────────────────
    redirect_uri = oidc.get("redirect_uri") or (
        request.host_url.rstrip("/") + url_for("auth.egi_callback")
    )
    token_url = f"{issuer}/protocol/openid-connect/token"

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    client_secret = oidc.get("client_secret")
    if client_secret:
        data["client_secret"] = client_secret

    try:
        resp = requests.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()
    except Exception as exc:
        logger.error("OAuth token exchange failed: %s", exc)
        flash("Login failed: could not exchange the authorization code.", "error")
        return redirect(url_for("auth.login"))

    access_token = tokens.get("access_token")
    if not access_token:
        flash("Login failed: no access token in the IdP response.", "error")
        return redirect(url_for("auth.login"))

    result = _establish_session(access_token, next_url)
    return result if result is not None else redirect(url_for("auth.login"))


@auth_bp.route("/logout")
def logout():
    """Clear the session and redirect to the login page."""
    reason = request.args.get("reason")
    session.clear()
    if reason == "expired":
        flash(
            "Your session has expired. Please paste a new token to continue.", "error"
        )
    else:
        flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
