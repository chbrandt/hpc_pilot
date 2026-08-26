"""
tests/lib/test_k8s_client.py — Unit tests for lib.k8s_client.K8sClient.

All Kubernetes API calls are mocked via unittest.mock so no real cluster
or kubeconfig is needed.
"""

from unittest.mock import MagicMock, patch

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
            patch("lib.k8s_client.client.BatchV1Api") as mock_batch,
        ):
            c = K8sClient(kubeconfig_path="/fake/kubeconfig")
            c.core_v1 = mock_core.return_value
            c.batch_v1 = mock_batch.return_value
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
# InterLink node listing
# ---------------------------------------------------------------------------


class TestListInterlinkNodes:
    def _make_node_mock(self, name: str, taint_keys: list[str]):
        node = MagicMock()
        node.metadata.name = name
        taints = []
        for key in taint_keys:
            t = MagicMock()
            t.key = key
            taints.append(t)
        node.spec.taints = taints
        return node

    def test_returns_nodes_with_interlink_taint(self, k8s):
        vk_node = self._make_node_mock("vk-node-1", ["virtual-node.interlink/no-schedule"])
        other_node = self._make_node_mock("regular-node", ["other.taint/key"])
        k8s.core_v1.list_node.return_value.items = [vk_node, other_node]
        result = k8s.list_interlink_nodes()
        assert result == ["vk-node-1"]

    def test_returns_multiple_interlink_nodes_sorted(self, k8s):
        node_b = self._make_node_mock("vk-b", ["virtual-node.interlink/no-schedule"])
        node_a = self._make_node_mock("vk-a", ["virtual-node.interlink/no-schedule"])
        k8s.core_v1.list_node.return_value.items = [node_b, node_a]
        result = k8s.list_interlink_nodes()
        assert result == ["vk-a", "vk-b"]

    def test_excludes_nodes_without_interlink_taint(self, k8s):
        node = self._make_node_mock("plain-node", [])
        k8s.core_v1.list_node.return_value.items = [node]
        result = k8s.list_interlink_nodes()
        assert result == []

    def test_returns_empty_list_on_api_exception(self, k8s):
        k8s.core_v1.list_node.side_effect = ApiException(status=403)
        result = k8s.list_interlink_nodes()
        assert result == []


# ---------------------------------------------------------------------------
# Job operations
# ---------------------------------------------------------------------------


class TestJobOperations:
    def _make_job_mock(self, name: str = "myapp", ns: str = "user-ns"):
        """Build a MagicMock shaped like a batch/v1 Job object."""
        job = MagicMock()
        job.metadata.name = name
        job.metadata.namespace = ns
        job.metadata.creation_timestamp.strftime.return_value = "2024-01-01 00:00:00"
        job.spec.template.spec.containers = [MagicMock(image="ubuntu:22.04")]
        job.spec.template.spec.node_selector = {"kubernetes.io/hostname": "vk-node"}
        # JobStatus counters (Deployment-era fields don't exist on a Job)
        job.status.active = 0
        job.status.ready = 0
        job.status.succeeded = 0
        job.status.failed = 0
        job.status.conditions = []
        return job

    # ── create_job ────────────────────────────────────────────────────

    def test_create_job_success(self, k8s):
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        result = k8s.create_job(
            name="myapp", image="ubuntu:22.04", node_name="vk-node", namespace="user-ns"
        )
        assert result["success"] is True
        assert result["job_name"] == "myapp"

    def test_create_job_result_has_job_name_not_deployment_name(self, k8s):
        """Result must use 'job_name', not 'deployment_name'."""
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        result = k8s.create_job(
            name="myapp", image="ubuntu:22.04", node_name="vk-node", namespace="user-ns"
        )
        assert "job_name" in result
        assert "deployment_name" not in result

    def test_create_job_sets_node_selector_and_toleration(self, k8s):
        """The pod spec must include nodeSelector and toleration for InterLink."""
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        k8s.create_job(
            name="myapp", image="ubuntu:22.04", node_name="vk-node", namespace="user-ns"
        )
        call_kwargs = k8s.batch_v1.create_namespaced_job.call_args
        job_body = call_kwargs[1]["body"] if call_kwargs[1] else call_kwargs[0][1]
        pod_spec = job_body.spec.template.spec
        assert pod_spec.node_selector == {"kubernetes.io/hostname": "vk-node"}
        assert len(pod_spec.tolerations) == 1
        assert pod_spec.tolerations[0].key == "virtual-node.interlink/no-schedule"
        assert pod_spec.tolerations[0].operator == "Exists"

    def test_create_job_api_exception(self, k8s):
        k8s.batch_v1.create_namespaced_job.side_effect = ApiException(
            status=409, reason="AlreadyExists"
        )
        result = k8s.create_job(
            name="myapp", image="ubuntu:22.04", node_name="vk-node", namespace="user-ns"
        )
        assert result["success"] is False
        assert "error" in result

    # ── list_jobs ──────────────────────────────────────────────────────

    def test_list_jobs_returns_list(self, k8s):
        job = self._make_job_mock()
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert len(result) == 1
        assert result[0]["name"] == "myapp"

    def test_list_jobs_contains_node_name(self, k8s):
        job = self._make_job_mock()
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert result[0]["node_name"] == "vk-node"

    def test_list_jobs_empty_on_api_exception(self, k8s):
        k8s.batch_v1.list_namespaced_job.side_effect = ApiException(status=403)
        result = k8s.list_jobs(namespace="user-ns")
        assert result == []

    # ── get_job_spec ───────────────────────────────────────────────────

    def test_get_job_spec_success(self, k8s):
        job = self._make_job_mock()
        container = job.spec.template.spec.containers[0]
        container.image = "ubuntu:22.04"
        container.command = None
        container.env = []
        k8s.batch_v1.read_namespaced_job.return_value = job
        result = k8s.get_job_spec("myapp", "user-ns")
        assert result["name"] == "myapp"
        assert result["image"] == "ubuntu:22.04"

    def test_get_job_spec_returns_node_name(self, k8s):
        job = self._make_job_mock()
        container = job.spec.template.spec.containers[0]
        container.image = "ubuntu:22.04"
        container.command = None
        container.env = []
        job.spec.template.spec.node_selector = {"kubernetes.io/hostname": "vk-node"}
        k8s.batch_v1.read_namespaced_job.return_value = job
        result = k8s.get_job_spec("myapp", "user-ns")
        assert result["node_name"] == "vk-node"

    def test_get_job_spec_api_exception(self, k8s):
        k8s.batch_v1.read_namespaced_job.side_effect = ApiException(status=404)
        result = k8s.get_job_spec("missing", "user-ns")
        assert "error" in result

    # ── get_job_status ─────────────────────────────────────────────────

    def test_get_job_status_success(self, k8s):
        job = self._make_job_mock()
        complete_cond = MagicMock()
        complete_cond.type = "Complete"
        complete_cond.status = "True"
        job.status.conditions = [complete_cond]
        job.status.succeeded = 1
        k8s.batch_v1.read_namespaced_job.return_value = job
        result = k8s.get_job_status("myapp", "user-ns")
        assert result["status"] == "succeeded"
        assert result["name"] == "myapp"

    def test_get_job_status_api_exception(self, k8s):
        k8s.batch_v1.read_namespaced_job.side_effect = ApiException(status=404)
        result = k8s.get_job_status("missing", "user-ns")
        assert "error" in result

    # ── delete_job ─────────────────────────────────────────────────────

    def test_delete_job_success(self, k8s):
        k8s.batch_v1.delete_namespaced_job.return_value = None
        result = k8s.delete_job("myapp", "user-ns")
        assert result["job"]["success"] is True
        # Result dict must use the 'job' key, not 'deployment'.
        assert "job" in result
        assert "deployment" not in result

    def test_delete_job_api_exception(self, k8s):
        k8s.batch_v1.delete_namespaced_job.side_effect = ApiException(status=404)
        result = k8s.delete_job("missing", "user-ns")
        assert result["job"]["success"] is False


