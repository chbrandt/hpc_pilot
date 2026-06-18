"""
tests/api/test_saved_api.py — Integration tests for api.saved endpoints.

Uses the Flask test client from conftest + patches saved_deployments and
site_config helpers so no real filesystem access is needed.
"""

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

SEED_DEFAULTS_PATCH = "api.saved.seed_defaults"
SEED_HPC_DEFAULTS_PATCH = "api.saved.seed_hpc_defaults"
LOAD_SITE_CONFIG_PATCH = "api.saved.load_site_config"

_FAKE_SITE_CFG = {"cluster_domain": "test.local"}


# ---------------------------------------------------------------------------
# POST /api/saved/seed
# ---------------------------------------------------------------------------


class TestSeedSavedDefaults:
    URL = "/api/saved/seed"

    def test_requires_auth(self, client):
        assert client.post(self.URL).status_code == 401

    def test_success_returns_200(self, client, auth_headers):
        headers, fake_ns = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH),
            patch(SEED_HPC_DEFAULTS_PATCH),
        ):
            resp = client.post(self.URL, headers=headers)

        assert resp.status_code == 200
        data = resp.get_json(force=True)
        assert data["seeded"] is True
        assert data["namespace"] == fake_ns

    def test_seed_defaults_called_with_site_config(self, client, auth_headers):
        """seed_defaults must receive the site config loaded by api.site_config."""
        headers, fake_ns = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH) as mock_seed,
            patch(SEED_HPC_DEFAULTS_PATCH),
        ):
            client.post(self.URL, headers=headers)

        mock_seed.assert_called_once_with(fake_ns, _FAKE_SITE_CFG)

    def test_seed_hpc_defaults_called_with_namespace_and_site_config(self, client, auth_headers):
        """seed_hpc_defaults must receive the namespace and site config."""
        headers, fake_ns = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH),
            patch(SEED_HPC_DEFAULTS_PATCH) as mock_hpc_seed,
        ):
            client.post(self.URL, headers=headers)

        mock_hpc_seed.assert_called_once_with(fake_ns, _FAKE_SITE_CFG)

    def test_seed_exception_returns_500(self, client, auth_headers):
        headers, _ = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH, side_effect=RuntimeError("disk error")),
            patch(SEED_HPC_DEFAULTS_PATCH),
        ):
            resp = client.post(self.URL, headers=headers)

        assert resp.status_code == 500
        assert "error" in resp.get_json(force=True)

    def test_seed_hpc_exception_returns_500(self, client, auth_headers):
        """An exception raised by seed_hpc_defaults should also return 500."""
        headers, _ = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH),
            patch(SEED_HPC_DEFAULTS_PATCH, side_effect=RuntimeError("hpc disk error")),
        ):
            resp = client.post(self.URL, headers=headers)

        assert resp.status_code == 500
        assert "error" in resp.get_json(force=True)

    def test_both_seeds_called_in_single_request(self, client, auth_headers):
        """Both Helm and HPC seed functions must be invoked in the same request."""
        headers, fake_ns = auth_headers
        with (
            patch(LOAD_SITE_CONFIG_PATCH, return_value=_FAKE_SITE_CFG),
            patch(SEED_DEFAULTS_PATCH) as mock_helm,
            patch(SEED_HPC_DEFAULTS_PATCH) as mock_hpc,
        ):
            client.post(self.URL, headers=headers)

        assert mock_helm.call_count == 1
        assert mock_hpc.call_count == 1
