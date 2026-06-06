"""
tests/api/test_checkin_api.py — Unit tests for api.checkin and the new
PKCE/auth-code helpers added to lib.token_checkin.

All HTTP calls to EGI Check-in are mocked; no real network traffic is made.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, session

from api.checkin import checkin_bp
from lib.token_checkin import (
    build_auth_code_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
)

# Default OIDC config returned when no site_config / env-var overrides are active.
_DEFAULT_OIDC = {
    "issuer": "https://aai.egi.eu/auth/realms/egi",
    "client_id": "oidc-agent",
    "scope": "openid offline_access profile email",
    "redirect_uri": "http://localhost:5000/api/auth/checkin/callback",
}

_DEV_OIDC = {
    "issuer": "https://aai-dev.egi.eu/auth/realms/egi",
    "client_id": "my-dev-client",
    "scope": "openid profile",
    "redirect_uri": "http://localhost:5000/api/auth/checkin/callback",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    if status_code >= 400:
        from requests import HTTPError

        resp.raise_for_status.side_effect = HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Minimal Flask app for blueprint testing
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    """Flask test application with the checkin blueprint registered."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.secret_key = "test-secret"
    flask_app.register_blueprint(checkin_bp)

    # Stub out the app_k8s.index endpoint that the callback redirects to.
    # Use add_url_rule so Flask's routing map knows the endpoint name.
    flask_app.add_url_rule(
        "/",
        endpoint="app_k8s.index",
        view_func=lambda: ("home", 200),
    )

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# lib.token_checkin — PKCE helpers
# ---------------------------------------------------------------------------


class TestGeneratePkcePair:
    def test_returns_two_non_empty_strings(self):
        verifier, challenge = generate_pkce_pair()
        assert isinstance(verifier, str) and verifier
        assert isinstance(challenge, str) and challenge

    def test_verifier_and_challenge_are_different(self):
        verifier, challenge = generate_pkce_pair()
        assert verifier != challenge

    def test_challenge_is_deterministic_from_verifier(self):
        """The challenge is SHA-256(verifier), so re-computing must match."""
        import base64
        import hashlib

        verifier, challenge = generate_pkce_pair()
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected

    def test_each_call_produces_unique_pair(self):
        v1, c1 = generate_pkce_pair()
        v2, c2 = generate_pkce_pair()
        assert v1 != v2
        assert c1 != c2

    def test_verifier_is_url_safe(self):
        """URL-safe base64 must not contain '+' or '/' characters."""
        verifier, _ = generate_pkce_pair()
        assert "+" not in verifier
        assert "/" not in verifier


# ---------------------------------------------------------------------------
# lib.token_checkin — build_auth_code_url
# ---------------------------------------------------------------------------


class TestBuildAuthCodeUrl:
    def test_returns_correct_base_url(self):
        url = build_auth_code_url(
            client_id="oidc-agent",
            redirect_uri="http://localhost/callback",
            scope="openid",
            state="mystate",
            code_challenge="mychallenge",
        )
        assert url.startswith(
            "https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/auth?"
        )

    def test_contains_required_params(self):
        url = build_auth_code_url(
            client_id="oidc-agent",
            redirect_uri="http://localhost/callback",
            scope="openid profile",
            state="st123",
            code_challenge="ch456",
        )
        assert "response_type=code" in url
        assert "client_id=oidc-agent" in url
        assert "state=st123" in url
        assert "code_challenge=ch456" in url
        assert "code_challenge_method=S256" in url

    def test_redirect_uri_is_encoded(self):
        url = build_auth_code_url(
            client_id="oidc-agent",
            redirect_uri="http://localhost:5000/api/auth/checkin/callback",
            scope="openid",
            state="s",
            code_challenge="c",
        )
        # The colon and slashes in the redirect_uri should be percent-encoded.
        assert "redirect_uri=http" in url

    def test_custom_code_challenge_method(self):
        url = build_auth_code_url(
            client_id="c",
            redirect_uri="http://cb",
            scope="openid",
            state="s",
            code_challenge="ch",
            code_challenge_method="plain",
        )
        assert "code_challenge_method=plain" in url


# ---------------------------------------------------------------------------
# lib.token_checkin — exchange_code_for_tokens
# ---------------------------------------------------------------------------


