"""
tests/api/test_docs_api.py — Tests for the OpenAPI spec endpoint.

The spec is served at ``GET /api/openapi.yaml`` (no auth required) and is the
source Swagger UI (``/api/docs``) loads.  The ``servers`` entry MUST be a
relative URL so that "Try it out" requests resolve against the same origin
as the spec — otherwise, in a cloud/FQDN deployment, the browser would try
to reach ``http://localhost:5000`` on the *user's* machine and fail with
"TypeError: Load failed".
"""

import yaml


SPEC_URL = "/api/openapi.yaml"


class TestOpenApiSpecEndpoint:
    def test_serves_yaml(self, client):
        """GET /api/openapi.yaml returns 200 + application/yaml."""
        resp = client.get(SPEC_URL)
        assert resp.status_code == 200
        assert resp.mimetype == "application/yaml"
        # Must be valid YAML and a mapping (the OpenAPI document root).
        doc = yaml.safe_load(resp.data)
        assert isinstance(doc, dict)
        assert doc.get("openapi", "").startswith("3.")

    def test_accessible_without_auth(self, client):
        """The spec endpoint does not require a Bearer token (it feeds Swagger UI)."""
        resp = client.get(SPEC_URL)
        assert resp.status_code == 200
        assert resp.status_code != 401

    def test_server_url_is_relative(self, client):
        """The servers URL must be relative (not an absolute localhost) so
        that Swagger UI 'Try it out' targets the deployed origin, not the
        user's browser host."""
        doc = yaml.safe_load(client.get(SPEC_URL).data)
        servers = doc.get("servers", [])
        assert servers, "openapi.yaml must declare at least one server"
        for srv in servers:
            url = srv.get("url", "")
            # Relative URLs start with "/" and have no scheme/host.
            assert url.startswith("/"), (
                f"server url must be relative (got {url!r}); an absolute "
                "http://localhost:5000 breaks 'Try it out' in deployments"
            )
            assert "://localhost" not in url, (
                f"server url must not point at localhost (got {url!r})"
            )
