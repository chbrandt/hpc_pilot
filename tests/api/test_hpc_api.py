"""
tests/api/test_hpc_api.py — Integration tests for api.hpc endpoints.

Uses the Flask test client from conftest + patches lib.hpc_client so no
real mccli, SSH, or HPC nodes are required.
"""

from unittest.mock import patch

import pytest

HPC_CLIENT = "api.hpc.hpc_client"

_SUCCESS = {"success": True, "output": "ok", "error": ""}
_FAILURE = {"success": False, "output": "", "error": "connection refused"}


# ---------------------------------------------------------------------------
# POST /api/hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcDeploy:
    URL = "/api/hpc/deploy"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_host_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        body = {
            "wstunnel_server": "wst.example.com",
            "wstunnel_secret": "secret",
        }
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 400
        assert "hpc_host" in resp.get_json(force=True)["error"].lower()

    def test_missing_wstunnel_server_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        body = {"hpc_host": "hpc.example.org", "wstunnel_secret": "secret"}
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 400
        assert "wstunnel_server" in resp.get_json(force=True)["error"].lower()

    def test_missing_wstunnel_secret_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        body = {"hpc_host": "hpc.example.org", "wstunnel_server": "wst.example.com"}
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 400
        assert "wstunnel_secret" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        body = {
            "hpc_host": "hpc.example.org",
            "wstunnel_server": "wst.example.com",
            "wstunnel_secret": "mysecret",
        }
        with patch(f"{HPC_CLIENT}.deploy", return_value=_SUCCESS):
            resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 200
        assert resp.get_json(force=True)["success"] is True

    def test_deploy_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        body = {
            "hpc_host": "hpc.example.org",
            "wstunnel_server": "wst.example.com",
            "wstunnel_secret": "mysecret",
        }
        with patch(f"{HPC_CLIENT}.deploy", return_value=_FAILURE):
            resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 500

    def test_custom_ports_passed_through(self, client, auth_headers):
        """Verify wstunnel_port and wstunnel_local_port are forwarded."""
        headers, _ = auth_headers
        body = {
            "hpc_host": "hpc.example.org",
            "wstunnel_server": "wst.example.com",
            "wstunnel_secret": "s",
            "wstunnel_port": 9000,
            "wstunnel_local_port": 9001,
        }
        with patch(f"{HPC_CLIENT}.deploy", return_value=_SUCCESS) as mock_deploy:
            client.post(self.URL, json=body, headers=headers)
        call_kwargs = mock_deploy.call_args.kwargs
        assert call_kwargs["wstunnel_port"] == 9000
        assert call_kwargs["wstunnel_local_port"] == 9001


# ---------------------------------------------------------------------------
# DELETE /api/hpc/deploy
# ---------------------------------------------------------------------------


class TestHpcUndeploy:
    URL = "/api/hpc/deploy"

    def test_requires_auth(self, client):
        assert client.delete(self.URL, json={}).status_code == 401

    def test_missing_hpc_host_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.delete(self.URL, json={}, headers=headers)
        assert resp.status_code == 400
        assert "hpc_host" in resp.get_json(force=True)["error"].lower()

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.undeploy", return_value=_SUCCESS):
            resp = client.delete(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 200
        assert resp.get_json(force=True)["success"] is True

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.undeploy", return_value=_FAILURE):
            resp = client.delete(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 500

    def test_custom_ssh_port_passed_through(self, client, auth_headers):
        """Verify ssh_port is forwarded to hpc_client.undeploy."""
        headers, _ = auth_headers
        body = {"hpc_host": "hpc.example.org", "ssh_port": 2222}
        with patch(f"{HPC_CLIENT}.undeploy", return_value=_SUCCESS) as mock_undeploy:
            client.delete(self.URL, json=body, headers=headers)
        call_kwargs = mock_undeploy.call_args.kwargs
        assert call_kwargs["ssh_port"] == 2222


# ---------------------------------------------------------------------------
# POST /api/hpc/status
# ---------------------------------------------------------------------------


class TestHpcStatus:
    URL = "/api/hpc/status"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_host_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.get_status", return_value=_SUCCESS):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.get_status", return_value=_FAILURE):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/hpc/start
# ---------------------------------------------------------------------------


class TestHpcStart:
    URL = "/api/hpc/start"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_host_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.start_services", return_value=_SUCCESS):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.start_services", return_value=_FAILURE):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/hpc/stop
# ---------------------------------------------------------------------------


class TestHpcStop:
    URL = "/api/hpc/stop"

    def test_requires_auth(self, client):
        assert client.post(self.URL, json={}).status_code == 401

    def test_missing_hpc_host_returns_400(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.post(self.URL, json={}, headers=headers)
        assert resp.status_code == 400

    def test_success_returns_200(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.stop_services", return_value=_SUCCESS):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 200

    def test_failure_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with patch(f"{HPC_CLIENT}.stop_services", return_value=_FAILURE):
            resp = client.post(
                self.URL, json={"hpc_host": "hpc.example.org"}, headers=headers
            )
        assert resp.status_code == 500
