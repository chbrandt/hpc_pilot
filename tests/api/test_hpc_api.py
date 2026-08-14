"""
tests/api/test_hpc_api.py — Integration tests for api.hpc endpoints.

Uses the Flask test client from conftest + patches lib.hpc_client and
lib.hpc_config so no real mccli, SSH, or HPC nodes are required.
"""

from unittest.mock import patch

import pytest

HPC_CLIENT = "api.hpc.hpc_client"
HPC_CONFIG = "api.hpc.load_hpc_config"

_SUCCESS = {"success": True, "output": "ok", "error": ""}
_FAILURE = {"success": False, "output": "", "error": "connection refused"}

_FAKE_HPC_CONFIG = {
    "name": "test-echo",
    "hostname": "hpc.example.org",
    "ssh_port": 22,
    "plugin": "echo",
}


# ---------------------------------------------------------------------------
# GET /api/hpc/nodes
# ---------------------------------------------------------------------------


class TestHpcNodes:
    URL = "/api/hpc/nodes"

    def test_requires_auth(self, client):
        assert client.get(self.URL).status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        nodes = [_FAKE_HPC_CONFIG]
        with patch("api.hpc.list_hpc_nodes", return_value=nodes):
            resp = client.get(self.URL, headers=headers)
        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert "nodes" in data
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["name"] == "test-echo"


# ---------------------------------------------------------------------------
# POST /api/hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcDeploy:
    URL = "/api/hpc/deploy"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        body = {}
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 400
        assert "hpc_name" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        body = {"hpc_name": "test-echo"}
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.deploy", return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 200
        assert resp.get_json(force=True)["success"] is True

    def test_deploy_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        body = {"hpc_name": "test-echo"}
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.deploy", return_value=_FAILURE),
        ):
            resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 500

    def test_plugin_from_config_passed_through(self, client, auth_headers):
        """Verify plugin is read from the HPC config, not the request body."""
        headers, _ = auth_headers
        body = {"hpc_name": "test-echo"}
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.deploy", return_value=_SUCCESS) as mock_deploy,
        ):
            client.post(self.URL, json=body, headers=headers)
        call_kwargs = mock_deploy.call_args.kwargs
        assert call_kwargs["plugin"] == "echo"
        assert call_kwargs["hpc_host"] == "hpc.example.org"
        assert call_kwargs["ssh_port"] == 22

    def test_wstunnel_params_computed_internally(self, client, auth_headers):
        """Verify wstunnel params are computed from the site config + namespace."""
        headers, fake_ns = auth_headers
        body = {"hpc_name": "test-echo"}
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.deploy", return_value=_SUCCESS) as mock_deploy,
        ):
            client.post(self.URL, json=body, headers=headers)
        call_kwargs = mock_deploy.call_args.kwargs
        # wstunnel_server is now the site's fixed hostname (no per-user
        # subdomain / wildcard domain routing).
        assert call_kwargs["wstunnel_server"] == "ngrok.dev"
        assert fake_ns not in call_kwargs["wstunnel_server"]
        # wstunnel_secret is the full namespace, used as both the shared
        # tunnel secret and the Ingress path prefix on the K8s side.
        assert call_kwargs["wstunnel_secret"] == fake_ns


# ---------------------------------------------------------------------------
# DELETE /api/hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcUndeploy:
    URL = "/api/hpc/deploy"

    def test_requires_auth(self, client):
        assert client.delete(self.URL, json={}).status_code == 401

    def test_missing_hpc_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.delete(self.URL, json={}, headers=headers)
        assert resp.status_code == 400
        assert "hpc_name" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.undeploy", return_value=_SUCCESS),
        ):
            resp = client.delete(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 200
        assert resp.get_json(force=True)["success"] is True

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.undeploy", return_value=_FAILURE),
        ):
            resp = client.delete(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 500

    def test_ssh_port_from_config_passed_through(self, client, auth_headers):
        """Verify ssh_port is read from the HPC config file."""
        headers, _ = auth_headers
        body = {"hpc_name": "test-echo"}
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.undeploy", return_value=_SUCCESS) as mock_undeploy,
        ):
            client.delete(self.URL, json=body, headers=headers)
        call_kwargs = mock_undeploy.call_args.kwargs
        assert call_kwargs["ssh_port"] == 22
        assert call_kwargs["hpc_host"] == "hpc.example.org"


# ---------------------------------------------------------------------------
# POST /api/hpc/status
# ---------------------------------------------------------------------------


class TestHpcStatus:
    URL = "/api/hpc/status"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.get_status", return_value=_SUCCESS),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.get_status", return_value=_FAILURE),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/hpc/start
# ---------------------------------------------------------------------------


class TestHpcStart:
    URL = "/api/hpc/start"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.start_services", return_value=_SUCCESS),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.start_services", return_value=_FAILURE),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/hpc/stop
# ---------------------------------------------------------------------------


class TestHpcStop:
    URL = "/api/hpc/stop"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_name_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.stop_services", return_value=_SUCCESS),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(HPC_CONFIG, return_value=_FAKE_HPC_CONFIG),
            patch(f"{HPC_CLIENT}.stop_services", return_value=_FAILURE),
        ):
            resp = client.post(
                self.URL, json={"hpc_name": "test-echo"}, headers=headers
            )
        assert resp.status_code == 500
