'''
tests/api/test_health_api.py - Tests for the public /health endpoint.

The endpoint is a liveness probe: it must be reachable without any
authentication and return a simple JSON status.
'''


class TestHealth:
    URL = '/health'

    def test_returns_200_without_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 200

    def test_returns_service_alive_json(self, client):
        resp = client.get(self.URL)
        assert resp.get_json(force=True) == {'status': 'Service alive'}

    def test_content_type_is_json(self, client):
        resp = client.get(self.URL)
        assert resp.mimetype == 'application/json'
