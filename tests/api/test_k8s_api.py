"""
tests/api/test_k8s_api.py — Integration tests for api.k8s endpoints.

Uses the Flask test client from conftest + patches K8sClient so no real
Kubernetes cluster is needed.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

K8S_PATCH = "api.k8s._get_k8s"


def _mock_k8s(**kwargs):
    """Return a MagicMock that mimics K8sClient with sensible defaults."""
    m = MagicMock()
    m.namespace_exists.return_value = kwargs.get("namespace_exists", False)
    m.create_namespace.return_value = kwargs.get(
        "create_namespace", {"success": True, "namespace": "user-testns"}
    )
    m.list_interlink_nodes.return_value = kwargs.get(
        "list_interlink_nodes", ["vk-node"]
    )
    m.list_jobs.return_value = kwargs.get("list_jobs", [])
    m.create_job.return_value = kwargs.get(
        "create_job",
        {"success": True, "job_name": "myapp", "namespace": "user-testns"},
    )
    m.get_job_spec.return_value = kwargs.get(
        "get_job_spec",
        {"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"},
    )
    m.get_job_status.return_value = kwargs.get(
        "get_job_status",
        {"name": "myapp", "status": "available"},
    )
    m.delete_job.return_value = kwargs.get(
        "delete_job",
        {"job": {"success": True, "name": "myapp"}},
    )
    m.get_job_output.return_value = kwargs.get(
        "get_job_output",
        {"name": "myapp", "pod": "myapp-abc123", "content": "output text\n"},
    )
    return m


# ---------------------------------------------------------------------------
# POST /api/namespaces/ensure
# ---------------------------------------------------------------------------


class TestEnsureNamespace:
    URL = "/api/namespaces/ensure"

    def test_requires_auth(self, client):
        resp = client.post(self.URL)
        assert resp.status_code == 401

    def test_namespace_already_exists_returns_200_created_false(self, client, auth_headers):
        headers, ns = auth_headers
        k8s = _mock_k8s(namespace_exists=True)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["created"] is False
        assert data["namespace"] == ns

    def test_new_namespace_returns_201_created_true(self, client, auth_headers):
        headers, ns = auth_headers
        k8s = _mock_k8s(
            namespace_exists=False,
            create_namespace={"success": True, "namespace": ns},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 201
        data = resp.get_json(force=True)
        assert data["created"] is True

    def test_create_namespace_failure_returns_500(self, client, auth_headers):
        headers, ns = auth_headers
        k8s = _mock_k8s(
            namespace_exists=False,
            create_namespace={"success": False, "error": "quota exceeded"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/nodes/interlink
# ---------------------------------------------------------------------------


class TestListInterlinkNodes:
    URL = "/api/nodes/interlink"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_returns_node_list(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(list_interlink_nodes=["vk-node-a", "vk-node-b"])
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["nodes"] == ["vk-node-a", "vk-node-b"]

    def test_returns_empty_list_when_no_nodes(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(list_interlink_nodes=[])
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        assert resp.get_json(force=True)["nodes"] == []

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = MagicMock()
        k8s.list_interlink_nodes.side_effect = RuntimeError("k8s down")
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    URL = "/api/jobs"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_returns_job_list(self, client, auth_headers):
        headers, ns = auth_headers
        job_list = [{"name": "app1", "namespace": ns, "status": "available",
                     "node_name": "vk-node"}]
        k8s = _mock_k8s(list_jobs=job_list)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert isinstance(data, list)
        assert data[0]["name"] == "app1"

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = MagicMock()
        k8s.list_jobs.side_effect = RuntimeError("k8s down")
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/jobs
# ---------------------------------------------------------------------------


class TestCreateJob:
    URL = "/api/jobs"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={"image": "ubuntu:22.04"}, headers=headers)
        assert resp.status_code == 400
        assert "name" in resp.get_json(force=True)["error"].lower()

    def test_missing_image_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={"name": "myapp"}, headers=headers)
        assert resp.status_code == 400
        assert "image" in resp.get_json(force=True)["error"].lower()

    def test_missing_node_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(
            self.URL,
            json={"name": "myapp", "image": "ubuntu:22.04"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "node_name" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_201(self, client, auth_headers):
        headers, ns = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_job={"success": True, "job_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"},
                headers=headers,
            )
        assert resp.status_code == 201

    def test_response_contains_job_name(self, client, auth_headers):
        """Response must use 'job_name', not 'deployment_name'."""
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_job={"success": True, "job_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"},
                headers=headers,
            )
        data = resp.get_json(force=True)
        assert "job_name" in data
        assert "deployment_name" not in data

    def test_node_name_is_forwarded_to_k8s_client(self, client, auth_headers):
        """node_name must be passed through to K8sClient.create_job."""
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_job={"success": True, "job_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            client.post(
                self.URL,
                json={"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"},
                headers=headers,
            )
        call_kwargs = k8s.create_job.call_args
        assert call_kwargs[1].get("node_name") == "vk-node"

    def test_unsupported_fields_are_ignored(self, client, auth_headers):
        """Sending replicas/ports/resources must not cause an error — they're silently ignored."""
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_job={"success": True, "job_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={
                    "name": "myapp",
                    "image": "ubuntu:22.04",
                    "node_name": "vk-node",
                    "replicas": 3,
                    "cpu_request": "100m",
                    "ports": [{"number": 80}],
                    "ingress": {"host": "example.com"},
                },
                headers=headers,
            )
        assert resp.status_code == 201
        # replicas/ports/resources must NOT be forwarded to K8sClient
        call_kwargs = k8s.create_job.call_args
        for unsupported in ("replicas", "cpu_request", "ports", "ingress"):
            assert unsupported not in (call_kwargs[1] or {}), \
                f"'{unsupported}' must not be forwarded to K8sClient"

    def test_k8s_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_job={"success": False, "error": "already exists"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"},
                headers=headers,
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/jobs/<name>
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_requires_auth(self, client):
        assert client.get("/api/jobs/myapp").status_code == 401

    def test_found_returns_200_with_spec(self, client, auth_headers):
        headers, _ = auth_headers
        spec = {"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"}
        k8s = _mock_k8s(get_job_spec=spec)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/myapp", headers=headers)
        assert resp.status_code == 200
        assert resp.get_json(force=True)["name"] == "myapp"

    def test_spec_does_not_contain_removed_fields(self, client, auth_headers):
        headers, _ = auth_headers
        spec = {"name": "myapp", "image": "ubuntu:22.04", "node_name": "vk-node"}
        k8s = _mock_k8s(get_job_spec=spec)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/myapp", headers=headers)
        data = resp.get_json(force=True)
        for removed in ("replicas", "cpu_request", "cpu_limit",
                        "mem_request", "mem_limit", "ports"):
            assert removed not in data

    def test_not_found_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_job_spec={"error": "not found"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/missing", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/jobs/<name>/status
# ---------------------------------------------------------------------------


class TestJobStatus:
    def test_requires_auth(self, client):
        assert client.get("/api/jobs/myapp/status").status_code == 401

    def test_found_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_job_status={"name": "myapp", "status": "available"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/myapp/status", headers=headers)
        assert resp.status_code == 200

    def test_error_key_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_job_status={"error": "not found"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/missing/status", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/jobs/<name>
# ---------------------------------------------------------------------------


class TestDeleteJob:
    def test_requires_auth(self, client):
        assert client.delete("/api/jobs/myapp").status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            delete_job={"job": {"success": True, "name": "myapp"}}
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.delete("/api/jobs/myapp", headers=headers)
        assert resp.status_code == 200

    def test_response_uses_job_key_not_deployment_key(self, client, auth_headers):
        """Response must use 'job', not 'deployment'."""
        headers, _ = auth_headers
        k8s = _mock_k8s(
            delete_job={"job": {"success": True, "name": "myapp"}}
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.delete("/api/jobs/myapp", headers=headers)
        data = resp.get_json(force=True)
        assert "job" in data
        assert "deployment" not in data
        assert "service" not in data
        assert "ingress" not in data

    def test_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            delete_job={
                "job": {"success": False, "error": "not found"},
            }
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.delete("/api/jobs/myapp", headers=headers)
        assert resp.status_code == 400
# ---------------------------------------------------------------------------
# GET /api/jobs/<name>/output
# ---------------------------------------------------------------------------


class TestJobOutput:
    def test_requires_auth(self, client):
        assert client.get("/api/jobs/myapp/output").status_code == 401

    def test_found_returns_200_with_content(self, client, auth_headers):
        headers, _ = auth_headers
        output = {"name": "myapp", "pod": "myapp-abc123", "content": "log output\n"}
        k8s = _mock_k8s(get_job_output=output)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/myapp/output", headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["name"] == "myapp"
        assert data["content"] == "log output\n"
        assert "pod" in data

    def test_error_key_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_job_output={"error": "not found"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/jobs/missing/output", headers=headers)
        assert resp.status_code == 404
