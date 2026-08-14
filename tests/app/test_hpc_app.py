"""
tests/app/test_hpc_app.py — GUI-layer tests for app.hpc routes.

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real session.
- ``app.hpc.api_post`` to avoid real HTTP calls (patched in ``app.hpc``'s namespace
  because it is imported via ``from app.api_client import …``).
- ``app.hpc.load_site_config`` to avoid reading site_config.yaml.
- ``app.hpc.list_hpc_nodes`` to avoid reading HPC config files.

No real mccli, SSH, or HPC nodes are required.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

GET_SESSION_USER_PATCH = "app.auth.get_session_user"
API_POST_PATCH = "app.hpc.api_post"
LOAD_SITE_CONFIG_PATCH = "app.hpc.load_site_config"
LIST_HPC_NODES_PATCH = "app.hpc.list_hpc_nodes"

FAKE_NAMESPACE = "user-testhpcnamespace1234"
FAKE_USER = {
    "sub": "test-sub",
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}
FAKE_SITE_CFG = {
    "hostname": "test.local",
    "wstunnel": {"port": 80, "local_port": 4000},
}
FAKE_HPC_NODES = [
    {
        "name": "test-echo",
        "hostname": "161.9.255.206",
        "ssh_port": 3333,
        "plugin": "echo",
    },
    {
        "name": "test-docker",
        "hostname": "161.9.255.233",
        "ssh_port": 22,
        "plugin": "docker",
    },
]

_SUCCESS = {"success": True, "output": "ok", "error": ""}
_FAILURE = {"success": False, "output": "", "error": "connection refused"}


def _logged_in_client(client):
    """Inject a fake session so require_login passes."""
    with client.session_transaction() as sess:
        sess["namespace"] = FAKE_NAMESPACE
        sess["token"] = "fake-token"
        sess["claims"] = {"sub": "test-sub", "exp": 9999999999}
    return client


def _http_error(status_code: int) -> requests.HTTPError:
    """Build a minimal requests.HTTPError with the given status code."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"error": f"HTTP {status_code}"}
    return requests.HTTPError(response=response)


# ---------------------------------------------------------------------------
# GET /hpc/  (HPC deployment form)
# ---------------------------------------------------------------------------


class TestHpcPage:
    URL = "/hpc/"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_form_when_logged_in(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Deploy to HPC" in html
        assert "hpc_name" in html

    def test_hpc_node_dropdown_rendered(self, client):
        """The HPC node <select> element must be present in the form."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.get(self.URL)

        assert b'name="hpc_name"' in resp.data
        assert b"test-echo" in resp.data
        assert b"test-docker" in resp.data


# ---------------------------------------------------------------------------
# POST /hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcDeploy:
    URL = "/hpc/deploy"
    FORM = {
        "hpc_name": "test-echo",
    }

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self.FORM)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        _logged_in_client(client)
        form = {**self.FORM, "hpc_name": ""}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.post(self.URL, data=form, follow_redirects=False)
        assert resp.status_code == 302

    def test_successful_deploy_renders_result(self, client):
        """A successful deploy should render hpc_result.html with success."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "deploy" in html.lower()

    def test_failed_deploy_renders_result(self, client):
        """A failed deploy should render the failure page gracefully."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, return_value=_FAILURE),
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200

    def test_api_http_error_renders_failure(self, client):
        """An HTTPError from the API should render the failure page gracefully."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, side_effect=_http_error(500)),
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200

    def test_hpc_name_forwarded_to_api(self, client):
        """The hpc_name must be included in the API payload."""
        _logged_in_client(client)
        captured = {}

        def fake_api_post(url, payload):
            captured.update(payload)
            return _SUCCESS

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, side_effect=fake_api_post),
        ):
            client.post(self.URL, data={**self.FORM, "hpc_name": "test-docker"})

        assert captured.get("hpc_name") == "test-docker"


# ---------------------------------------------------------------------------
# POST /hpc/status
# ---------------------------------------------------------------------------


class TestHpcStatus:
    URL = "/hpc/status"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={"hpc_name": ""}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200

    def test_failure_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_FAILURE),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /hpc/start  and  POST /hpc/stop
# ---------------------------------------------------------------------------


class TestHpcStartStop:
    def _post(self, client, url, hpc_name="test-echo"):
        return client.post(url, data={"hpc_name": hpc_name})

    def test_start_redirects_when_not_logged_in(self, client):
        resp = self._post(client, "/hpc/start")
        assert resp.status_code == 302

    def test_stop_redirects_when_not_logged_in(self, client):
        resp = self._post(client, "/hpc/stop")
        assert resp.status_code == 302

    def test_start_success_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = self._post(client, "/hpc/start")
        assert resp.status_code == 200

    def test_stop_success_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = self._post(client, "/hpc/stop")
        assert resp.status_code == 200
