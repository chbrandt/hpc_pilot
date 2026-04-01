"""
EGI Check-in JWT access token validation and user identity utilities.

Validates tokens using JWKS signature verification against the issuer's
public keys, and provides namespace derivation helpers for Kubernetes.
"""

import hashlib
import json
import logging
import time
from functools import wraps
from typing import Optional

import jwt as pyjwt
import requests
from jwt.algorithms import RSAAlgorithm
from flask import flash, redirect, request, session, url_for

logger = logging.getLogger(__name__)

# ── Trusted issuers ───────────────────────────────────────────────────
TRUSTED_ISSUERS = [
    "https://aai.egi.eu/auth/realms/egi",
    "https://aai-dev.egi.eu/auth/realms/egi",
    "https://aai-demo.egi.eu/auth/realms/egi",
]

# ── In-memory JWKS key cache keyed by issuer ─────────────────────────
# Each entry: {"keys": list[dict], "fetched_at": float}
_KEY_CACHE: dict[str, dict] = {}
_CACHE_TTL = 3600  # seconds (1 hour)


# ── JWKS helpers ──────────────────────────────────────────────────────


def _fetch_jwks(issuer: str) -> list[dict]:
    """
    Return JWKS keys for the given issuer, using a 1-hour in-memory cache.

    Discovers the JWKS URI from the issuer's OpenID Connect well-known
    configuration endpoint, then fetches and caches the key set.

    Raises:
        ValueError: if the well-known or JWKS endpoint cannot be reached.
    """
    cached = _KEY_CACHE.get(issuer)
    if cached and (time.time() - cached["fetched_at"]) < _CACHE_TTL:
        return cached["keys"]

    # Discover JWKS URI from .well-known configuration
    well_known_url = f"{issuer}/.well-known/openid-configuration"
    try:
        resp = requests.get(well_known_url, timeout=10)
        resp.raise_for_status()
        oidc_config = resp.json()
        jwks_uri = oidc_config["jwks_uri"]
        logger.debug(f"Discovered JWKS URI: {jwks_uri}")
    except Exception as exc:
        raise ValueError(
            f"Cannot fetch OIDC configuration from {well_known_url}: {exc}"
        ) from exc

    # Fetch JWKS
    try:
        resp = requests.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
        logger.debug(f"Fetched {len(keys)} JWKS keys for issuer {issuer}")
    except Exception as exc:
        raise ValueError(f"Cannot fetch JWKS from {jwks_uri}: {exc}") from exc

    _KEY_CACHE[issuer] = {"keys": keys, "fetched_at": time.time()}
    return keys


def _get_public_key(issuer: str, kid: str):
    """
    Return the RSA public key object for the given issuer and key ID.

    Automatically retries once with a fresh JWKS fetch if the key is not
    found in the cache (handles key rotation).

    Raises:
        ValueError: if no matching key is found after retry.
    """
    keys = _fetch_jwks(issuer)
    matching = [k for k in keys if k.get("kid") == kid]

    if not matching:
        # Key may have rotated — clear cache and retry once
        _KEY_CACHE.pop(issuer, None)
        keys = _fetch_jwks(issuer)
        matching = [k for k in keys if k.get("kid") == kid]

    if not matching:
        raise ValueError(f"No JWKS key found for kid='{kid}' under issuer '{issuer}'.")

    return RSAAlgorithm.from_jwk(json.dumps(matching[0]))


# ── Token validation ──────────────────────────────────────────────────


def validate_token(token: str) -> dict:
    """
    Validate an EGI Check-in JWT access token end-to-end.

    Steps:
    1. Decode the JWT header to extract 'kid' and 'alg'.
    2. Decode the payload without verification to extract 'iss'.
    3. Verify 'iss' is in the trusted issuers list.
    4. Fetch (or use cached) JWKS keys for the issuer.
    5. Locate the matching public key by 'kid'.
    6. Verify the full token: signature, expiry ('exp'), and issuer.
    7. Return the verified claims dict.

    Args:
        token: Raw JWT string (the EGI Check-in access token).

    Returns:
        Decoded and verified claims dict.

    Raises:
        ValueError: with a human-readable message for any validation failure.
    """
    # Step 1 — Decode header
    try:
        header = pyjwt.get_unverified_header(token)
    except pyjwt.exceptions.DecodeError as exc:
        raise ValueError(f"Malformed JWT header: {exc}") from exc

    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    if not kid:
        raise ValueError("Token is missing the 'kid' header field.")
    if not alg.startswith("RS"):
        raise ValueError(f"Unsupported algorithm '{alg}'. Expected RSA.")

    # Step 2 — Peek at payload (unverified) to get issuer
    try:
        unverified = pyjwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False},
        )
    except pyjwt.exceptions.DecodeError as exc:
        raise ValueError(f"Malformed JWT payload: {exc}") from exc

    issuer = unverified.get("iss")
    if not issuer:
        raise ValueError("Token is missing the 'iss' (issuer) claim.")

    # Step 3 — Validate issuer
    if issuer not in TRUSTED_ISSUERS:
        raise ValueError(
            f"Issuer '{issuer}' is not trusted. "
            f"Accepted issuers: {', '.join(TRUSTED_ISSUERS)}"
        )

    # Steps 4 & 5 — Fetch JWKS and locate matching public key
    public_key = _get_public_key(issuer, kid)

    # Step 6 — Full verification: signature + exp + iss
    try:
        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=[alg],
            issuer=issuer,
            options={"verify_exp": True},
        )
    except pyjwt.exceptions.ExpiredSignatureError:
        raise ValueError("Token has expired.")
    except pyjwt.exceptions.InvalidIssuerError:
        raise ValueError(f"Token issuer mismatch (expected '{issuer}').")
    except pyjwt.exceptions.InvalidTokenError as exc:
        raise ValueError(f"Token signature/validation failed: {exc}") from exc

    logger.info(f"Token validated for sub={claims.get('sub', '?')[:20]}...")
    return claims


# ── Namespace derivation ──────────────────────────────────────────────


def derive_namespace(sub: str) -> str:
    """
    Derive a stable, Kubernetes-safe namespace name from a user's subject ID.

    Algorithm: ``"user-" + sha256(sub).hexdigest()[:16]``

    The result is always exactly 21 characters — valid as a Kubernetes
    namespace (lowercase, alphanumeric/hyphens, ≤63 chars).

    Example::

        sub = "71a0a90cbb0e71fa8893...@egi.eu"
        → "user-a3f1b2c4d5e6f7a8"
    """
    digest = hashlib.sha256(sub.encode()).hexdigest()[:16]
    return f"user-{digest}"


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


# ── Route decorator ───────────────────────────────────────────────────


def require_token(f):
    """
    Flask route decorator that enforces authentication.

    Checks the Flask session for a valid, non-expired token.

    - HTML requests: redirects to /login with a flash message.
    - JSON / AJAX requests: returns HTTP 401 with a JSON error body.
    """

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
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated
