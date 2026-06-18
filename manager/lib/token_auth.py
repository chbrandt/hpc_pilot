"""
token_auth.py — EGI Check-in JWT access token validation, group access
control, and namespace derivation.

Pure Python module: no web-framework dependency.

Validates tokens using JWKS signature verification against the issuer's
public keys, enforces optional group-membership restrictions via
``check_group_access``, and provides namespace derivation helpers for
Kubernetes.

Flask session helpers (get_session_user, require_login) live in the
respective web layers: app/auth.py (GUI) and api/auth.py (REST API).
"""

import hashlib
import json
import logging
import time
from typing import Optional

import jwt as pyjwt
import requests
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger(__name__)

# ── Trusted issuers ───────────────────────────────────────────────────
TRUSTED_ISSUERS = [
    "https://aai.egi.eu/auth/realms/egi",
    "https://aai-dev.egi.eu/auth/realms/egi",
    "https://aai-demo.egi.eu/auth/realms/egi",
]

# ── In-memory caches ──────────────────────────────────────────────────
# OIDC config cache: {"config": dict, "fetched_at": float}
_OIDC_CONFIG_CACHE: dict[str, dict] = {}
# JWKS key cache: {"keys": list[dict], "fetched_at": float}
_KEY_CACHE: dict[str, dict] = {}
_CACHE_TTL = 3600  # seconds (1 hour)


# ── OIDC config / JWKS helpers ────────────────────────────────────────


def _get_oidc_config(issuer: str) -> dict:
    """
    Return the cached OpenID Connect well-known configuration for the issuer.

    Fetches and caches ``{issuer}/.well-known/openid-configuration`` for
    ``_CACHE_TTL`` seconds.  The config dict contains fields such as
    ``jwks_uri`` and ``userinfo_endpoint`` used by other helpers.

    Args:
        issuer: Trusted issuer URL.

    Returns:
        The parsed OIDC well-known configuration dict.

    Raises:
        ValueError: if the well-known endpoint cannot be reached or parsed.
    """
    cached = _OIDC_CONFIG_CACHE.get(issuer)
    if cached and (time.time() - cached["fetched_at"]) < _CACHE_TTL:
        return cached["config"]

    well_known_url = f"{issuer}/.well-known/openid-configuration"
    try:
        resp = requests.get(well_known_url, timeout=10)
        resp.raise_for_status()
        config = resp.json()
        logger.debug(f"Fetched OIDC configuration for issuer {issuer}")
    except Exception as exc:
        raise ValueError(
            f"Cannot fetch OIDC configuration from {well_known_url}: {exc}"
        ) from exc

    _OIDC_CONFIG_CACHE[issuer] = {"config": config, "fetched_at": time.time()}
    return config


def _fetch_jwks(issuer: str) -> list[dict]:
    """
    Return JWKS keys for the given issuer, using a 1-hour in-memory cache.

    Discovers the JWKS URI from the issuer's cached OIDC well-known
    configuration, then fetches and caches the key set.

    Raises:
        ValueError: if the JWKS endpoint cannot be reached.
    """
    cached = _KEY_CACHE.get(issuer)
    if cached and (time.time() - cached["fetched_at"]) < _CACHE_TTL:
        return cached["keys"]

    oidc_config = _get_oidc_config(issuer)
    jwks_uri = oidc_config["jwks_uri"]
    logger.debug(f"Discovered JWKS URI: {jwks_uri}")

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
        # Key may have rotated — clear JWKS cache and retry once
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

    Note:
        The returned claims dict contains only the fields embedded in the
        JWT itself.  EGI Check-in does **not** include entitlement claims
        (``eduperson_entitlement``, ``entitlements``) in the access token.
        Call :func:`fetch_userinfo` to obtain the full profile including
        group membership, then pass the merged dict to
        :func:`check_group_access`.

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

    logger.info(f"Token validated for sub={claims.get('sub', '?')}...")
    return claims


# ── UserInfo fetch ────────────────────────────────────────────────────


def fetch_userinfo(token: str, issuer: str) -> dict:
    """
    Fetch the full user profile from the OIDC UserInfo endpoint.

    EGI Check-in does **not** embed entitlement claims
    (``eduperson_entitlement``, ``entitlements``) in the JWT access token
    payload — they are only available by calling the UserInfo endpoint with
    the access token as the bearer credential.

    The ``userinfo_endpoint`` URL is discovered from the issuer's well-known
    OIDC configuration, which is already cached by :func:`_get_oidc_config`.

    Args:
        token: Raw JWT access token string (used as the bearer credential).
        issuer: Trusted issuer URL (must already have been validated by
            :func:`validate_token`).

    Returns:
        UserInfo claims dict.  Typically includes ``sub``, ``email``,
        ``eduperson_entitlement``, ``entitlements``, and other profile
        fields released by the OP.

    Raises:
        ValueError: if the UserInfo endpoint cannot be reached or returns a
            non-2xx response.

    Example::

        claims = validate_token(token)
        userinfo = fetch_userinfo(token, claims["iss"])
        merged = {**claims, **userinfo}
        check_group_access(merged, allowed_groups)
    """
    oidc_config = _get_oidc_config(issuer)
    userinfo_url = oidc_config.get("userinfo_endpoint")
    if not userinfo_url:
        raise ValueError(
            f"Issuer '{issuer}' OIDC configuration does not expose a "
            f"'userinfo_endpoint'."
        )

    try:
        resp = requests.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        userinfo = resp.json()
        logger.debug(f"Fetched UserInfo for sub={userinfo.get('sub', '?')}")
        return userinfo
    except Exception as exc:
        raise ValueError(
            f"Cannot fetch UserInfo from {userinfo_url}: {exc}"
        ) from exc


# ── Group access control ──────────────────────────────────────────────


def check_group_access(claims: dict, allowed_groups: list[str]) -> None:
    """
    Enforce group membership by inspecting token entitlement claims.

    Looks in both ``eduperson_entitlement`` and ``entitlements`` claim
    fields (union of both lists).  An entitlement string is accepted when
    it *contains* any of the ``allowed_groups`` strings as a substring
    (case-sensitive).

    If ``allowed_groups`` is empty or ``None`` the check is skipped,
    granting open access to any authenticated EGI user.

    .. important::
        EGI Check-in does not embed entitlement claims in the JWT access
        token.  Pass a **merged** dict of JWT claims and UserInfo claims
        (see :func:`fetch_userinfo`) to this function; do not pass the raw
        JWT claims dict when group enforcement is needed.

    Args:
        claims: Claims dict to inspect — typically the merged result of
            :func:`validate_token` and :func:`fetch_userinfo`.
        allowed_groups: Substrings that must appear in at least one
            entitlement value.  Example: ``["vo.access.egi.eu"]``.

    Raises:
        ValueError: if ``allowed_groups`` is non-empty and none of the
            required substrings are found in the token's entitlements.

    Example::

        claims = {
            "eduperson_entitlement": [
                "urn:mace:egi.eu:group:vo.access.egi.eu:role=member#aai.egi.eu"
            ]
        }
        check_group_access(claims, ["vo.access.egi.eu"])  # passes
        check_group_access(claims, ["vo.other.egi.eu"])   # raises ValueError
    """
    if not allowed_groups:
        return  # open access — nothing to enforce

    # Collect all entitlement strings from both claim fields
    entitlements: list[str] = []
    for field in ("eduperson_entitlement", "entitlements"):
        value = claims.get(field, [])
        if isinstance(value, list):
            entitlements.extend(value)
        elif isinstance(value, str):
            entitlements.append(value)

    logger.debug(
        f"Checking group access — required: {allowed_groups}, "
        f"found entitlements: {entitlements}"
    )

    # Accept if any entitlement contains any of the required group substrings
    for group in allowed_groups:
        if any(group in ent for ent in entitlements):
            logger.debug(f"Group access granted: matched '{group}'")
            return

    raise ValueError(
        f"Access denied: your token does not contain a required group "
        f"entitlement. Required (any of): {allowed_groups}"
    )


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