# ---------------------------------------------------------------------------
# Configuration loading (kubeconfig vs in-cluster ServiceAccount)
# ---------------------------------------------------------------------------


class TestConfigLoading:
    """K8sClient must fall back to the in-cluster ServiceAccount when no
    kubeconfig exists — the normal situation for the Helm deployment."""

    def test_falls_back_to_incluster_on_missing_kubeconfig_file(self, monkeypatch):
        monkeypatch.delenv("KUBECONFIG", raising=False)
        with (
            patch(
                "lib.k8s_client.config.load_kube_config",
                side_effect=FileNotFoundError("~/.kube/config"),
            ),
            patch("lib.k8s_client.config.load_incluster_config") as mock_incluster,
            patch("lib.k8s_client.client.CoreV1Api"),
            patch("lib.k8s_client.client.BatchV1Api"),
        ):
            K8sClient(kubeconfig_path=None)
        mock_incluster.assert_called_once()

    def test_falls_back_to_incluster_on_invalid_kubeconfig(self, monkeypatch):
        from kubernetes.config import ConfigException

        monkeypatch.delenv("KUBECONFIG", raising=False)
        with (
            patch(
                "lib.k8s_client.config.load_kube_config",
                side_effect=ConfigException("Invalid kube-config file"),
            ),
            patch("lib.k8s_client.config.load_incluster_config") as mock_incluster,
            patch("lib.k8s_client.client.CoreV1Api"),
            patch("lib.k8s_client.client.BatchV1Api"),
        ):
            K8sClient(kubeconfig_path=None)
        mock_incluster.assert_called_once()

    def test_raises_when_no_kubeconfig_and_not_incluster(self, monkeypatch):
        from kubernetes.config import ConfigException

        monkeypatch.delenv("KUBECONFIG", raising=False)
        with (
            patch(
                "lib.k8s_client.config.load_kube_config",
                side_effect=FileNotFoundError("~/.kube/config"),
            ),
            patch(
                "lib.k8s_client.config.load_incluster_config",
                side_effect=ConfigException("service host/port not set"),
            ),
            patch("lib.k8s_client.client.CoreV1Api"),
            patch("lib.k8s_client.client.BatchV1Api"),
        ):
            with pytest.raises(ConfigException):
                K8sClient(kubeconfig_path=None)

    def test_prefers_kubeconfig_when_available(self, monkeypatch):
        monkeypatch.delenv("KUBECONFIG", raising=False)
        with (
            patch("lib.k8s_client.config.load_kube_config") as mock_kubeconfig,
            patch("lib.k8s_client.config.load_incluster_config") as mock_incluster,
            patch("lib.k8s_client.client.CoreV1Api"),
            patch("lib.k8s_client.client.BatchV1Api"),
        ):
            K8sClient(kubeconfig_path="/fake/kubeconfig")
        mock_kubeconfig.assert_called_once_with(config_file="/fake/kubeconfig")
        mock_incluster.assert_not_called()
