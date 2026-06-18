"""
conftest.py — shared pytest fixtures for HPC Pilot tests.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Flask application fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(tmp_path):
    """
    Create the Flask application in testing mode.

    Patches out the Kubernetes config loading so no real kubeconfig is needed,
    and patches saved_deployments._DATA_DIR to use a temporary directory.
    """
    with (
        patch("kubernetes.config.load_kube_config"),
        patch("lib.saved_deployments._DATA_DIR", str(tmp_path / "data")),
    ):
        from main import create_app

        flask_app = create_app()
        flask_app.config["TESTING"] = True
        flask_app.config["SECRET_KEY"] = "test-secret"
        yield flask_app


@pytest.fixture()
def client(app):
    """Return a Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

# Fake claims returned by validate_token when a test bypasses real validation.
FAKE_SUB = "test-user-sub-12345678"
FAKE_NAMESPACE = "user-" + "a" * 16  # placeholder; derived in fixture below


def make_fake_claims(sub: str = FAKE_SUB, exp_offset: int = 3600) -> dict:
    """Return a minimal fake JWT claims dict."""
    return {
        "sub": sub,
        "iss": "https://aai.egi.eu/auth/realms/egi",
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }


@pytest.fixture()
def auth_headers(app):
    """
    Return HTTP headers carrying a fake Bearer token.

    Patches both ``validate_token`` and ``derive_namespace`` inside the
    ``api.auth`` module so every protected route receives valid claims without
    a real EGI Check-in token.
    """
    import hashlib

    fake_sub = FAKE_SUB
    fake_ns = "user-" + hashlib.sha256(fake_sub.encode()).hexdigest()[:16]
    fake_claims = make_fake_claims(sub=fake_sub)

    with (
        patch("api.auth.validate_token", return_value=fake_claims),
        patch("api.auth.derive_namespace", return_value=fake_ns),
        # Bypass group-access check: tests cover API logic, not authn/authz rules
        patch("api.auth.load_site_config", return_value={}),
    ):
        yield {"Authorization": "Bearer fake-test-token"}, fake_ns
