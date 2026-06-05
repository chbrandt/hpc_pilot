"""
tests/api/test_auth.py — Unit tests for api.auth helpers.

Tests the Bearer-token extraction and the require_token decorator in
isolation, without needing a real JWT or EGI Check-in endpoint.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

from api.auth import extract_bearer_token, get_request_claims, require_token


# ---------------------------------------------------------------------------
# Minimal Flask app for decorator testing
# ---------------------------------------------------------------------------


@pytest.fixture()
def mini_app():
    """Tiny Flask app that registers one protected and one public route."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/protected")
    @require_token
    def protected_view():
        claims = get_request_claims()
        return {"ok": True, "sub": claims["sub"]}, 200

    @app.route("/public")
    def public_view():
        return {"ok": True}, 200

    return app


@pytest.fixture()
def mini_client(mini_app):
    return mini_app.test_client()


# ---------------------------------------------------------------------------
# extract_bearer_token
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    def test_returns_token_from_valid_header(self, mini_app):
        with mini_app.test_request_context(
            headers={"Authorization": "Bearer mytoken123"}
        ):
            assert extract_bearer_token() == "mytoken123"

    def test_returns_none_when_header_absent(self, mini_app):
        with mini_app.test_request_context():
            assert extract_bearer_token() is None

    def test_returns_none_for_non_bearer_scheme(self, mini_app):
        with mini_app.test_request_context(
            headers={"Authorization": "Basic dXNlcjpwYXNz"}
        ):
            assert extract_bearer_token() is None

    def test_returns_none_for_empty_header(self, mini_app):
        with mini_app.test_request_context(
            headers={"Authorization": ""}
        ):
            assert extract_bearer_token() is None


# ---------------------------------------------------------------------------
# require_token decorator
# ---------------------------------------------------------------------------


class TestRequireToken:
    def test_missing_auth_header_returns_401(self, mini_client):
        resp = mini_client.get("/protected")
        assert resp.status_code == 401
        data = resp.get_json(force=True)
        assert "error" in data

    def test_invalid_token_returns_401(self, mini_client):
        with patch("api.auth.validate_token", side_effect=ValueError("bad token")):
            resp = mini_client.get(
                "/protected", headers={"Authorization": "Bearer badtoken"}
            )
        assert resp.status_code == 401
        data = resp.get_json(force=True)
        assert "bad token" in data["error"]

    def test_expired_token_returns_401(self, mini_client):
        expired_claims = {
            "sub": "user-123",
            "iss": "https://aai.egi.eu/auth/realms/egi",
            "exp": int(time.time()) - 100,  # already expired
        }
        with (
            patch("api.auth.validate_token", return_value=expired_claims),
            patch("api.auth.derive_namespace", return_value="user-ns"),
        ):
            resp = mini_client.get(
                "/protected", headers={"Authorization": "Bearer expiredtoken"}
            )
        assert resp.status_code == 401

    def test_valid_token_calls_wrapped_function(self, mini_client):
        valid_claims = {
            "sub": "test-sub-xyz",
            "iss": "https://aai.egi.eu/auth/realms/egi",
            "exp": int(time.time()) + 3600,
        }
        with (
            patch("api.auth.validate_token", return_value=valid_claims),
            patch("api.auth.derive_namespace", return_value="user-testns"),
        ):
            resp = mini_client.get(
                "/protected", headers={"Authorization": "Bearer validtoken"}
            )
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["ok"] is True
        assert data["sub"] == "test-sub-xyz"

    def test_claims_namespace_injected(self, mini_client):
        """
        After successful auth, g._api_claims must contain the 'namespace' key
        derived from the sub claim.
        """
        valid_claims = {
            "sub": "some-sub",
            "iss": "https://aai.egi.eu/auth/realms/egi",
            "exp": int(time.time()) + 3600,
        }
        captured = {}

        @mini_client.application.route("/claims-check")
        @require_token
        def claims_check():
            captured["claims"] = get_request_claims()
            return {}, 200

        with (
            patch("api.auth.validate_token", return_value=valid_claims),
            patch("api.auth.derive_namespace", return_value="user-derived-ns"),
        ):
            mini_client.get(
                "/claims-check", headers={"Authorization": "Bearer tok"}
            )

        assert captured["claims"]["namespace"] == "user-derived-ns"
        assert captured["claims"]["_token"] == "tok"
