"""
tests/api/test_k8s_api.py — Integration tests for api.k8s endpoints.

Uses the Flask test client from conftest + patches K8sClient so no real
Kubernetes cluster is needed.
"""

import json
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
    m.list_deployments.return_value = kwargs.get("list_deployments", [])
    m.create_deployment.return_value = kwargs.get(
        "create_deployment",
        {"success": True, "deployment_name": "myapp", "namespace": "user-testns"},
    )
    m.get_deployment_spec.return_value = kwargs.get(
        "get_deployment_spec",
        {"name": "myapp", "image": "nginx:latest", "node_name": "vk-node", "replicas": 1},
    )
    m.get_deployment_status.return_value = kwargs.get(
        "get_deployment_status",
        {"name": "myapp", "status": "available"},
    )
    m.delete_deployment.return_value = kwargs.get(
        "delete_deployment",
        {"deployment": {"success": True, "name": "myapp"}, "service": None, "ingress": None},
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
# GET /api/deployments
# ---------------------------------------------------------------------------


class TestListDeployments:
    URL = "/api/deployments"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_returns_deployment_list(self, client, auth_headers):
        headers, ns = auth_headers
        dep_list = [{"name": "app1", "namespace": ns, "status": "available"}]
        k8s = _mock_k8s(list_deployments=dep_list)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert isinstance(data, list)
        assert data[0]["name"] == "app1"

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = MagicMock()
        k8s.list_deployments.side_effect = RuntimeError("k8s down")
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/deployments
# ---------------------------------------------------------------------------


class TestCreateDeployment:
    URL = "/api/deployments"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={"image": "nginx"}, headers=headers)
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
            json={"name": "myapp", "image": "nginx:latest"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "node_name" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_201(self, client, auth_headers):
        headers, ns = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_deployment={"success": True, "deployment_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={"name": "myapp", "image": "nginx:latest", "node_name": "vk-node"},
                headers=headers,
            )
        assert resp.status_code == 201

    def test_node_name_is_forwarded_to_k8s_client(self, client, auth_headers):
        """node_name must be passed through to K8sClient.create_deployment."""
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_deployment={"success": True, "deployment_name": "myapp"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            client.post(
                self.URL,
                json={"name": "myapp", "image": "nginx:latest", "node_name": "vk-node"},
                headers=headers,
            )
        call_kwargs = k8s.create_deployment.call_args
        assert call_kwargs[1].get("node_name") == "vk-node"

    def test_k8s_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=True,
            create_deployment={"success": False, "error": "already exists"},
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.post(
                self.URL,
                json={"name": "myapp", "image": "nginx:latest", "node_name": "vk-node"},
                headers=headers,
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/deployments/<name>
# ---------------------------------------------------------------------------


class TestGetDeployment:
    def test_requires_auth(self, client):
        assert client.get("/api/deployments/myapp").status_code == 401

    def test_found_returns_200_with_spec(self, client, auth_headers):
        headers, _ = auth_headers
        spec = {"name": "myapp", "image": "nginx:latest", "replicas": 1}
        k8s = _mock_k8s(get_deployment_spec=spec)
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/deployments/myapp", headers=headers)
        assert resp.status_code == 200
        assert resp.get_json(force=True)["name"] == "myapp"

    def test_not_found_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_deployment_spec={"error": "not found"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/deployments/missing", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/deployments/<name>/status
# ---------------------------------------------------------------------------


class TestDeploymentStatus:
    def test_requires_auth(self, client):
        assert client.get("/api/deployments/myapp/status").status_code == 401

    def test_found_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_deployment_status={"name": "myapp", "status": "available"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/deployments/myapp/status", headers=headers)
        assert resp.status_code == 200

    def test_error_key_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(get_deployment_status={"error": "not found"})
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.get("/api/deployments/missing/status", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/deployments/<name>
# ---------------------------------------------------------------------------


class TestDeleteDeployment:
    def test_requires_auth(self, client):
        assert client.delete("/api/deployments/myapp").status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            delete_deployment={
                "deployment": {"success": True, "name": "myapp"},
                "service": None,
                "ingress": None,
            }
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.delete("/api/deployments/myapp", headers=headers)
        assert resp.status_code == 200

    def test_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            delete_deployment={
                "deployment": {"success": False, "error": "not found"},
                "service": None,
                "ingress": None,
            }
        )
        with patch(K8S_PATCH, return_value=k8s):
            resp = client.delete("/api/deployments/myapp", headers=headers)
        assert resp.status_code == 400
