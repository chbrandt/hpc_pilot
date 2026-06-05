"""
tests/api/test_helm_api.py — Integration tests for api.helm endpoints.

Uses the Flask test client from conftest + patches helm_client functions
and K8sClient so no real Helm binary or Kubernetes cluster is needed.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

HELM_LIST_PATCH = "api.helm.helm_list"
HELM_INSTALL_PATCH = "api.helm.helm_install"
HELM_GET_VALUES_PATCH = "api.helm.helm_get_values"
HELM_UNINSTALL_PATCH = "api.helm.helm_uninstall"
K8S_PATCH = "api.helm._get_k8s"


def _mock_k8s(namespace_exists=True, create_namespace=None):
    m = MagicMock()
    m.namespace_exists.return_value = namespace_exists
    m.create_namespace.return_value = create_namespace or {"success": True}
    return m


# ---------------------------------------------------------------------------
# GET /api/releases
# ---------------------------------------------------------------------------


class TestListReleases:
    URL = "/api/releases"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_returns_releases_list(self, client, auth_headers):
        headers, _ = auth_headers
        releases = [{"name": "my-rel", "namespace": "user-ns", "status": "deployed"}]
        with patch(HELM_LIST_PATCH, return_value=releases):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert len(data) == 1
        assert data[0]["name"] == "my-rel"

    def test_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(HELM_LIST_PATCH, side_effect=RuntimeError("helm error")):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/helm/install
# ---------------------------------------------------------------------------


class TestInstallChart:
    URL = "/api/helm/install"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_release_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={"chart": "bitnami/nginx"}, headers=headers)
        assert resp.status_code == 400
        assert "release_name" in resp.get_json(force=True)["error"].lower()

    def test_missing_chart_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(
            self.URL, json={"release_name": "my-rel"}, headers=headers
        )
        assert resp.status_code == 400
        assert "chart" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_201(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "Release installed"}
        k8s = _mock_k8s(namespace_exists=True)
        with (
            patch(K8S_PATCH, return_value=k8s),
            patch(HELM_INSTALL_PATCH, return_value=result),
        ):
            resp = client.post(
                self.URL,
                json={"release_name": "my-rel", "chart": "bitnami/nginx"},
                headers=headers,
            )
        assert resp.status_code == 201

    def test_helm_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": False, "output": "", "error": "already installed"}
        k8s = _mock_k8s(namespace_exists=True)
        with (
            patch(K8S_PATCH, return_value=k8s),
            patch(HELM_INSTALL_PATCH, return_value=result),
        ):
            resp = client.post(
                self.URL,
                json={"release_name": "my-rel", "chart": "bitnami/nginx"},
                headers=headers,
            )
        assert resp.status_code == 400

    def test_namespace_created_when_missing(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "ok"}
        k8s = _mock_k8s(namespace_exists=False, create_namespace={"success": True})
        with (
            patch(K8S_PATCH, return_value=k8s),
            patch(HELM_INSTALL_PATCH, return_value=result),
        ):
            resp = client.post(
                self.URL,
                json={"release_name": "r", "chart": "c"},
                headers=headers,
            )
        k8s.create_namespace.assert_called_once()
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/releases/<name>/values
# ---------------------------------------------------------------------------


class TestGetReleaseValues:
    def test_requires_auth(self, client):
        assert client.get("/api/releases/my-rel/values").status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "values_yaml": "key: value\n", "error": None}
        with patch(HELM_GET_VALUES_PATCH, return_value=result):
            resp = client.get("/api/releases/my-rel/values", headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["values_yaml"] == "key: value\n"

    def test_failure_returns_404(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": False, "values_yaml": None, "error": "release not found"}
        with patch(HELM_GET_VALUES_PATCH, return_value=result):
            resp = client.get("/api/releases/missing/values", headers=headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/releases/<name>
# ---------------------------------------------------------------------------


class TestDeleteRelease:
    def test_requires_auth(self, client):
        assert client.delete("/api/releases/my-rel").status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": True, "output": "release uninstalled"}
        with patch(HELM_UNINSTALL_PATCH, return_value=result):
            resp = client.delete("/api/releases/my-rel", headers=headers)
        assert resp.status_code == 200

    def test_failure_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        result = {"success": False, "error": "release not found"}
        with patch(HELM_UNINSTALL_PATCH, return_value=result):
            resp = client.delete("/api/releases/missing", headers=headers)
        assert resp.status_code == 400
