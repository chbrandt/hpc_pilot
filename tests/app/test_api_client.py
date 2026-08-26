"""
tests/app/test_api_client.py — Unit tests for app.api_client.

Verifies that the ``timeout`` argument is forwarded to ``requests`` and that
the module exposes the long-running-operation timeout constant used by the
GUI routes (Helm install --wait, HPC deploy).
"""

from unittest.mock import MagicMock, patch

from app import api_client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_long_timeout_is_greater_than_default():
    """LONG_TIMEOUT must exceed DEFAULT_TIMEOUT (long ops need more time)."""
    assert api_client.LONG_TIMEOUT > api_client.DEFAULT_TIMEOUT


def test_long_timeout_stays_under_ingress_read_timeout():
    """
    The chart's nginx Ingress sets ``proxy-read-timeout: 360``.  The GUI→API
    call must finish before the browser connection is cut off, so LONG_TIMEOUT
    must stay below 360 s.
    """
    assert api_client.LONG_TIMEOUT < 360


# ---------------------------------------------------------------------------
# Timeout forwarding
# ---------------------------------------------------------------------------


def _patched_requests():
    """Return a MagicMock standing in for the ``requests`` module in api_client."""
    mock_requests = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}
    mock_requests.get.return_value = mock_resp
    mock_requests.post.return_value = mock_resp
    mock_requests.delete.return_value = mock_resp
    return mock_requests


def test_api_post_forwards_timeout(app):
    with app.test_request_context():
        with patch.object(api_client, "requests", _patched_requests()) as mock_req:
            api_client.api_post("/api/jobs", {"a": 1}, timeout=123)
    _, kwargs = mock_req.post.call_args
    assert kwargs["timeout"] == 123


def test_api_post_uses_default_timeout_when_unspecified(app):
    with app.test_request_context():
        with patch.object(api_client, "requests", _patched_requests()) as mock_req:
            api_client.api_post("/api/jobs", {"a": 1})
    _, kwargs = mock_req.post.call_args
    assert kwargs["timeout"] == api_client.DEFAULT_TIMEOUT


def test_api_get_forwards_timeout(app):
    with app.test_request_context():
        with patch.object(api_client, "requests", _patched_requests()) as mock_req:
            api_client.api_get("/api/jobs", timeout=45)
    _, kwargs = mock_req.get.call_args
    assert kwargs["timeout"] == 45


def test_api_delete_forwards_timeout(app):
    with app.test_request_context():
        with patch.object(api_client, "requests", _patched_requests()) as mock_req:
            api_client.api_delete("/api/jobs/x", timeout=45)
    _, kwargs = mock_req.delete.call_args
    assert kwargs["timeout"] == 45


def test_api_post_forwards_long_timeout_value(app):
    """A caller passing LONG_TIMEOUT must see exactly that value forwarded."""
    with app.test_request_context():
        with patch.object(api_client, "requests", _patched_requests()) as mock_req:
            api_client.api_post("/api/hpc/deploy", {"hpc_name": "n"}, timeout=api_client.LONG_TIMEOUT)
    _, kwargs = mock_req.post.call_args
    assert kwargs["timeout"] == api_client.LONG_TIMEOUT