class TestExchangeCodeForTokens:
    def test_returns_token_dict_on_200(self):
        tokens = {"access_token": "at", "id_token": "idt"}
        with patch(
            "lib.token_checkin.requests.post",
            return_value=_mock_response(200, tokens),
        ):
            result = exchange_code_for_tokens(
                code="code123",
                client_id="oidc-agent",
                redirect_uri="http://localhost/callback",
                code_verifier="verifier",
            )
        assert result["access_token"] == "at"

    def test_raises_runtime_error_on_non_200(self):
        with patch(
            "lib.token_checkin.requests.post",
            return_value=_mock_response(400, {"error": "invalid_grant"}),
        ):
            with pytest.raises(RuntimeError, match="Authorization code exchange failed"):
                exchange_code_for_tokens(
                    code="bad",
                    client_id="oidc-agent",
                    redirect_uri="http://localhost/callback",
                    code_verifier="v",
                )

    def test_posts_correct_grant_type(self):
        with patch(
            "lib.token_checkin.requests.post",
            return_value=_mock_response(200, {"access_token": "at"}),
        ) as mock_post:
            exchange_code_for_tokens("c", "oidc-agent", "http://cb", "v")

        posted = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert posted["grant_type"] == "authorization_code"
        assert posted["code"] == "c"
        assert posted["code_verifier"] == "v"


# ---------------------------------------------------------------------------
# GET /api/auth/checkin — client detection
# ---------------------------------------------------------------------------


