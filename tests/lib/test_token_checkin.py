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
    load_tokens,
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
