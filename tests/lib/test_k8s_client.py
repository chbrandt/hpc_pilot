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
            patch("lib.k8s_client.client.CertificatesV1Api") as mock_certs,
        ):
            c = K8sClient(kubeconfig_path="/fake/kubeconfig")
            c.core_v1 = mock_core.return_value
            c.batch_v1 = mock_batch.return_value
            c.certificates_v1 = mock_certs.return_value
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

    # ── create_job_from_spec ────────────────────────────

    def test_create_job_from_spec_injects_toleration(self, k8s):
        """A spec without the InterLink toleration gets it injected."""
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        pod_spec = {
            "containers": [{"name": "myapp", "image": "ubuntu:22.04"}],
            "nodeSelector": {"kubernetes.io/hostname": "vk-node"},
        }
        k8s.create_job_from_spec(name="myapp", spec=pod_spec, namespace="user-ns")
        call_kwargs = k8s.batch_v1.create_namespaced_job.call_args
        body = call_kwargs[1]["body"] if call_kwargs[1] else call_kwargs[0][1]
        spec = body["spec"]["template"]["spec"]
        assert any(
            t.get("key") == "virtual-node.interlink/no-schedule"
            for t in spec["tolerations"]
        )
        assert spec["restartPolicy"] == "Never"

    def test_create_job_from_spec_keeps_existing_tolerations(self, k8s):
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        tol = {"key": "virtual-node.interlink/no-schedule", "operator": "Exists"}
        pod_spec = {
            "containers": [{"name": "myapp", "image": "ubuntu:22.04"}],
            "tolerations": [tol],
        }
        k8s.create_job_from_spec(name="myapp", spec=pod_spec, namespace="user-ns")
        call_kwargs = k8s.batch_v1.create_namespaced_job.call_args
        body = call_kwargs[1]["body"] if call_kwargs[1] else call_kwargs[0][1]
        assert body["spec"]["template"]["spec"]["tolerations"] == [tol]

    def test_create_job_from_spec_rejects_spec_without_containers(self, k8s):
        result = k8s.create_job_from_spec(name="myapp", spec={"restartPolicy": "Never"})
        assert result["success"] is False
        k8s.batch_v1.create_namespaced_job.assert_not_called()

    def test_create_job_from_spec_success(self, k8s):
        created = self._make_job_mock()
        k8s.batch_v1.create_namespaced_job.return_value = created
        result = k8s.create_job_from_spec(
            name="myapp",
            spec={"containers": [{"name": "myapp", "image": "ubuntu:22.04"}]},
            namespace="user-ns",
        )
        assert result["success"] is True
        assert result["job_name"] == "myapp"

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

    def test_list_jobs_status_failed(self, k8s):
        job = self._make_job_mock()
        failed_cond = MagicMock()
        failed_cond.type = "Failed"
        failed_cond.status = "True"
        job.status.conditions = [failed_cond]
        job.status.failed = 1
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert result[0]["status"] == "failed"

    def test_list_jobs_status_succeeded(self, k8s):
        job = self._make_job_mock()
        complete_cond = MagicMock()
        complete_cond.type = "Complete"
        complete_cond.status = "True"
        job.status.conditions = [complete_cond]
        job.status.succeeded = 1
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert result[0]["status"] == "succeeded"

    def test_list_jobs_status_running(self, k8s):
        job = self._make_job_mock()
        job.status.active = 1
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert result[0]["status"] == "running"

    def test_list_jobs_status_unknown(self, k8s):
        job = self._make_job_mock()
        k8s.batch_v1.list_namespaced_job.return_value.items = [job]
        result = k8s.list_jobs(namespace="user-ns")
        assert result[0]["status"] == "unknown"

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

    # ── get_job_output ────────────────────────────────────────────────

    def test_get_job_output_success(self, k8s):
        """Should list pods by job-name label and read their logs."""
        pod_mock = MagicMock()
        pod_mock.metadata.name = "myapp-abc123"
        pod_mock.spec.containers = [MagicMock()]
        pod_mock.spec.containers[0].name = "myapp"
        k8s.core_v1.list_namespaced_pod.return_value.items = [pod_mock]

        # Raw (non-preloaded) log response: bytes payload on `.data`
        log_response = MagicMock()
        log_response.data = b"hello world\n"
        k8s.core_v1.read_namespaced_pod_log.return_value = log_response

        result = k8s.get_job_output("myapp", "user-ns")
        assert result["name"] == "myapp"
        assert result["pod"] == "myapp-abc123"
        assert result["content"] == "hello world\n"
        k8s.core_v1.list_namespaced_pod.assert_called_once_with(
            namespace="user-ns", label_selector="job-name=myapp"
        )
        k8s.core_v1.read_namespaced_pod_log.assert_called_once_with(
            name="myapp-abc123",
            namespace="user-ns",
            container="myapp",
            _preload_content=False,
        )

    def test_get_job_output_decodes_bytes_not_repr(self, k8s):
        """Regression: raw bytes must be decoded, not str()-mangled to b'...'."""
        pod_mock = MagicMock()
        pod_mock.metadata.name = "myapp-abc123"
        pod_mock.spec.containers = [MagicMock()]
        pod_mock.spec.containers[0].name = "myapp"
        k8s.core_v1.list_namespaced_pod.return_value.items = [pod_mock]

        log_response = MagicMock()
        log_response.data = "café ☕ unicode output\n".encode("utf-8")
        k8s.core_v1.read_namespaced_pod_log.return_value = log_response

        result = k8s.get_job_output("myapp", "user-ns")
        assert result["content"] == "café ☕ unicode output\n"
        assert not result["content"].startswith("b'")

    def test_get_job_output_no_pods_returns_error(self, k8s):
        k8s.core_v1.list_namespaced_pod.return_value.items = []
        result = k8s.get_job_output("nonexistent", "user-ns")
        assert "error" in result
        assert "No pods found" in result["error"]

    def test_get_job_output_api_exception(self, k8s):
        k8s.core_v1.list_namespaced_pod.side_effect = ApiException(status=403)
        result = k8s.get_job_output("myapp", "user-ns")
        assert "error" in result


