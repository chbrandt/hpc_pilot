"""
tests/app/test_k8s_app.py — GUI-layer tests for app.k8s routes.

Focuses on the Jobs page (``GET /jobs``), which merges container jobs with the
InterLink Helm release.  In particular, it verifies that a 404 from
``GET /api/interlink`` (the normal "no release deployed yet" state) is *not*
rendered as an error — mirroring the behaviour already tested for the
Releases page in ``test_helm_app.py``.

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real session.
- ``app.k8s.api_get`` to avoid real HTTP calls (patched in ``app.k8s``'s own
  namespace because it is imported via ``from app.api_client import api_get``).

No real Helm binary, Kubernetes cluster, or EGI Check-in token is required.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

GET_SESSION_USER_PATCH = "app.auth.get_session_user"
# Patch the names as they exist in app.k8s's own namespace (imported via `from`)
API_GET_PATCH = "app.k8s.api_get"
LIST_HPC_NODES_PATCH = "lib.hpc_config.list_hpc_nodes"

FAKE_NAMESPACE = "user-testnamespace1234"
FAKE_USER = {
    "sub": "test-sub",
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}
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


def _job(name="my-job", image="ubuntu:22.04", node_name="vk-1", status="running"):
    """A minimal job dict as returned by ``GET /api/jobs``."""
    return {
        "name": name,
        "namespace": FAKE_NAMESPACE,
        "image": image,
        "node_name": node_name,
        "status": status,
        "created": "2025-01-01 00:00:00",
    }


# ---------------------------------------------------------------------------
# GET /jobs
# ---------------------------------------------------------------------------


class TestJobsPage:
    URL = "/jobs"

    def test_redirects_when_not_logged_in(self, client):
        """Unauthenticated requests should be redirected to /login."""
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_interlink_404_renders_empty_state_without_error(self, client, app):
        """
        A 404 from /api/interlink means no InterLink release is deployed yet —
        a perfectly normal state.  The page must render with the empty-state
        message and NO error banner.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=[[], _http_error(404)]),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No jobs in your namespace yet" in html
        assert "alert-error" not in html
        assert "Helm releases" not in html
        assert "interlink" not in html

    def test_interlink_404_with_jobs_shows_jobs_without_error(self, client, app):
        """
        When container jobs exist but the InterLink release isn't deployed,
        the jobs must be listed and the 404 must NOT surface as an error.
        """
        jobs = [_job(name="alpha"), _job(name="beta")]
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=[jobs, _http_error(404)]),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha" in html
        assert "beta" in html
        assert "alert-error" not in html
        assert "Helm releases" not in html

    def test_interlink_deployed_shows_helm_row(self, client, app):
        """
        When /api/interlink returns success, the InterLink Helm release must
        appear in the workloads table with status 'deployed'.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(
                API_GET_PATCH,
                side_effect=[[], {"success": True, "values_yaml": "nodeName: vk\n"}],
            ),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "interlink-test-echo" in html
        assert "deployed" in html

    def test_interlink_500_shows_error_message(self, client, app):
        """
        A real failure (e.g. 500 when the helm CLI / cluster is unreachable)
        must still be surfaced to the user as an error.
        """
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(LIST_HPC_NODES_PATCH, return_value=FAKE_HPC_NODES),
            patch(API_GET_PATCH, side_effect=[[], _http_error(500)]),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alert-error" in html
        assert "Helm releases" in html


# ---------------------------------------------------------------------------
# GET /jobs/<ns>/<name>/output
# ---------------------------------------------------------------------------


class TestJobOutputPage:

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_wrong_namespace_redirects(self, client, app):
        """Cross-user namespace attempts must redirect to /jobs."""
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get("/jobs/other-namespace/my-job/output")
        assert resp.status_code == 302
        assert "/jobs" in resp.headers["Location"]

    def test_renders_output_content(self, client, app):
        """A successful API call shows the output content and pod name."""
        _logged_in_client(client)
        api_response = {
            "name": "my-job",
            "pod": "my-job-abc123",
            "content": "Submitted to SLURM node vnode-1.\nHello world!\n",
        }
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=api_response),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Hello world!" in html
        assert "my-job-abc123" in html
        assert "Submitted to SLURM" in html

    def test_api_error_renders_error_card(self, client, app):
        """An API failure (e.g. 404) renders an error card, not a crash."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=_http_error(404)),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Could not retrieve job output" in html
        assert "HTTP 404" in html
