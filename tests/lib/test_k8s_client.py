"""
tests/lib/test_k8s_client.py — Unit tests for lib.k8s_client.K8sClient.

All Kubernetes API calls are mocked via unittest.mock so no real cluster
or kubeconfig is needed.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from kubernetes.client.rest import ApiException

from lib.k8s_client import K8sClient


# ---------------------------------------------------------------------------
# Fixture — instantiate K8sClient with a patched kubeconfig loader
# ---------------------------------------------------------------------------


@pytest.fixture()
def k8s():
    """Return a K8sClient instance with all k8s API objects mocked."""
    with patch("lib.k8s_client.config.load_kube_config"):
        with (
            patch("lib.k8s_client.client.CoreV1Api") as mock_core,
            patch("lib.k8s_client.client.AppsV1Api") as mock_apps,
            patch("lib.k8s_client.client.NetworkingV1Api") as mock_net,
        ):
            c = K8sClient(kubeconfig_path="/fake/kubeconfig")
            c.core_v1 = mock_core.return_value
            c.apps_v1 = mock_apps.return_value
            c.networking_v1 = mock_net.return_value
            yield c


# ---------------------------------------------------------------------------
# Namespace operations
# ---------------------------------------------------------------------------


class TestNamespaceOperations:
    def test_list_namespaces_returns_sorted_names(self, k8s):
        ns_b = MagicMock()
        ns_b.metadata.name = "beta"
        ns_a = MagicMock()
        ns_a.metadata.name = "alpha"
        k8s.core_v1.list_namespace.return_value.items = [ns_b, ns_a]
        result = k8s.list_namespaces()
        assert result == ["alpha", "beta"]

    def test_list_namespaces_returns_default_on_api_exception(self, k8s):
        k8s.core_v1.list_namespace.side_effect = ApiException(status=403)
        result = k8s.list_namespaces()
        assert result == ["default"]

    def test_create_namespace_success(self, k8s):
        created_ns = MagicMock()
        created_ns.metadata.name = "user-ns"
        k8s.core_v1.create_namespace.return_value = created_ns
        result = k8s.create_namespace("user-ns")
        assert result["success"] is True
        assert result["namespace"] == "user-ns"

    def test_create_namespace_already_exists(self, k8s):
        k8s.core_v1.create_namespace.side_effect = ApiException(status=409)
        result = k8s.create_namespace("user-ns")
        assert result["success"] is True
        assert "already exists" in result.get("note", "")

    def test_create_namespace_other_error(self, k8s):
        k8s.core_v1.create_namespace.side_effect = ApiException(status=403)
        result = k8s.create_namespace("user-ns")
        assert result["success"] is False
        assert "error" in result

    def test_namespace_exists_true(self, k8s):
        k8s.core_v1.read_namespace.return_value = MagicMock()
        assert k8s.namespace_exists("user-ns") is True

    def test_namespace_exists_false(self, k8s):
        k8s.core_v1.read_namespace.side_effect = ApiException(status=404)
        assert k8s.namespace_exists("missing-ns") is False


# ---------------------------------------------------------------------------
# Deployment operations
# ---------------------------------------------------------------------------


class TestDeploymentOperations:
    def _make_deployment_mock(self, name: str = "myapp", ns: str = "user-ns"):
        dep = MagicMock()
        dep.metadata.name = name
        dep.metadata.namespace = ns
        dep.metadata.creation_timestamp.strftime.return_value = "2024-01-01 00:00:00"
        dep.spec.replicas = 1
        dep.spec.template.spec.containers = [MagicMock(image="nginx:latest")]
        dep.status.ready_replicas = 1
        dep.status.available_replicas = 1
        dep.status.updated_replicas = 1
        dep.status.conditions = []
        return dep

    def test_create_deployment_success(self, k8s):
        created = self._make_deployment_mock()
        k8s.apps_v1.create_namespaced_deployment.return_value = created
        result = k8s.create_deployment(name="myapp", image="nginx:latest", namespace="user-ns")
        assert result["success"] is True
        assert result["deployment_name"] == "myapp"

    def test_create_deployment_api_exception(self, k8s):
        k8s.apps_v1.create_namespaced_deployment.side_effect = ApiException(
            status=409, reason="AlreadyExists"
        )
        result = k8s.create_deployment(name="myapp", image="nginx:latest", namespace="user-ns")
        assert result["success"] is False
        assert "error" in result

    def test_list_deployments_returns_list(self, k8s):
        dep = self._make_deployment_mock()
        k8s.apps_v1.list_namespaced_deployment.return_value.items = [dep]
        # Make service lookup raise (no svc) — that's fine
        k8s.core_v1.read_namespaced_service.side_effect = ApiException(status=404)
        k8s.networking_v1.read_namespaced_ingress.side_effect = ApiException(status=404)

        result = k8s.list_deployments(namespace="user-ns")
        assert len(result) == 1
        assert result[0]["name"] == "myapp"

    def test_list_deployments_empty_on_api_exception(self, k8s):
        k8s.apps_v1.list_namespaced_deployment.side_effect = ApiException(status=403)
        result = k8s.list_deployments(namespace="user-ns")
        assert result == []

    def test_get_deployment_spec_success(self, k8s):
        dep = self._make_deployment_mock()
        container = dep.spec.template.spec.containers[0]
        container.image = "nginx:latest"
        container.command = None
        container.ports = []
        container.env = []
        container.resources = None
        k8s.apps_v1.read_namespaced_deployment.return_value = dep
        result = k8s.get_deployment_spec("myapp", "user-ns")
        assert result["name"] == "myapp"
        assert result["image"] == "nginx:latest"

    def test_get_deployment_spec_api_exception(self, k8s):
        k8s.apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)
        result = k8s.get_deployment_spec("missing", "user-ns")
        assert "error" in result

    def test_get_deployment_status_success(self, k8s):
        dep = self._make_deployment_mock()
        available_cond = MagicMock()
        available_cond.type = "Available"
        available_cond.status = "True"
        dep.status.conditions = [available_cond]
        k8s.apps_v1.read_namespaced_deployment.return_value = dep
        result = k8s.get_deployment_status("myapp", "user-ns")
        assert result["status"] == "available"
        assert result["name"] == "myapp"

    def test_get_deployment_status_api_exception(self, k8s):
        k8s.apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)
        result = k8s.get_deployment_status("missing", "user-ns")
        assert "error" in result

    def test_delete_deployment_success(self, k8s):
        k8s.apps_v1.delete_namespaced_deployment.return_value = None
        k8s.core_v1.delete_namespaced_service.side_effect = ApiException(status=404)
        k8s.networking_v1.delete_namespaced_ingress.side_effect = ApiException(status=404)
        result = k8s.delete_deployment("myapp", "user-ns")
        assert result["deployment"]["success"] is True

    def test_delete_deployment_api_exception(self, k8s):
        k8s.apps_v1.delete_namespaced_deployment.side_effect = ApiException(status=404)
        result = k8s.delete_deployment("missing", "user-ns")
        assert result["deployment"]["success"] is False
