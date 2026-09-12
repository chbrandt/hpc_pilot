"""
tests/app/test_k8s_app.py — GUI-layer tests for app.k8s routes.

Focuses on the Jobs page (``GET /jobs``), which lists the user's container
jobs only — InterLink nodes/releases are managed on the "Manage Nodes" page
(``GET /hpc/nodes``, see test_hpc_app.py), not shown here as jobs.

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

FAKE_NAMESPACE = "user-testnamespace1234"
FAKE_USER = {
    "sub": "test-sub",
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}


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


FAKE_HPC_NODES = [
    {"name": "test-echo", "hostname": "1.2.3.4", "ssh_port": 22, "plugin": "echo"},
]


# ---------------------------------------------------------------------------
# GET /  (Home: overview of jobs + interlink/HPC nodes)
# ---------------------------------------------------------------------------


class TestHomePage:
    URL = "/"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_jobs_and_nodes(self, client):
        """The home page lists both jobs and interlink/HPC nodes."""
        jobs = [_job(name="alpha"), _job(name="beta")]
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("lib.hpc_config.list_hpc_nodes", return_value=FAKE_HPC_NODES),
            patch(
                API_GET_PATCH,
                side_effect=[
                    jobs,                                  # GET /api/jobs
                    {"success": True},                     # GET /api/interlink (deployed)
                ],
            ),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Home" in html
        assert "alpha" in html
        assert "beta" in html
        assert "test-echo" in html
        assert "deployed" in html

    def test_empty_state_renders_without_error(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("lib.hpc_config.list_hpc_nodes", return_value=[]),
            patch(API_GET_PATCH, return_value=[]),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No jobs" in html
        assert "No HPC nodes" in html
        assert "alert-error" not in html


# ---------------------------------------------------------------------------
# GET /submit  (Job submission form)
# ---------------------------------------------------------------------------


class TestSubmitForm:
    URL = "/submit"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_submit_form(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value={"nodes": ["vk-node-1"]}),
            patch(
                "api.site_config.load_site_config",
                return_value={"default_image": "alpine:3.19"},
            ),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Submit a Job" in html
        assert "vk-node-1" in html
        # GA7: the configured default image is used as the placeholder
        assert 'placeholder="alpine:3.19"' in html

    def test_resources_fields_present(self, client):
        """GA2: the form must include optional CPU and memory fields."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value={"nodes": ["vk-node-1"]}),
            patch("api.site_config.load_site_config", return_value={}),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'name="cpu"' in html
        assert 'name="memory"' in html


# ---------------------------------------------------------------------------
# POST /submit  (job submission)
# ---------------------------------------------------------------------------


class TestSubmitJob:
    URL = "/submit"
    API_POST_PATCH = "app.k8s.api_post"

    FORM = {
        "name": "my-job",
        "image": "ubuntu:22.04",
        "node_name": "vk-node-1",
    }

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self.FORM)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_forwards_cpu_and_memory_to_api(self, client):
        """GA2: cpu/memory form fields must be forwarded to POST /api/jobs/preset."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(self.API_POST_PATCH, return_value={"success": True, "job_name": "my-job"}) as mock_post,
        ):
            resp = client.post(
                self.URL, data={**self.FORM, "cpu": "2", "memory": "4Gi"}
            )

        assert resp.status_code == 200
        call_kwargs = mock_post.call_args
        body = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("json")
        assert body["cpu"] == "2"
        assert body["memory"] == "4Gi"

    def test_omits_cpu_memory_when_blank(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(self.API_POST_PATCH, return_value={"success": True, "job_name": "my-job"}) as mock_post,
        ):
            resp = client.post(self.URL, data=self.FORM)

        assert resp.status_code == 200
        call_kwargs = mock_post.call_args
        body = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("json")
        assert body["cpu"] is None
        assert body["memory"] is None

    def test_missing_name_redirects(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={"image": "ubuntu:22.04"}, follow_redirects=False)
        assert resp.status_code == 302


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

    def test_no_jobs_renders_empty_state_without_error(self, client, app):
        """With no jobs, the page renders the empty state and NO error."""
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=[]),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No jobs in your namespace yet" in html
        assert "alert-error" not in html
        assert "interlink" not in html

    def test_jobs_listed_without_error(self, client, app):
        """Jobs are listed; interlink nodes/releases are NOT shown as jobs."""
        jobs = [_job(name="alpha"), _job(name="beta")]
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=jobs),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha" in html
        assert "beta" in html
        assert "alert-error" not in html
        assert "interlink" not in html


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
