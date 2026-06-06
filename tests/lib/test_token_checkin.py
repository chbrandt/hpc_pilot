"""
tests/lib/test_token_checkin.py — Unit tests for lib.token_checkin.

All HTTP requests are mocked; no real EGI Check-in endpoint is called.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from lib.token_checkin import (
    DEFAULT_ISSUER,
    _DISCOVERY_CACHE,
    _get_auth_endpoint,
    _get_device_endpoint,
    _get_revocation_endpoint,
    _get_token_endpoint,
    build_auth_code_url,
    exchange_code_for_tokens,
    load_tokens,
    oidc_discover,
    poll_token_endpoint,
    refresh_with_rt,
    revoke_token,
    save_tokens,
    start_device_flow,
)


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
# save_tokens / load_tokens
# ---------------------------------------------------------------------------


class TestSaveLoadTokens:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "tokens.json")
        tokens = {"access_token": "at", "refresh_token": "rt"}
        save_tokens(tokens, path)
        loaded = load_tokens(path)
        assert loaded["access_token"] == "at"
        assert loaded["refresh_token"] == "rt"

    def test_saved_file_has_restricted_permissions(self, tmp_path):
        path = str(tmp_path / "tokens.json")
        save_tokens({"access_token": "at"}, path)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_load_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_tokens(str(tmp_path / "nonexistent.json"))

    def test_load_invalid_json_raises_value_error(self, tmp_path):
        path = str(tmp_path / "bad.json")
        path_obj = tmp_path / "bad.json"
        path_obj.write_text("not valid json")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_tokens(str(path_obj))


# ---------------------------------------------------------------------------
# start_device_flow
# ---------------------------------------------------------------------------


class TestStartDeviceFlow:
    def test_returns_device_code_response(self):
        resp_body = {
            "device_code": "devcode123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://aai.egi.eu/device",
            "interval": 5,
            "expires_in": 300,
        }
        with patch("lib.token_checkin.requests.post", return_value=_mock_response(200, resp_body)):
            result = start_device_flow("oidc-agent", "openid")
        assert result["device_code"] == "devcode123"

    def test_raises_on_http_error(self):
        from requests import HTTPError
        resp = _mock_response(400, {"error": "invalid_client"})
        with patch("lib.token_checkin.requests.post", return_value=resp):
            with pytest.raises(HTTPError):
                start_device_flow("bad-client", "openid")

    def test_audience_included_when_provided(self):
        resp_body = {"device_code": "dc", "interval": 5}
        with patch("lib.token_checkin.requests.post", return_value=_mock_response(200, resp_body)) as mock_post:
            start_device_flow("oidc-agent", "openid", audience="interlink")
        posted_data = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1].get("data")
        assert posted_data.get("audience") == "interlink"


# ---------------------------------------------------------------------------
# poll_token_endpoint
# ---------------------------------------------------------------------------


class TestPollTokenEndpoint:
    def test_returns_tokens_on_200(self):
        tokens = {"access_token": "at", "refresh_token": "rt"}
        with patch("lib.token_checkin.requests.post", return_value=_mock_response(200, tokens)):
            with patch("time.sleep"):  # skip actual sleep
                result = poll_token_endpoint("devcode", "oidc-agent", interval=5)
        assert result["access_token"] == "at"

    def test_retries_on_authorization_pending(self):
        pending = _mock_response(400, {"error": "authorization_pending"})
        success = _mock_response(200, {"access_token": "at"})
        with patch("lib.token_checkin.requests.post", side_effect=[pending, success]):
            with patch("time.sleep"):
                result = poll_token_endpoint("devcode", "oidc-agent", interval=5)
        assert result["access_token"] == "at"

    def test_slow_down_increases_interval(self):
        slow = _mock_response(400, {"error": "slow_down"})
        success = _mock_response(200, {"access_token": "at"})
        with patch("lib.token_checkin.requests.post", side_effect=[slow, success]):
            with patch("time.sleep"):
                result = poll_token_endpoint("devcode", "oidc-agent", interval=5)
        assert result["access_token"] == "at"

    def test_access_denied_raises_runtime_error(self):
        denied = _mock_response(400, {"error": "access_denied"})
        with patch("lib.token_checkin.requests.post", return_value=denied):
            with patch("time.sleep"):
                with pytest.raises(RuntimeError, match="access_denied"):
                    poll_token_endpoint("devcode", "oidc-agent", interval=5)

    def test_timeout_raises_timeout_error(self):
        pending = _mock_response(400, {"error": "authorization_pending"})
        with patch("lib.token_checkin.requests.post", return_value=pending):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 9999]):
                    with pytest.raises(TimeoutError):
                        poll_token_endpoint(
                            "devcode", "oidc-agent", interval=5, timeout_seconds=1
                        )


# ---------------------------------------------------------------------------
# refresh_with_rt
# ---------------------------------------------------------------------------


class TestRefreshWithRt:
    def test_success_returns_new_tokens(self):
        new_tokens = {"access_token": "new-at", "refresh_token": "new-rt"}
        with patch("lib.token_checkin.requests.post", return_value=_mock_response(200, new_tokens)):
            result = refresh_with_rt("old-rt", "oidc-agent")
        assert result["access_token"] == "new-at"

    def test_failure_raises_runtime_error(self):
        with patch("lib.token_checkin.requests.post", return_value=_mock_response(400, {"error": "invalid_grant"})):
            with pytest.raises(RuntimeError, match="Refresh failed"):
                refresh_with_rt("bad-rt", "oidc-agent")


# ---------------------------------------------------------------------------
# revoke_token
# ---------------------------------------------------------------------------


class TestRevokeToken:
    def test_returns_response_object(self):
        resp = _mock_response(200)
        with patch("lib.token_checkin.requests.post", return_value=resp):
            result = revoke_token("rt", "at", "oidc-agent")
        assert result.status_code == 200


# ---------------------------------------------------------------------------
# oidc_discover
# ---------------------------------------------------------------------------

_SAMPLE_DISCOVERY_DOC = {
    "issuer": "https://aai-dev.egi.eu/auth/realms/egi",
    "authorization_endpoint": "https://aai-dev.egi.eu/auth/realms/egi/protocol/openid-connect/auth",
    "token_endpoint": "https://aai-dev.egi.eu/auth/realms/egi/protocol/openid-connect/token",
    "device_authorization_endpoint": "https://aai-dev.egi.eu/auth/realms/egi/protocol/openid-connect/auth/device",
    "revocation_endpoint": "https://aai-dev.egi.eu/auth/realms/egi/protocol/openid-connect/revoke",
    "jwks_uri": "https://aai-dev.egi.eu/auth/realms/egi/protocol/openid-connect/certs",
}

_DEV_ISSUER = "https://aai-dev.egi.eu/auth/realms/egi"


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    """Ensure the module-level discovery cache is empty before each test."""
    _DISCOVERY_CACHE.clear()
    yield
    _DISCOVERY_CACHE.clear()


class TestOidcDiscover:
    def test_fetches_and_returns_doc(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            doc = oidc_discover(_DEV_ISSUER)
        assert doc["token_endpoint"] == _SAMPLE_DISCOVERY_DOC["token_endpoint"]

    def test_caches_result(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp) as mock_get:
            oidc_discover(_DEV_ISSUER)
            oidc_discover(_DEV_ISSUER)
        # Second call must hit the cache — only one HTTP GET made.
        assert mock_get.call_count == 1

    def test_expired_cache_refetches(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp) as mock_get:
            # Seed the cache with a timestamp far in the past.
            import lib.token_checkin as _mod
            _DISCOVERY_CACHE[_DEV_ISSUER] = {
                "doc": _SAMPLE_DISCOVERY_DOC,
                "fetched_at": time.time() - (_mod._DISCOVERY_CACHE_TTL + 1),
            }
            oidc_discover(_DEV_ISSUER)
        assert mock_get.call_count == 1

    def test_uses_default_issuer_when_none_given(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp) as mock_get:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CHECKIN_ISSUER", None)
                oidc_discover(None)
        called_url = mock_get.call_args[0][0]
        assert DEFAULT_ISSUER in called_url

    def test_uses_checkin_issuer_env_var(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp) as mock_get:
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _DEV_ISSUER}):
                oidc_discover(None)
        called_url = mock_get.call_args[0][0]
        assert _DEV_ISSUER in called_url

    def test_raises_runtime_error_on_network_failure(self):
        with patch(
            "lib.token_checkin.requests.get",
            side_effect=Exception("connection refused"),
        ):
            with pytest.raises(RuntimeError, match="Cannot fetch OIDC configuration"):
                oidc_discover(_DEV_ISSUER)

    def test_raises_runtime_error_on_non_2xx(self):
        mock_resp = _mock_response(503, {})
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Cannot fetch OIDC configuration"):
                oidc_discover(_DEV_ISSUER)


# ---------------------------------------------------------------------------
# Dynamic endpoint getters
# ---------------------------------------------------------------------------


class TestDynamicEndpointGetters:
    """When CHECKIN_ISSUER is not set the getters return the hardcoded defaults.
    When CHECKIN_ISSUER is set they return values from the discovery document.
    """

    def test_get_token_endpoint_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHECKIN_ISSUER", None)
            ep = _get_token_endpoint()
        assert "aai.egi.eu" in ep
        assert "token" in ep

    def test_get_device_endpoint_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHECKIN_ISSUER", None)
            ep = _get_device_endpoint()
        assert "aai.egi.eu" in ep
        assert "device" in ep

    def test_get_auth_endpoint_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHECKIN_ISSUER", None)
            ep = _get_auth_endpoint()
        assert "aai.egi.eu" in ep
        assert "auth" in ep

    def test_get_revocation_endpoint_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHECKIN_ISSUER", None)
            ep = _get_revocation_endpoint()
        assert "aai.egi.eu" in ep
        assert "revoke" in ep

    def test_get_token_endpoint_from_discovery(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _DEV_ISSUER}):
                ep = _get_token_endpoint()
        assert ep == _SAMPLE_DISCOVERY_DOC["token_endpoint"]

    def test_get_device_endpoint_from_discovery(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _DEV_ISSUER}):
                ep = _get_device_endpoint()
        assert ep == _SAMPLE_DISCOVERY_DOC["device_authorization_endpoint"]

    def test_get_auth_endpoint_from_discovery(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _DEV_ISSUER}):
                ep = _get_auth_endpoint()
        assert ep == _SAMPLE_DISCOVERY_DOC["authorization_endpoint"]

    def test_get_revocation_endpoint_from_discovery(self):
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _DEV_ISSUER}):
                ep = _get_revocation_endpoint()
        assert ep == _SAMPLE_DISCOVERY_DOC["revocation_endpoint"]

    # ── Default-issuer short-circuit (no HTTP) ────────────────────────────────

    def test_default_issuer_explicit_skips_discovery(self):
        """Passing DEFAULT_ISSUER explicitly must NOT trigger oidc_discover."""
        with patch("lib.token_checkin.requests.get") as mock_get:
            ep = _get_token_endpoint(issuer=DEFAULT_ISSUER)
        mock_get.assert_not_called()
        assert "token" in ep

    def test_default_issuer_explicit_device_skips_discovery(self):
        with patch("lib.token_checkin.requests.get") as mock_get:
            ep = _get_device_endpoint(issuer=DEFAULT_ISSUER)
        mock_get.assert_not_called()
        assert "device" in ep

    def test_default_issuer_explicit_auth_skips_discovery(self):
        with patch("lib.token_checkin.requests.get") as mock_get:
            ep = _get_auth_endpoint(issuer=DEFAULT_ISSUER)
        mock_get.assert_not_called()
        assert "auth" in ep

    def test_default_issuer_explicit_revocation_skips_discovery(self):
        with patch("lib.token_checkin.requests.get") as mock_get:
            ep = _get_revocation_endpoint(issuer=DEFAULT_ISSUER)
        mock_get.assert_not_called()
        assert "revoke" in ep

    def test_non_default_issuer_explicit_triggers_discovery(self):
        """Passing a non-default issuer explicitly MUST call oidc_discover."""
        mock_resp = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_resp) as mock_get:
            _get_token_endpoint(issuer=_DEV_ISSUER)
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# issuer param on start_device_flow, build_auth_code_url, exchange_code_for_tokens
# ---------------------------------------------------------------------------


class TestIssuerParamOnHighLevelFunctions:
    """The issuer param is forwarded from the high-level API functions to
    the internal _get_*_endpoint() helpers."""

    def test_start_device_flow_forwards_issuer_to_discovery(self):
        """start_device_flow(issuer=DEV_ISSUER) must call the dev device endpoint."""
        mock_get = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        mock_post = _mock_response(200, {"device_code": "dc", "interval": 5})
        with patch("lib.token_checkin.requests.get", return_value=mock_get):
            with patch("lib.token_checkin.requests.post", return_value=mock_post):
                result = start_device_flow(
                    "my-client", "openid", issuer=_DEV_ISSUER
                )
        assert result["device_code"] == "dc"

    def test_start_device_flow_default_issuer_skips_discovery(self):
        """start_device_flow without issuer (or DEFAULT_ISSUER) must not call GET."""
        mock_post = _mock_response(200, {"device_code": "dc", "interval": 5})
        with patch("lib.token_checkin.requests.post", return_value=mock_post):
            with patch("lib.token_checkin.requests.get") as mock_get:
                start_device_flow("oidc-agent", "openid", issuer=DEFAULT_ISSUER)
        mock_get.assert_not_called()

    def test_build_auth_code_url_with_dev_issuer(self):
        """build_auth_code_url(issuer=DEV_ISSUER) must embed the dev auth endpoint."""
        mock_get = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        with patch("lib.token_checkin.requests.get", return_value=mock_get):
            url = build_auth_code_url(
                client_id="c",
                redirect_uri="http://cb",
                scope="openid",
                state="s",
                code_challenge="ch",
                issuer=_DEV_ISSUER,
            )
        assert "aai-dev.egi.eu" in url

    def test_exchange_code_for_tokens_with_dev_issuer_hits_dev_token_endpoint(self):
        """exchange_code_for_tokens(issuer=DEV_ISSUER) posts to the dev token endpoint."""
        mock_get = _mock_response(200, _SAMPLE_DISCOVERY_DOC)
        mock_post = _mock_response(200, {"access_token": "at"})
        with patch("lib.token_checkin.requests.get", return_value=mock_get):
            with patch(
                "lib.token_checkin.requests.post", return_value=mock_post
            ) as mock_post_call:
                exchange_code_for_tokens(
                    code="c",
                    client_id="cl",
                    redirect_uri="http://cb",
                    code_verifier="v",
                    issuer=_DEV_ISSUER,
                )

        called_url = mock_post_call.call_args.kwargs.get("url") or mock_post_call.call_args[0][0]
        assert called_url == _SAMPLE_DISCOVERY_DOC["token_endpoint"]
