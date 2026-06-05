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
