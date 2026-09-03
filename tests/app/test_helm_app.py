"""
tests/app/test_helm_app.py — GUI-layer tests for app.helm routes.

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real session.
- ``app.helm.api_get`` / ``app.helm.api_post`` to avoid real HTTP calls (patched in
  ``app.helm``'s namespace because they are imported via ``from app.api_client import …``).
- ``app.helm.list_configs`` to avoid touching the filesystem.

No real Helm binary, Kubernetes cluster, or EGI Check-in token is required.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

GET_SESSION_USER_PATCH = "app.auth.get_session_user"
# Patch the names as they exist in app.helm's own namespace (imported via `from`)
API_GET_PATCH = "app.helm.api_get"
API_POST_PATCH = "app.helm.api_post"
API_DELETE_PATCH = "app.helm.api_delete"
LIST_HPC_NODES_PATCH = "app.helm.list_hpc_nodes"

FAKE_NAMESPACE = "user-testnamespace1234"
FAKE_USER = {"sub": "test-sub", "namespace": FAKE_NAMESPACE, "exp": 9999999999, "iss": "https://aai.egi.eu"}
FAKE_HPC_NODES = [
    {"name": "test-echo", "hostname": "161.9.255.206", "ssh_port": 22, "plugin": "echo"},
]


def _logged_in_client(client):
    """
    Return the Flask test client with the namespace injected into the session.

    The session write happens inside the application context so that
    ``session["namespace"]`` is available to the route handlers.
    """
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
    exc = requests.HTTPError(response=response)
    return exc


# ---------------------------------------------------------------------------
# GET /releases
# ---------------------------------------------------------------------------


class TestReleasesPage:
    URL = "/releases"

    def test_redirects_when_not_logged_in(self, client):
        """Unauthenticated requests should be redirected to /login."""
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_no_release_deployed_shows_empty_state(self, client, app):
        """
        When /api/interlink returns 404 (no release deployed), the page should
        render successfully with an empty releases list and NO error message.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=_http_error(404)),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No Helm releases" in html
        assert "Cannot list" not in html
        assert "alert-error" not in html

    def test_release_deployed_shows_table_row(self, client):
        """
        When /api/interlink returns success, the releases table should show
        the interlink release row.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, return_value={"success": True, "values_yaml": "nodeName: vk\n"}),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "interlink-test-echo" in html
        assert "deployed" in html

    def test_api_server_error_shows_error_message(self, client):
        """
        When /api/interlink returns a 500, an error message should be displayed.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=_http_error(500)),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Cannot list Helm releases" in html

    def test_deploy_a_chart_link_points_to_helm_deploy(self, client):
        """
        The 'Deploy a Chart' button in the empty-state should link to /helm,
        not back to /releases.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=_http_error(404)),
        ):
            resp = client.get(self.URL)

        assert b'href="/helm"' in resp.data


# ---------------------------------------------------------------------------
# GET /helm  (deploy-a-chart form)
# ---------------------------------------------------------------------------


class TestHelmDeployPage:
    URL = "/helm"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_deploy_form(self, client):
        """GET /helm should render the helm.html deploy form."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Deploy InterLink" in html
        assert 'action="/helm/install"' in html

    def test_renders_hpc_nodes_when_present(self, client):
        """Configured HPC nodes should be listed in the form's dropdown."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        assert b"test-echo" in resp.data


# ---------------------------------------------------------------------------
# POST /helm/install
# ---------------------------------------------------------------------------


class TestHelmInstallRoute:
    URL = "/helm/install"
    FORM_DATA = {"hpc_name": "test-echo"}

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self.FORM_DATA)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={}, follow_redirects=False)
        assert resp.status_code == 302

    def test_successful_install_renders_success_page(self, client):
        """A successful API call should render helm_result.html with success."""
        _logged_in_client(client)
        api_result = {"success": True, "output": "Release installed\n", "error": None}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=api_result),
        ):
            resp = client.post(self.URL, data=self.FORM_DATA)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Chart Installed" in html
        assert "interlink-test-echo" in html

    def test_failed_install_renders_failure_page(self, client):
        """When the API returns success=False, the failure page should be rendered."""
        _logged_in_client(client)
        api_result = {"success": False, "output": "", "error": "already installed"}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=api_result),
        ):
            resp = client.post(self.URL, data=self.FORM_DATA)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Installation Failed" in html
        assert "already installed" in html

    def test_api_http_error_renders_failure_page(self, client):
        """An HTTPError from the API should render the failure page gracefully."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=_http_error(400)),
        ):
            resp = client.post(self.URL, data=self.FORM_DATA)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Installation Failed" in html

    def test_unexpected_exception_renders_failure_page(self, client):
        """Any unexpected exception should still render the failure page."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=RuntimeError("connection refused")),
        ):
            resp = client.post(self.URL, data=self.FORM_DATA)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Installation Failed" in html
        assert "connection refused" in html

    def test_install_uses_long_timeout(self, client):
        """``helm install --wait`` can take minutes; the API call must use LONG_TIMEOUT."""
        from app.api_client import LONG_TIMEOUT

        _logged_in_client(client)
        captured = {}

        def fake_api_post(url, body=None, timeout=None):
            captured["_timeout"] = timeout
            return {"success": True, "output": "", "error": None}

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=fake_api_post),
        ):
            client.post(self.URL, data=self.FORM_DATA)

        assert captured["_timeout"] == LONG_TIMEOUT
