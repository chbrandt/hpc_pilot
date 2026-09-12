"""
tests/app/test_auth_app.py — GUI-layer tests for app.auth, focusing on the
EGI Check-in OAuth authorization-code login (GA4).

Patches:
- ``app.auth.validate_token`` / ``app.auth.derive_namespace`` /
  ``app.auth.check_group_access`` so ``_establish_session`` passes without
  a real EGI Check-in token.
- ``api.site_config.load_site_config`` to control the (fake) OIDC client
  configuration and empty allowed_groups.
- ``app.api_client.api_post`` (userspace ensure + saved seed).
- ``app.auth.requests.post`` for the OAuth token-exchange call.

No real network access is required.
"""

import time
from unittest.mock import patch

FAKE_CLAIMS = {
    "sub": "test-user-sub-12345678",
    "iss": "https://aai.egi.eu/auth/realms/egi",
    "exp": int(time.time()) + 3600,
    "iat": int(time.time()),
}

FAKE_USER = {
    "sub": "test-user-sub-12345678",
    "namespace": "user-fcbc139581fea03d",
    "exp": 9999999999,
    "iss": "https://aai.egi.eu/auth/realms/egi",
}

OIDC_CONFIG = {
    "oidc": {
        "client_id": "hpc-pilot",
        "client_secret": "",
        "issuer": "https://aai.egi.eu/auth/realms/egi",
    },
}

NO_OIDC_CONFIG = {}

_TOKEN_EXCHANGE_RESPONSE = {
    "access_token": "fake-access-token-from-oauth",
    "token_type": "Bearer",
    "expires_in": 3600,
}

LOAD_SITE_CONFIG_PATCH = "app.auth.load_site_config"
API_POST_PATCH = "app.api_client.api_post"
VALIDATE_TOKEN_PATCH = "app.auth.validate_token"
GROUP_ACCESS_PATCH = "app.auth.check_group_access"
REQUESTS_POST_PATCH = "app.auth.requests.post"


def _logged_in_client(client):
    with client.session_transaction() as sess:
        sess["namespace"] = FAKE_USER["namespace"]
        sess["token"] = "fake-token"
        sess["claims"] = FAKE_CLAIMS
    return client


class TestEgiLoginRedirect:
    URL = "/login/egi"

    def test_redirects_to_login_when_not_configured(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=NO_OIDC_CONFIG):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_checkin_authorize_url(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG):
            resp = client.get(self.URL, follow_redirects=False)

        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/auth" in location
        assert "client_id=hpc-pilot" in location
        assert "response_type=code" in location
        assert "state=" in location
        # The state must be stored in the session for the callback to verify
        with client.session_transaction() as sess:
            assert sess["oidc_state"]

    def test_next_url_stored_in_session(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG):
            client.get(self.URL + "?next=/jobs", follow_redirects=False)
        with client.session_transaction() as sess:
            assert sess["oidc_next"] == "/jobs"


class TestEgiCallback:
    URL = "/login/egi/callback"

    def test_redirects_to_login_when_not_configured(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=NO_OIDC_CONFIG):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_state_mismatch_redirects_to_login(self, client):
        _logged_in_client(client)
        with client.session_transaction() as sess:
            sess["oidc_state"] = "expected-state"
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG):
            resp = client.get(
                self.URL + "?state=wrong-state&code=abc", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_code_redirects_to_login(self, client):
        _logged_in_client(client)
        with client.session_transaction() as sess:
            sess["oidc_state"] = "expected-state"
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG):
            resp = client.get(
                self.URL + "?state=expected-state", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_successful_login_establishes_session(self, client):
        mock_response = type(
            "R",
            (),
            {
                "json": staticmethod(lambda: dict(_TOKEN_EXCHANGE_RESPONSE)),
                "raise_for_status": staticmethod(lambda: None),
            },
        )()
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG),
            patch(VALIDATE_TOKEN_PATCH, return_value=dict(FAKE_CLAIMS)),
            patch(GROUP_ACCESS_PATCH),  # no-op
            patch(API_POST_PATCH, return_value={"created": False}),
            patch(REQUESTS_POST_PATCH, return_value=mock_response) as mock_post,
        ):
            with client.session_transaction() as sess:
                sess["oidc_state"] = "expected-state"
                sess["oidc_next"] = ""

            resp = client.get(
                self.URL + "?state=expected-state&code=auth-code",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        # The exchanged access token must be stored in the session
        with client.session_transaction() as sess:
            assert sess["token"] == "fake-access-token-from-oauth"
            assert sess["namespace"] == FAKE_USER["namespace"]
        # The token exchange must hit the Check-in token endpoint
        url = mock_post.call_args[0][0]
        assert "protocol/openid-connect/token" in url

    def test_token_exchange_failure_redirects_to_login(self, client):
        bad_response = type(
            "R",
            (),
            {
                "raise_for_status": staticmethod(
                    lambda: (_ for _ in ()).throw(RuntimeError("IdP down"))
                )
            },
        )()
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG),
            patch(REQUESTS_POST_PATCH, return_value=bad_response),
        ):
            with client.session_transaction() as sess:
                sess["oidc_state"] = "expected-state"
            resp = client.get(
                self.URL + "?state=expected-state&code=abc", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestLoginPageOidcButton:
    URL = "/login"

    def test_button_hidden_when_oidc_not_configured(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=NO_OIDC_CONFIG):
            resp = client.get(self.URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Log in with EGI Check-in" not in html

    def test_button_shown_when_oidc_configured(self, client):
        with patch(LOAD_SITE_CONFIG_PATCH, return_value=OIDC_CONFIG):
            resp = client.get(self.URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Log in with EGI Check-in" in html
        assert "/login/egi" in html

