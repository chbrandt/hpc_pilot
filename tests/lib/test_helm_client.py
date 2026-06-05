"""
tests/lib/test_helm_client.py — Unit tests for lib.helm_client.

All subprocess.run calls are mocked; no real Helm binary is required.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from lib.helm_client import (
    helm_get_values,
    helm_install,
    helm_list,
    helm_uninstall,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


# ---------------------------------------------------------------------------
# helm_install
# ---------------------------------------------------------------------------


class TestHelmInstall:
    def test_success_returns_success_true(self):
        mock_result = _make_completed_process(b"Release installed", b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_install("my-release", "bitnami/nginx", "user-ns")
        assert res["success"] is True
        assert "Release installed" in res["output"]
        assert res["error"] is None

    def test_non_zero_returncode_returns_success_false(self):
        mock_result = _make_completed_process(b"", b"helm error", 1)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_install("my-release", "bitnami/nginx", "user-ns")
        assert res["success"] is False
        assert "helm error" in res["error"]

    def test_timeout_returns_error(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("helm", 360)):
            res = helm_install("my-release", "bitnami/nginx", "user-ns")
        assert res["success"] is False
        assert "timed out" in res["error"].lower()

    def test_helm_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            res = helm_install("my-release", "bitnami/nginx", "user-ns")
        assert res["success"] is False
        assert "not found" in res["error"].lower()

    def test_values_yaml_passed_via_stdin(self):
        mock_result = _make_completed_process(b"ok", b"", 0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            helm_install("r", "c", "ns", values_yaml="key: value")
            call_kwargs = mock_run.call_args
            assert b"key: value" == call_kwargs[1].get("input") or \
                   b"key: value" == call_kwargs.kwargs.get("input")

    def test_version_flag_included_when_provided(self):
        mock_result = _make_completed_process(b"ok", b"", 0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            helm_install("r", "c", "ns", version="1.2.3")
            cmd = mock_run.call_args[0][0]
            assert "--version" in cmd
            assert "1.2.3" in cmd


# ---------------------------------------------------------------------------
# helm_list
# ---------------------------------------------------------------------------


class TestHelmList:
    def test_parses_json_output(self):
        releases = [{"name": "my-rel", "namespace": "user-ns", "revision": "1",
                     "updated": "2024-01-01", "status": "deployed",
                     "chart": "nginx-1.0", "app_version": "1.0"}]
        mock_result = _make_completed_process(json.dumps(releases).encode(), b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            result = helm_list("user-ns")
        assert len(result) == 1
        assert result[0]["name"] == "my-rel"

    def test_empty_output_returns_empty_list(self):
        mock_result = _make_completed_process(b"", b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            result = helm_list("user-ns")
        assert result == []

    def test_non_zero_returncode_raises_runtime_error(self):
        mock_result = _make_completed_process(b"", b"forbidden", 1)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="forbidden"):
                helm_list("user-ns")

    def test_helm_not_found_raises_runtime_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="[Nn]ot found"):
                helm_list("user-ns")

    def test_normalises_field_names(self):
        releases = [{"name": "r", "namespace": "ns", "revision": "2",
                     "updated": "2024", "status": "deployed",
                     "chart": "nginx-1", "app_version": "latest"}]
        mock_result = _make_completed_process(json.dumps(releases).encode(), b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            result = helm_list("ns")
        # All expected keys must be present
        for key in ("name", "namespace", "revision", "updated", "status", "chart", "app_version"):
            assert key in result[0]


# ---------------------------------------------------------------------------
# helm_get_values
# ---------------------------------------------------------------------------


class TestHelmGetValues:
    def test_success_returns_values_yaml(self):
        yaml_out = b"key: value\n"
        mock_result = _make_completed_process(yaml_out, b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_get_values("my-release", "user-ns")
        assert res["success"] is True
        assert "key: value" in res["values_yaml"]
        assert res["error"] is None

    def test_null_output_returns_none_values(self):
        mock_result = _make_completed_process(b"null", b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_get_values("my-release", "user-ns")
        assert res["success"] is True
        assert res["values_yaml"] is None

    def test_non_zero_returncode_returns_failure(self):
        mock_result = _make_completed_process(b"", b"release not found", 1)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_get_values("my-release", "user-ns")
        assert res["success"] is False
        assert res["values_yaml"] is None
        assert "not found" in res["error"]

    def test_helm_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            res = helm_get_values("my-release", "user-ns")
        assert res["success"] is False
        assert "not found" in res["error"].lower()


# ---------------------------------------------------------------------------
# helm_uninstall
# ---------------------------------------------------------------------------


class TestHelmUninstall:
    def test_success(self):
        mock_result = _make_completed_process(b"release uninstalled", b"", 0)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_uninstall("my-release", "user-ns")
        assert res["success"] is True
        assert res["error"] is None

    def test_failure_returns_error(self):
        mock_result = _make_completed_process(b"", b"release not found", 1)
        with patch("subprocess.run", return_value=mock_result):
            res = helm_uninstall("my-release", "user-ns")
        assert res["success"] is False
        assert "not found" in res["error"]

    def test_helm_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            res = helm_uninstall("my-release", "user-ns")
        assert res["success"] is False
        assert "not found" in res["error"].lower()