# ---------------------------------------------------------------------------
# CSR auto-approval (InterLink virtual-kubelet serving certificate)
# ---------------------------------------------------------------------------


def _make_csr_mock(
    name: str,
    signer: str,
    requestor: str,
    conditions: list | None = None,
):
    csr = MagicMock()
    csr.metadata.name = name
    csr.spec.signer_name = signer
    csr.spec.username = requestor
    csr.status.conditions = conditions or []
    return csr


class TestApprovePendingCsrs:
    NS = "user-abc123"

    def _vk_csr(self):
        return _make_csr_mock(
            name="vk-virtual-node-user-abc123-s7vsc",
            signer="kubernetes.io/kubelet-serving",
            requestor=f"system:serviceaccount:{self.NS}:virtual-node-{self.NS}",
        )

    def test_approves_pending_matching_csr(self, k8s):
        csr = self._vk_csr()
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]

        approved = k8s.approve_pending_csrs(self.NS, timeout=0.0)

        assert approved == [csr.metadata.name]
        k8s.certificates_v1.replace_certificate_signing_request_approval.assert_called_once()
        body = k8s.certificates_v1.replace_certificate_signing_request_approval.call_args.kwargs["body"]
        assert body["status"]["conditions"][0]["type"] == "Approved"
        assert body["metadata"]["name"] == csr.metadata.name

    def test_ignores_csr_with_wrong_signer(self, k8s):
        csr = _make_csr_mock(
            name="client-csr",
            signer="kubernetes.io/kube-apiserver-client",
            requestor=f"system:serviceaccount:{self.NS}:virtual-node-{self.NS}",
        )
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]

        assert k8s.approve_pending_csrs(self.NS, timeout=0.0) == []
        k8s.certificates_v1.replace_certificate_signing_request_approval.assert_not_called()

    def test_ignores_csr_from_other_namespace(self, k8s):
        csr = _make_csr_mock(
            name="vk-virtual-node-other-ns",
            signer="kubernetes.io/kubelet-serving",
            requestor="system:serviceaccount:user-other:virtual-node-user-other",
        )
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]

        assert k8s.approve_pending_csrs(self.NS, timeout=0.0) == []
        k8s.certificates_v1.replace_certificate_signing_request_approval.assert_not_called()

    def test_ignores_already_approved_csr(self, k8s):
        csr = self._vk_csr()
        csr.status.conditions = [MagicMock()]  # any condition ⇒ not pending
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]

        assert k8s.approve_pending_csrs(self.NS, timeout=0.0) == []
        k8s.certificates_v1.replace_certificate_signing_request_approval.assert_not_called()

    def test_list_failure_returns_empty_list(self, k8s):
        k8s.certificates_v1.list_certificate_signing_request.side_effect = ApiException(
            status=403
        )
        assert k8s.approve_pending_csrs(self.NS, timeout=0.0) == []

    def test_approval_failure_is_skipped_not_raised(self, k8s):
        csr = self._vk_csr()
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]
        k8s.certificates_v1.replace_certificate_signing_request_approval.side_effect = (
            ApiException(status=403)
        )

        assert k8s.approve_pending_csrs(self.NS, timeout=0.0) == []

    def test_polls_until_csr_appears(self, k8s):
        """A CSR created shortly after install is picked up by polling."""
        csr = self._vk_csr()
        listing = MagicMock()
        listing.items = []
        listing_with_csr = MagicMock()
        listing_with_csr.items = [csr]
        k8s.certificates_v1.list_certificate_signing_request.side_effect = [
            listing,
            listing_with_csr,
        ]

        with patch("lib.k8s_client.time.sleep") as mock_sleep:
            approved = k8s.approve_pending_csrs(self.NS, timeout=30.0)

        assert approved == [csr.metadata.name]
        mock_sleep.assert_called_once()


