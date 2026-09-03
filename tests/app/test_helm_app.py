"""
tests/app/test_helm_app.py — GUI-layer tests for app.helm routes.

app.helm now only hosts deprecated redirects: the InterLink deploy/list UI
was merged into the "Manage Nodes" page (app.hpc, GET /hpc/nodes). See
tests/app/test_hpc_app.py for the functional coverage of that page and of
app.hpc.interlink_deploy / app.hpc.interlink_delete.
"""

from unittest.mock import patch


GET_SESSION_USER_PATCH = "app.auth.get_session_user"

FAKE_NAMESPACE = "user-testnamespace1234"
FAKE_USER = {"sub": "test-sub", "namespace": FAKE_NAMESPACE, "exp": 9999999999, "iss": "https://aai.egi.eu"}


def _logged_in_client(client):
    with client.session_transaction() as sess:
        sess["namespace"] = FAKE_NAMESPACE
        sess["token"] = "fake-token"
        sess["claims"] = {"sub": "test-sub", "exp": 9999999999}
    return client


class TestReleasesRedirect:
    URL = "/releases"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_manage_nodes_when_logged_in(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")


class TestHelmDeployRedirect:
    URL = "/helm"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_manage_nodes_when_logged_in(self, client):
        _logged_in_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")

