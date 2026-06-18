"""
tests/lib/test_token_auth.py — Unit tests for lib.token_auth.

All network calls (requests.get) and cryptographic operations are mocked so
the tests run without any external connectivity.
"""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from lib.token_auth import (
    TRUSTED_ISSUERS,
    _KEY_CACHE,
    check_group_access,
    derive_namespace,
    validate_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRUSTED_ISSUER = TRUSTED_ISSUERS[0]
FAKE_KID = "test-key-id"
FAKE_ALG = "RS256"

FAKE_HEADER = {"kid": FAKE_KID, "alg": FAKE_ALG}
FAKE_CLAIMS = {
    "sub": "user-sub-abc123",
    "iss": TRUSTED_ISSUER,
    "exp": int(time.time()) + 3600,
    "iat": int(time.time()),
}


def _make_fake_jwks_response(kid: str = FAKE_KID) -> dict:
    """Return a minimal JWKS dict with one RSA key."""
    return {"keys": [{"kid": kid, "kty": "RSA", "use": "sig"}]}


# ---------------------------------------------------------------------------
# derive_namespace
# ---------------------------------------------------------------------------


class TestDeriveNamespace:
    def test_returns_user_prefix(self):
        ns = derive_namespace("some-sub")
        assert ns.startswith("user-")

    def test_length_is_21_chars(self):
        ns = derive_namespace("some-sub")
        assert len(ns) == 21  # "user-" (5) + 16 hex chars

    def test_deterministic(self):
        assert derive_namespace("abc") == derive_namespace("abc")

    def test_different_subs_give_different_namespaces(self):
        assert derive_namespace("sub-a") != derive_namespace("sub-b")

    def test_uses_sha256_hexdigest(self):
        sub = "my-subject"
        expected_hash = hashlib.sha256(sub.encode()).hexdigest()[:16]
        assert derive_namespace(sub) == f"user-{expected_hash}"

    def test_result_is_lowercase_alphanumeric_and_hyphens(self):
        ns = derive_namespace("any-subject-value")
        # Kubernetes namespace rules: lowercase, alphanumeric, hyphens
        import re
        assert re.match(r"^[a-z0-9-]+$", ns)


# ---------------------------------------------------------------------------
# validate_token — header / payload checks (no real network)
# ---------------------------------------------------------------------------


class TestValidateTokenBadInput:
    def test_malformed_jwt_raises_value_error(self):
        with pytest.raises(ValueError, match="Malformed JWT"):
            validate_token("not.a.valid.jwt.at.all")

    def test_missing_kid_raises_value_error(self):
        import jwt as pyjwt
        # Create a token without a 'kid' header
        token = pyjwt.encode(
            FAKE_CLAIMS,
            "secret",
            algorithm="HS256",
            headers={"alg": "HS256"},  # no 'kid'
        )
        with pytest.raises(ValueError, match="kid"):
            validate_token(token)

    def test_non_rsa_algorithm_raises_value_error(self):
        import jwt as pyjwt
        token = pyjwt.encode(
            FAKE_CLAIMS,
            "secret",
            algorithm="HS256",
            headers={"kid": FAKE_KID, "alg": "HS256"},
        )
        with pytest.raises(ValueError, match="[Uu]nsupported algorithm"):
            validate_token(token)

    def test_untrusted_issuer_raises_value_error(self):
        evil_claims = {**FAKE_CLAIMS, "iss": "https://evil.issuer.example.com"}
        with (
            patch("jwt.get_unverified_header", return_value=FAKE_HEADER),
            patch("jwt.decode", return_value=evil_claims),
        ):
            with pytest.raises(ValueError, match="[Nn]ot trusted|[Ii]ssuer"):
                validate_token("any.token.value")

    def test_missing_iss_raises_value_error(self):
        no_iss_claims = {k: v for k, v in FAKE_CLAIMS.items() if k != "iss"}
        with (
            patch("jwt.get_unverified_header", return_value=FAKE_HEADER),
            patch("jwt.decode", return_value=no_iss_claims),
        ):
            with pytest.raises(ValueError, match="iss"):
                validate_token("any.token.value")


# ---------------------------------------------------------------------------
# validate_token — full happy path with mocked JWKS + pyjwt.decode
# ---------------------------------------------------------------------------


class TestValidateTokenSuccess:
    def test_valid_token_returns_claims(self):
        """
        Patch requests.get (JWKS discovery + fetch) and pyjwt machinery so
        the full validate_token flow succeeds without a real key or network.
        """
        import jwt as pyjwt

        # Build a raw token with a trusted issuer header so header checks pass.
        raw_token = "header.payload.signature"

        fake_header = {"kid": FAKE_KID, "alg": "RS256"}

        with (
            patch("lib.token_auth._fetch_jwks") as mock_fetch_jwks,
            patch("lib.token_auth._get_public_key") as mock_get_key,
            patch("jwt.get_unverified_header", return_value=fake_header),
            patch(
                "jwt.decode",
                side_effect=[
                    # First call: unverified decode to get iss
                    FAKE_CLAIMS,
                    # Second call: full verified decode
                    FAKE_CLAIMS,
                ],
            ),
        ):
            mock_get_key.return_value = MagicMock()  # fake public key object
            result = validate_token(raw_token)

        assert result["sub"] == FAKE_CLAIMS["sub"]
        assert result["iss"] == TRUSTED_ISSUER

    def test_expired_token_raises_value_error(self):
        import jwt as pyjwt
        from jwt.exceptions import ExpiredSignatureError

        fake_header = {"kid": FAKE_KID, "alg": "RS256"}
        expired_claims = {**FAKE_CLAIMS, "exp": int(time.time()) - 100}

        with (
            patch("jwt.get_unverified_header", return_value=fake_header),
            patch(
                "jwt.decode",
                side_effect=[
                    expired_claims,          # unverified peek
                    ExpiredSignatureError(),  # full verify
                ],
            ),
            patch("lib.token_auth._get_public_key", return_value=MagicMock()),
        ):
            with pytest.raises(ValueError, match="[Ee]xpired"):
                validate_token("any.token.value")


# ---------------------------------------------------------------------------
# check_group_access
# ---------------------------------------------------------------------------

_ENTITLEMENT_ACCESS = (
    "urn:mace:egi.eu:group:vo.access.egi.eu:role=member#aai.egi.eu"
)
_ENTITLEMENT_NOTEBOOKS = (
    "urn:mace:egi.eu:group:vo.notebooks.egi.eu:role=member#aai.egi.eu"
)


class TestCheckGroupAccess:
    """Tests for lib.token_auth.check_group_access (substring matching)."""

    # ── Open-access cases (empty / None allowed_groups) ──────────────

    def test_empty_allowed_groups_passes(self):
        """No restrictions configured → any authenticated user is allowed."""
        check_group_access({"sub": "user"}, [])

    def test_none_allowed_groups_passes(self):
        """None is treated the same as an empty list."""
        check_group_access({"sub": "user"}, None)

    # ── Successful match cases ────────────────────────────────────────

    def test_match_in_eduperson_entitlement(self):
        """Substring found in 'eduperson_entitlement' → passes."""
        claims = {"eduperson_entitlement": [_ENTITLEMENT_ACCESS]}
        check_group_access(claims, ["vo.access.egi.eu"])  # no exception

    def test_match_in_entitlements_field(self):
        """Substring found in 'entitlements' → passes."""
        claims = {"entitlements": [_ENTITLEMENT_NOTEBOOKS]}
        check_group_access(claims, ["vo.notebooks.egi.eu"])  # no exception

    def test_match_in_either_field_is_sufficient(self):
        """A hit in either field grants access (union semantics)."""
        claims = {
            "eduperson_entitlement": [_ENTITLEMENT_ACCESS],
            "entitlements": [_ENTITLEMENT_NOTEBOOKS],
        }
        # required group only present in 'entitlements'
        check_group_access(claims, ["vo.notebooks.egi.eu"])

    def test_first_matching_group_is_sufficient(self):
        """Having at least one required group is enough even if others are missing."""
        claims = {"eduperson_entitlement": [_ENTITLEMENT_ACCESS]}
        check_group_access(
            claims, ["vo.missing.egi.eu", "vo.access.egi.eu"]
        )  # second entry matches

    def test_string_entitlement_value_handled(self):
        """A bare string (not a list) in either claim field is accepted."""
        claims = {"eduperson_entitlement": _ENTITLEMENT_ACCESS}
        check_group_access(claims, ["vo.access.egi.eu"])

    # ── Failure cases ─────────────────────────────────────────────────

    def test_no_match_raises_value_error(self):
        """No entitlement matches any required group → ValueError."""
        claims = {"eduperson_entitlement": [_ENTITLEMENT_ACCESS]}
        with pytest.raises(ValueError, match="[Aa]ccess denied"):
            check_group_access(claims, ["vo.other.egi.eu"])

    def test_missing_entitlement_claims_raises_value_error(self):
        """Token has no entitlement claims at all → ValueError."""
        claims = {"sub": "user-no-groups"}
        with pytest.raises(ValueError, match="[Aa]ccess denied"):
            check_group_access(claims, ["vo.access.egi.eu"])

    def test_empty_entitlement_list_raises_value_error(self):
        """Empty entitlement list with a required group → ValueError."""
        claims = {"eduperson_entitlement": [], "entitlements": []}
        with pytest.raises(ValueError, match="[Aa]ccess denied"):
            check_group_access(claims, ["vo.access.egi.eu"])

    def test_error_message_includes_required_groups(self):
        """The ValueError message should mention the required groups."""
        required = ["vo.specific.egi.eu"]
        claims = {"eduperson_entitlement": [_ENTITLEMENT_ACCESS]}
        with pytest.raises(ValueError, match="vo.specific.egi.eu"):
            check_group_access(claims, required)
