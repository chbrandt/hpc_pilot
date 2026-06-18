"""
app/auth.py — Flask session-based authentication for the Web GUI layer.

Handles:
- Login / logout routes (HTML form with token paste)
- Session helpers (get_session_user)
- require_login route decorator

The pure JWT validation logic lives in lib/token_auth.py.
"""

import logging
import time
from functools import wraps
from typing import Optional

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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page: accepts and validates an EGI Check-in access token."""
    from app.api_client import api_post

    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if not token:
            flash("Please paste your EGI Check-in access token.", "error")
            return redirect(url_for("auth.login"))

        # Validate the token (JWKS signature + expiry + trusted issuer)
        try:
            claims = validate_token(token)
            logger.debug(f"Token claims: {claims}")
        except ValueError as exc:
            flash(f"Token validation failed: {exc}", "error")
            return redirect(url_for("auth.login"))

        # Group-access check (no-op when allowed_groups is empty)
        allowed_groups = load_site_config().get("allowed_groups") or []
        if allowed_groups:
            # Entitlements are not in the JWT — fetch them from the UserInfo endpoint
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
                return redirect(url_for("auth.login"))
        try:
            check_group_access(claims, allowed_groups)
        except ValueError as exc:
            logger.warning("Web login group access denied: %s", exc)
            flash(str(exc), "error")
            return redirect(url_for("auth.login"))

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
            result = api_post("/api/namespaces/ensure")
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
        next_url = request.form.get("next") or url_for("app_k8s.index")
        return redirect(next_url)

    # GET — render the login form
    reason = request.args.get("reason")   # "expired"
    refresh = request.args.get("refresh")  # "1"
    next_url = request.args.get("next", "")
    return render_template(
        "login.html", reason=reason, refresh=refresh, next_url=next_url
    )


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