class TestCheckinDetection:
    def test_browser_request_redirects(self, client):
        """A request with Accept: text/html should start the auth code flow."""
        device_resp = {
            "device_code": "dc",
            "user_code": "UC",
            "verification_uri": "https://aai.egi.eu/device",
            "interval": 5,
            "expires_in": 300,
        }
        # Even if device flow is mocked, browser path should NOT call it.
        with patch("api.checkin.start_device_flow", return_value=device_resp) as mock_df:
            resp = client.get(
                "/api/auth/checkin",
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
        # Should be a redirect (302) to EGI Check-in, not a JSON response.
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "aai.egi.eu" in location
        mock_df.assert_not_called()

    def test_curl_request_returns_device_flow_json(self, client):
        """A request without text/html Accept should start the device flow."""
        device_resp = {
            "device_code": "dc123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://aai.egi.eu/device",
            "verification_uri_complete": "https://aai.egi.eu/device?user_code=ABCD-1234",
            "interval": 5,
            "expires_in": 300,
        }
        with patch("api.checkin.start_device_flow", return_value=device_resp):
            resp = client.get(
                "/api/auth/checkin",
                headers={"Accept": "application/json"},
            )
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["flow"] == "device"
        assert data["user_code"] == "ABCD-1234"
        assert data["device_code"] == "dc123"
        assert data["poll_url"] == "/api/auth/checkin/device/poll"

    def test_no_accept_header_returns_device_flow_json(self, client):
        """curl with no Accept header should also get the device flow."""
        device_resp = {
            "device_code": "dc",
            "user_code": "XY-99",
            "verification_uri": "https://aai.egi.eu/device",
            "interval": 5,
            "expires_in": 300,
        }
        with patch("api.checkin.start_device_flow", return_value=device_resp):
            resp = client.get("/api/auth/checkin")
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["flow"] == "device"

    def test_device_flow_start_failure_returns_502(self, client):
        """If EGI Check-in is unreachable, the endpoint should return 502."""
        with patch(
            "api.checkin.start_device_flow",
            side_effect=Exception("connection refused"),
        ):
            resp = client.get("/api/auth/checkin")
        assert resp.status_code == 502
        data = resp.get_json(force=True)
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /api/auth/checkin — browser auth code flow: session storage
# ---------------------------------------------------------------------------


class TestBrowserAuthCodeFlow:
    def test_redirect_contains_pkce_params(self, client):
        """The redirect URL must include code_challenge and state."""
        resp = client.get(
            "/api/auth/checkin",
            headers={"Accept": "text/html"},
        )
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "code_challenge=" in location
        assert "state=" in location
        assert "response_type=code" in location

    def test_redirect_stores_state_and_verifier_in_session(self, app):
        """After the redirect, session must hold _checkin_state and _checkin_verifier."""
        with app.test_client() as c:
            c.get("/api/auth/checkin", headers={"Accept": "text/html"})
            with c.session_transaction() as sess:
                assert "_checkin_state" in sess
                assert "_checkin_verifier" in sess


# ---------------------------------------------------------------------------
# GET /api/auth/checkin/callback
# ---------------------------------------------------------------------------


class TestCheckinCallback:
    def _seed_session(self, client, state: str, verifier: str):
        """Pre-populate the session with PKCE state."""
        with client.session_transaction() as sess:
            sess["_checkin_state"] = state
            sess["_checkin_verifier"] = verifier

    def test_missing_code_returns_400(self, client):
        self._seed_session(client, "mystate", "myverifier")
        resp = client.get(
            "/api/auth/checkin/callback?state=mystate",
        )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert "code" in data["error"].lower()

    def test_state_mismatch_returns_400(self, client):
        self._seed_session(client, "correctstate", "myverifier")
        resp = client.get(
            "/api/auth/checkin/callback?code=somecode&state=wrongstate",
        )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert "state" in data["error"].lower()

    def test_idp_error_returns_400(self, client):
        resp = client.get(
            "/api/auth/checkin/callback?error=access_denied"
            "&error_description=User+denied",
        )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert data["error"] == "access_denied"

    def test_token_exchange_failure_returns_502(self, client):
        self._seed_session(client, "st", "vv")
        with patch(
            "api.checkin.exchange_code_for_tokens",
            side_effect=RuntimeError("exchange failed"),
        ):
            resp = client.get(
                "/api/auth/checkin/callback?code=mycode&state=st",
            )
        assert resp.status_code == 502
        data = resp.get_json(force=True)
        assert "exchange failed" in data["error"]

    def test_successful_exchange_stores_tokens_in_session(self, app):
        """After a successful callback the session must contain the token."""
        tokens = {
            "access_token": "valid-at",
            "id_token": "valid-idt",
            "refresh_token": "valid-rt",
        }
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_checkin_state"] = "good-state"
                sess["_checkin_verifier"] = "good-verifier"

            with (
                patch(
                    "api.checkin.exchange_code_for_tokens",
                    return_value=tokens,
                ),
                patch("api.checkin._store_tokens_in_session") as mock_store,
            ):
                resp = c.get(
                    "/api/auth/checkin/callback?code=mycode&state=good-state",
                )

        # Should redirect to home (302) after success.
        assert resp.status_code == 302
        mock_store.assert_called_once_with(tokens)

    def test_state_consumed_from_session_after_callback(self, app):
        """The state and verifier must be removed from the session after use."""
        tokens = {"access_token": "at"}
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_checkin_state"] = "used-state"
                sess["_checkin_verifier"] = "used-verifier"

            with (
                patch("api.checkin.exchange_code_for_tokens", return_value=tokens),
                patch("api.checkin._store_tokens_in_session"),
            ):
                c.get("/api/auth/checkin/callback?code=c&state=used-state")

            with c.session_transaction() as sess:
                assert "_checkin_state" not in sess
                assert "_checkin_verifier" not in sess


# ---------------------------------------------------------------------------
# POST /api/auth/checkin/device/poll
# ---------------------------------------------------------------------------


class TestDevicePoll:
    def test_missing_device_code_returns_400(self, client):
        resp = client.post(
            "/api/auth/checkin/device/poll",
            json={},
        )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert "device_code" in data["error"]

    def test_pending_returns_202(self, client):
        import requests as _requests_mod

        mock_resp = _mock_response(400, {"error": "authorization_pending"})
        with patch.object(_requests_mod, "post", return_value=mock_resp):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc123"},
            )
        assert resp.status_code == 202
        data = resp.get_json(force=True)
        assert data["status"] == "pending"

    def test_slow_down_returns_202(self, client):
        import requests as _requests_mod

        mock_resp = _mock_response(400, {"error": "slow_down"})
        with patch.object(_requests_mod, "post", return_value=mock_resp):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc123"},
            )
        assert resp.status_code == 202
        data = resp.get_json(force=True)
        assert data["status"] == "slow_down"

    def test_access_denied_returns_400(self, client):
        import requests as _requests_mod

        mock_resp = _mock_response(400, {"error": "access_denied"})
        with patch.object(_requests_mod, "post", return_value=mock_resp):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc123"},
            )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert data["error"] == "access_denied"

    def test_successful_poll_returns_tokens(self, client):
        import requests as _requests_mod

        tokens = {"access_token": "at", "refresh_token": "rt"}
        mock_resp = _mock_response(200, tokens)
        with patch.object(_requests_mod, "post", return_value=mock_resp):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc123"},
            )
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["access_token"] == "at"
        assert data["refresh_token"] == "rt"

    def test_expired_token_returns_400(self, client):
        import requests as _requests_mod

        mock_resp = _mock_response(400, {"error": "expired_token"})
        with patch.object(_requests_mod, "post", return_value=mock_resp):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc123"},
            )
        assert resp.status_code == 400
        data = resp.get_json(force=True)
        assert data["error"] == "expired_token"

    def test_custom_client_id_is_forwarded(self, client):
        """If client_id is supplied in the body it must be sent to EGI."""
        import requests as _requests_mod

        tokens = {"access_token": "at"}
        mock_resp = _mock_response(200, tokens)
        with patch.object(_requests_mod, "post", return_value=mock_resp) as mock_post:
            client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc", "client_id": "my-client"},
            )
        posted_data = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert posted_data["client_id"] == "my-client"

    def test_network_error_returns_502(self, client):
        import requests as _requests_mod

        with patch.object(_requests_mod, "post", side_effect=Exception("timeout")):
            resp = client.post(
                "/api/auth/checkin/device/poll",
                json={"device_code": "dc"},
            )
        assert resp.status_code == 502
        data = resp.get_json(force=True)
        assert "error" in data


