"""
tests/api/test_helm_api.py — Integration tests for api.helm InterLink endpoints.

Uses the Flask test client from conftest + patches helm_client functions,
K8sClient, and saved_deployments helpers so no real Helm binary, Kubernetes
cluster, or config file is needed.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

HELM_INSTALL_PATCH = "api.helm.helm_install"
HELM_GET_VALUES_PATCH = "api.helm.helm_get_values"
HELM_UNINSTALL_PATCH = "api.helm.helm_uninstall"
K8S_PATCH = "api.helm._get_k8s"
LOAD_DEFAULT_CHARTS_PATCH = "api.helm.load_default_charts"
LOAD_SITE_CONFIG_PATCH = "api.helm.load_site_config"
RESOLVE_PLACEHOLDERS_PATCH = "api.helm._resolve_placeholders"

# Minimal interlink chart config returned by the mocked loader
_INTERLINK_CFG = [
    {
        "release_name": "interlink",
        "chart": "oci://ghcr.io/chbrandt/interlink",
        "version": None,
        "singleton": True,
        "values_yaml": "nodeName: vk-node\n",
    }
]


def _mock_k8s(namespace_exists: bool = True, create_namespace: dict | None = None):
    m = MagicMock()
    m.namespace_exists.return_value = namespace_exists
    m.create_namespace.return_value = create_namespace or {"success": True}
    return m


def _default_chart_patches():
    """Return a context-manager stack that patches chart-config helpers."""
    return (
        patch(LOAD_DEFAULT_CHARTS_PATCH, return_value=_INTERLINK_CFG),
        patch(LOAD_SITE_CONFIG_PATCH, return_value={"hostname": "dev.local"}),
        patch(RESOLVE_PLACEHOLDERS_PATCH, side_effect=lambda text, ns, cfg: text),
    )


# ---------------------------------------------------------------------------
# POST /api/interlink
# ---------------------------------------------------------------------------


class TestDeployInterlink:
    URL = "/api/interlink"

    def test_requires_auth(self, client):
        assert client.post(self.URL).status_code == 401

    def test_success_returns_201(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "Release installed"}
        k8s = _mock_k8s(namespace_exists=True)
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s), patch(
            HELM_INSTALL_PATCH, return_value=result
        ):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 201
        data = resp.get_json(force=True)
        assert data["success"] is True

    def test_approves_vk_csr_after_successful_install(self, client, auth_headers):
        """POST /api/interlink must approve the virtual-kubelet's pending
        serving-cert CSR right after a successful helm install."""
        headers, _ = auth_headers
        result = {"success": True, "output": "Release installed"}
        k8s = _mock_k8s(namespace_exists=True)
        k8s.approve_pending_csrs.return_value = ["vk-virtual-node-user-abc123-x"]
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s), patch(
            HELM_INSTALL_PATCH, return_value=result
        ):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 201
        k8s.approve_pending_csrs.assert_called_once()

    def test_csr_approval_failure_does_not_fail_install(self, client, auth_headers):
        """CSR approval is best-effort: an exception must not flip a
        successful install into a 5xx."""
        headers, _ = auth_headers
        result = {"success": True, "output": "Release installed"}
        k8s = _mock_k8s(namespace_exists=True)
        k8s.approve_pending_csrs.side_effect = RuntimeError("rbac denied")
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s), patch(
            HELM_INSTALL_PATCH, return_value=result
        ):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 201

    def test_helm_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": False, "output": "", "error": "already installed"}
        k8s = _mock_k8s(namespace_exists=True)
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s), patch(
            HELM_INSTALL_PATCH, return_value=result
        ):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 400

    def test_namespace_created_when_missing(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "ok"}
        k8s = _mock_k8s(namespace_exists=False, create_namespace={"success": True})
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s), patch(
            HELM_INSTALL_PATCH, return_value=result
        ):
            resp = client.post(self.URL, headers=headers)
        k8s.create_namespace.assert_called_once()
        assert resp.status_code == 201

    def test_missing_chart_config_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(LOAD_DEFAULT_CHARTS_PATCH, return_value=[]):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 500
        assert "not found" in resp.get_json(force=True)["error"].lower()

    def test_namespace_create_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        k8s = _mock_k8s(
            namespace_exists=False,
            create_namespace={"success": False, "error": "forbidden"},
        )
        p1, p2, p3 = _default_chart_patches()
        with p1, p2, p3, patch(K8S_PATCH, return_value=k8s):
            resp = client.post(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/interlink
# ---------------------------------------------------------------------------


class TestGetInterlinkValues:
    URL = "/api/interlink"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "values_yaml": "nodeName: vk-node\n", "error": None}
        with patch(HELM_GET_VALUES_PATCH, return_value=result):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["values_yaml"] == "nodeName: vk-node\n"

    def test_not_deployed_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        result = {
            "success": False,
            "values_yaml": None,
            "error": "release: not found",
        }
        with patch(HELM_GET_VALUES_PATCH, return_value=result):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 404

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(HELM_GET_VALUES_PATCH, side_effect=RuntimeError("helm error")):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /api/interlink
# ---------------------------------------------------------------------------


class TestDeleteInterlink:
    URL = "/api/interlink"

    def test_requires_auth(self, client):
        assert client.delete(self.URL).status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "release uninstalled"}
        with patch(HELM_UNINSTALL_PATCH, return_value=result):
            resp = client.delete(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["success"] is True

    def test_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": False, "error": "release not found"}
        with patch(HELM_UNINSTALL_PATCH, return_value=result):
            resp = client.delete(self.URL, headers=headers)
        assert resp.status_code == 400

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(HELM_UNINSTALL_PATCH, side_effect=RuntimeError("helm error")):
            resp = client.delete(self.URL, headers=headers)
        assert resp.status_code == 500
