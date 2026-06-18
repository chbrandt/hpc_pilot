"""
tests/app/test_hpc_app.py — GUI-layer tests for app.hpc routes.

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real session.
- ``app.hpc.api_post`` to avoid real HTTP calls (patched in ``app.hpc``'s namespace
  because it is imported via ``from app.api_client import …``).
- ``app.hpc.list_configs`` / ``app.hpc.save_config`` to avoid touching the filesystem.
- ``app.hpc.load_site_config`` to avoid reading site_config.yaml.

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
LIST_CONFIGS_PATCH = "app.hpc.list_configs"
SAVE_CONFIG_PATCH = "app.hpc.save_config"
LOAD_SITE_CONFIG_PATCH = "app.hpc.load_site_config"

FAKE_NAMESPACE = "user-testhpcnamespace1234"
FAKE_USER = {
    "sub": "test-sub",
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}
FAKE_SITE_CFG = {
    "cluster_domain": "test.local",
    "wstunnel": {"port": 80, "local_port": 4000},
}

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
            patch(LIST_CONFIGS_PATCH, return_value=[]),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Deploy to HPC" in html
        assert "hpc_host" in html

    def test_plugin_select_rendered(self, client):
        """The plugin <select> element must be present in the form."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_CONFIGS_PATCH, return_value=[]),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.get(self.URL)

        assert b'name="plugin"' in resp.data
        assert b"echo" in resp.data
        assert b"slurm" in resp.data
        assert b"docker" in resp.data

    def test_saved_configs_shown(self, client):
        """Saved HPC configs should appear in the page."""
        saved = [
            {
                "id": "abc123",
                "kind": "hpc",
                "label": "My Cluster",
                "hpc_host": "login.myhpc.org",
                "ssh_port": 22,
                "plugin": "echo",
                "saved_at": "2026-06-01T00:00:00",
            }
        ]
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_CONFIGS_PATCH, return_value=saved),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        assert b"My Cluster" in resp.data
        assert b"login.myhpc.org" in resp.data


# ---------------------------------------------------------------------------
# POST /hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcDeploy:
    URL = "/hpc/deploy"
    FORM = {
        "hpc_host": "login.myhpc.example.org",
        "ssh_port": "22",
        "wstunnel_server": "user-ns.test.local",
        "wstunnel_port": "80",
        "wstunnel_secret": "mysecret",
        "wstunnel_local_port": "4000",
        "label": "My HPC",
        "plugin": "echo",
    }

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self.FORM)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_host_redirects(self, client):
        _logged_in_client(client)
        form = {**self.FORM, "hpc_host": ""}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
        ):
            resp = client.post(self.URL, data=form, follow_redirects=False)
        assert resp.status_code == 302

    def test_invalid_plugin_redirects(self, client):
        _logged_in_client(client)
        form = {**self.FORM, "plugin": "badplugin"}
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
            patch(SAVE_CONFIG_PATCH),
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "deploy" in html.lower()

    def test_successful_deploy_auto_saves_hpc_side_fields_only(self, client):
        """On success, save_config must be called with HPC-side fields only."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, return_value=_SUCCESS),
            patch(SAVE_CONFIG_PATCH) as mock_save,
        ):
            client.post(self.URL, data=self.FORM)

        assert mock_save.called
        call_kwargs = mock_save.call_args
        # config arg must contain plugin
        saved_config = call_kwargs.kwargs.get("config") or call_kwargs.args[2]
        assert "plugin" in saved_config
        assert saved_config["plugin"] == "echo"
        # wstunnel_server must NOT be persisted in the HPC config
        assert "wstunnel_server" not in saved_config
        assert "wstunnel_secret" not in saved_config

    def test_failed_deploy_does_not_auto_save(self, client):
        """When the deploy fails, save_config must not be called."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, return_value=_FAILURE),
            patch(SAVE_CONFIG_PATCH) as mock_save,
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200
        mock_save.assert_not_called()

    def test_api_http_error_renders_failure(self, client):
        """An HTTPError from the API should render the failure page gracefully."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, side_effect=_http_error(500)),
            patch(SAVE_CONFIG_PATCH),
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200

    def test_plugin_forwarded_to_api(self, client):
        """The plugin choice must be included in the API payload."""
        _logged_in_client(client)
        captured = {}

        def fake_api_post(url, payload):
            captured.update(payload)
            return _SUCCESS

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LOAD_SITE_CONFIG_PATCH, return_value=FAKE_SITE_CFG),
            patch(API_POST_PATCH, side_effect=fake_api_post),
            patch(SAVE_CONFIG_PATCH),
        ):
            client.post(self.URL, data={**self.FORM, "plugin": "slurm"})

        assert captured.get("plugin") == "slurm"


# ---------------------------------------------------------------------------
# POST /hpc/status
# ---------------------------------------------------------------------------


class TestHpcStatus:
    URL = "/hpc/status"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_host": "hpc.example.org"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_host_redirects(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={"hpc_host": ""}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_host": "hpc.example.org", "ssh_port": "22"})
        assert resp.status_code == 200

    def test_failure_renders_result(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_FAILURE),
        ):
            resp = client.post(self.URL, data={"hpc_host": "hpc.example.org"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /hpc/start  and  POST /hpc/stop
# ---------------------------------------------------------------------------


class TestHpcStartStop:
    def _post(self, client, url, hpc_host="hpc.example.org"):
        return client.post(url, data={"hpc_host": hpc_host, "ssh_port": "22"})

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


# ---------------------------------------------------------------------------
# POST /hpc/<config_id>/save
# ---------------------------------------------------------------------------


class TestHpcSave:
    URL = "/hpc/test-id-123/save"
    FORM = {
        "hpc_host": "login.myhpc.org",
        "ssh_port": "22",
        "label": "My Saved HPC",
        "plugin": "slurm",
    }

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self.FORM)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_save_stores_hpc_side_fields(self, client):
        """save_config must be called with label, hpc_host, ssh_port, plugin."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(SAVE_CONFIG_PATCH) as mock_save,
        ):
            client.post(self.URL, data=self.FORM)

        assert mock_save.called
        call_kwargs = mock_save.call_args.kwargs
        saved = call_kwargs.get("config") or mock_save.call_args.args[2]
        assert saved["hpc_host"] == "login.myhpc.org"
        assert saved["ssh_port"] == 22
        assert saved["plugin"] == "slurm"
        assert saved["label"] == "My Saved HPC"
        # wstunnel fields must NOT be stored
        assert "wstunnel_server" not in saved
        assert "wstunnel_secret" not in saved

    def test_save_with_invalid_plugin_redirects(self, client):
        _logged_in_client(client)
        form = {**self.FORM, "plugin": "badplugin"}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(SAVE_CONFIG_PATCH),
        ):
            resp = client.post(self.URL, data=form, follow_redirects=False)
        assert resp.status_code == 302

    def test_successful_save_redirects_to_hpc_page(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(SAVE_CONFIG_PATCH),
        ):
            resp = client.post(self.URL, data=self.FORM, follow_redirects=False)
        assert resp.status_code == 302
        assert "/hpc/" in resp.headers["Location"]