# ---------------------------------------------------------------------------
# Site-config integration — OIDC values flow from get_oidc_config to lib calls
# ---------------------------------------------------------------------------


class TestSiteConfigIntegration:
    """Verify that values from get_oidc_config() are passed through to lib calls."""

    def test_device_flow_uses_site_config_client_id(self, client):
        """client_id from site_config must be forwarded to start_device_flow."""
        device_resp = {
            "device_code": "dc",
            "user_code": "XY",
            "verification_uri": "https://aai-dev.egi.eu/device",
            "interval": 5,
            "expires_in": 300,
        }
        with patch("api.checkin.get_oidc_config", return_value=_DEV_OIDC):
            with patch("api.checkin.start_device_flow", return_value=device_resp) as mock_df:
                client.get("/api/auth/checkin", headers={"Accept": "application/json"})

        call_kwargs = mock_df.call_args.kwargs
        assert call_kwargs["client_id"] == "my-dev-client"
        assert call_kwargs["issuer"] == "https://aai-dev.egi.eu/auth/realms/egi"

    def test_device_flow_response_includes_configured_client_id(self, client):
        """JSON payload must reflect the client_id from site_config."""
        device_resp = {
            "device_code": "dc",
            "user_code": "XY",
            "verification_uri": "https://aai-dev.egi.eu/device",
            "interval": 5,
            "expires_in": 300,
        }
        with patch("api.checkin.get_oidc_config", return_value=_DEV_OIDC):
            with patch("api.checkin.start_device_flow", return_value=device_resp):
                resp = client.get(
                    "/api/auth/checkin", headers={"Accept": "application/json"}
                )

        data = resp.get_json(force=True)
        assert data["client_id"] == "my-dev-client"

    def test_browser_flow_uses_site_config_issuer_for_redirect(self, client):
        """Browser redirect must contain the dev issuer host when site_config says so."""
        with patch("api.checkin.get_oidc_config", return_value=_DEV_OIDC):
            with patch(
                "lib.token_checkin.oidc_discover",
                return_value={
                    "authorization_endpoint": "https://aai-dev.egi.eu/auth",
                    "token_endpoint": "https://aai-dev.egi.eu/token",
                    "device_authorization_endpoint": "https://aai-dev.egi.eu/device",
                    "revocation_endpoint": "https://aai-dev.egi.eu/revoke",
                },
            ):
                resp = client.get(
                    "/api/auth/checkin", headers={"Accept": "text/html"}
                )

        assert resp.status_code == 302
        assert "aai-dev.egi.eu" in resp.headers["Location"]

    def test_callback_uses_site_config_redirect_uri(self, app):
        """exchange_code_for_tokens must receive the redirect_uri from site_config."""
        tokens = {"access_token": "at"}
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["_checkin_state"] = "s"
                sess["_checkin_verifier"] = "v"

            with (
                patch("api.checkin.get_oidc_config", return_value=_DEV_OIDC),
                patch(
                    "api.checkin.exchange_code_for_tokens", return_value=tokens
                ) as mock_exc,
                patch("api.checkin._store_tokens_in_session"),
            ):
                c.get("/api/auth/checkin/callback?code=abc&state=s")

        call_kwargs = mock_exc.call_args.kwargs
        assert call_kwargs["redirect_uri"] == _DEV_OIDC["redirect_uri"]
        assert call_kwargs["client_id"] == "my-dev-client"
        assert call_kwargs["issuer"] == "https://aai-dev.egi.eu/auth/realms/egi"