class TestGetJobOutputTlsSelfHealing:
    """On the API server's kubelet-proxy TLS failure, get_job_output must
    approve the pending VK CSR and retry the log read once."""

    NS = "user-abc123"

    def _make_pod(self):
        pod_mock = MagicMock()
        pod_mock.metadata.name = "myapp-abc123"
        pod_mock.spec.containers = [MagicMock()]
        pod_mock.spec.containers[0].name = "myapp"
        return pod_mock

    def _tls_error(self):
        exc = ApiException(status=500, reason="Internal Server Error")
        # ApiException has no `body` constructor kwarg; set it directly
        # (mirrors what http_resp-based construction would populate).
        exc.body = '{"message":"remote error: tls: internal error"}'
        return exc

    def test_tls_error_triggers_approval_and_retry(self, k8s):
        k8s.core_v1.list_namespaced_pod.return_value.items = [self._make_pod()]

        good_response = MagicMock()
        good_response.data = b"job output after retry\n"
        k8s.core_v1.read_namespaced_pod_log.side_effect = [
            self._tls_error(),
            good_response,
        ]

        csr = _make_csr_mock(
            name="vk-virtual-node-user-abc123-s7vsc",
            signer="kubernetes.io/kubelet-serving",
            requestor=f"system:serviceaccount:{self.NS}:virtual-node-{self.NS}",
        )
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = [csr]

        result = k8s.get_job_output("myapp", self.NS)

        assert result["content"] == "job output after retry\n"
        assert k8s.core_v1.read_namespaced_pod_log.call_count == 2
        k8s.certificates_v1.replace_certificate_signing_request_approval.assert_called_once()

    def test_tls_error_without_pending_csr_returns_error(self, k8s):
        k8s.core_v1.list_namespaced_pod.return_value.items = [self._make_pod()]
        k8s.core_v1.read_namespaced_pod_log.side_effect = self._tls_error()
        k8s.certificates_v1.list_certificate_signing_request.return_value.items = []

        result = k8s.get_job_output("myapp", self.NS)

        assert "error" in result
        assert k8s.core_v1.read_namespaced_pod_log.call_count == 1

    def test_non_tls_error_returns_error_without_approval(self, k8s):
        k8s.core_v1.list_namespaced_pod.return_value.items = [self._make_pod()]
        k8s.core_v1.read_namespaced_pod_log.side_effect = ApiException(status=500, reason="Boom")

        result = k8s.get_job_output("myapp", self.NS)

        assert "error" in result
        k8s.certificates_v1.list_certificate_signing_request.assert_not_called()


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
            patch("lib.k8s_client.client.CertificatesV1Api"),
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
            patch("lib.k8s_client.client.CertificatesV1Api"),
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
