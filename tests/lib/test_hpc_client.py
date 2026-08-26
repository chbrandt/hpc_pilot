"""
tests/lib/test_hpc_client.py — Unit tests for lib.hpc_client.

All subprocess.run calls and validate_token are mocked; no real mccli,
SSH, or HPC nodes are required.
"""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

import lib.hpc_client as hpc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(stdout: bytes = b"", returncode: int = 0, stderr: bytes = b""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


FAKE_TOKEN = "fake.egi.token"
FAKE_HOST = "hpc.example.org"
FAKE_PORT = 22


# ---------------------------------------------------------------------------
# _run_mccli
# ---------------------------------------------------------------------------


class TestRunMccli:
    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_success_when_no_sentinel_in_output(self):
        with patch("subprocess.run", return_value=_completed(b"myuser\n")):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "whoami")
        assert res["success"] is True
        assert "myuser" in res["output"]

    def test_failure_when_sentinel_in_output(self):
        sentinel = b"something went wrong\n__MCCLI_COMMAND_FAILED__\n"
        with patch("subprocess.run", return_value=_completed(sentinel)):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "false")
        assert res["success"] is False

    def test_timeout_returns_failure(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("mccli", 30)):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "whoami")
        assert res["success"] is False
        assert "timed out" in res["error"].lower()

    def test_file_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "whoami")
        assert res["success"] is False
        assert "mccli" in res["error"].lower()


# ---------------------------------------------------------------------------
# _copy_mccli
# ---------------------------------------------------------------------------


class TestCopyMccli:
    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_success_when_returncode_zero(self):
        """rc==0 with empty output means the copy succeeded (scp -q is quiet)."""
        with patch("subprocess.run", return_value=_completed(b"")):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/local/file", "/remote/file")
        assert res["success"] is True

    def test_failure_when_returncode_nonzero_surfaces_stderr(self):
        """A non-zero exit code means failure; the error (on stderr) must be surfaced."""
        with patch("subprocess.run", return_value=_completed(b"", returncode=1, stderr=b"Permission denied\n")):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/local/file", "/remote/file")
        assert res["success"] is False
        assert "Permission denied" in res["error"]

    def test_failure_when_returncode_nonzero_and_stderr_empty(self):
        """Even without stderr, a non-zero rc must be reported as a failure."""
        with patch("subprocess.run", return_value=_completed(b"", returncode=1)):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/l", "/r")
        assert res["success"] is False
        assert "scp failed" in res["error"]

    def test_success_with_stderr_banner(self):
        """rc==0 with a stderr banner (some servers print one) is still success."""
        with patch("subprocess.run", return_value=_completed(b"", returncode=0, stderr=b"Welcome to the cluster\n")):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/l", "/r")
        assert res["success"] is True

    def test_timeout_returns_failure(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("mccli", 30)):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/l", "/r")
        assert res["success"] is False
        assert "timed out" in res["error"].lower()

    def test_file_not_found_returns_error(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            res = hpc._copy_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "/l", "/r")
        assert res["success"] is False


# ---------------------------------------------------------------------------
# _run_mccli — stderr surfacing
# ---------------------------------------------------------------------------


class TestRunMccliStderr:
    """mccli-level failures (auth/connection) surface via stderr + non-zero rc."""

    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_nonzero_returncode_surfaces_stderr(self):
        with patch("subprocess.run", return_value=_completed(b"", returncode=255, stderr=b"Connection refused\n")):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "whoami")
        assert res["success"] is False
        assert "Connection refused" in res["error"]

    def test_sentinel_failure_includes_stderr(self):
        sentinel = b"boom\n__MCCLI_COMMAND_FAILED__\n"
        with patch("subprocess.run", return_value=_completed(sentinel, returncode=0, stderr=b"extra context\n")):
            res = hpc._run_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, "false")
        assert res["success"] is False
        assert "boom" in res["error"]
        assert "extra context" in res["error"]


# ---------------------------------------------------------------------------
# _copy_via_stdin_mccli — cat-over-ssh fallback
# ---------------------------------------------------------------------------


class TestCopyViaStdinMccli:
    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_pipes_file_content_over_cat(self, tmp_path):
        local = tmp_path / "conf.yaml"
        local.write_text("key: value\n")
        with patch("lib.hpc_client._run_mccli", return_value={"success": True, "output": "", "error": ""}) as mock_run:
            res = hpc._copy_via_stdin_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, str(local), "~/.pilot/conf.yaml")
        assert res["success"] is True
        assert "cat over ssh" in res["output"].lower()
        args, kwargs = mock_run.call_args
        # command is the 4th positional arg: "cat > <remote>"
        assert "cat >" in args[3]
        assert kwargs["stdin_data"] == b"key: value\n"

    def test_failure_propagates(self, tmp_path):
        local = tmp_path / "conf.yaml"
        local.write_text("x")
        with patch("lib.hpc_client._run_mccli", return_value={"success": False, "output": "", "error": "permission denied"}):
            res = hpc._copy_via_stdin_mccli(FAKE_TOKEN, FAKE_HOST, FAKE_PORT, str(local), "/r")
        assert res["success"] is False
        assert "permission denied" in res["error"]


# ---------------------------------------------------------------------------
# deploy — scp → cat-over-ssh fallback wiring
# ---------------------------------------------------------------------------


class TestDeployCopyFallback:
    """When scp fails, deploy's copier must fall back to cat-over-ssh so the
    config files still reach the remote node (the original silent-failure bug
    that left supervisord.conf missing and made start_supervisord fail)."""

    _OK = {"success": True, "output": "ok", "error": ""}

    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_scp_failure_triggers_cat_over_ssh_fallback(self):
        import contextlib

        # Steps that don't touch the copier — stub them to succeed.
        run_only = [
            "setup_directories", "install_supervisord", "install_wstunnel",
            "install_plugin", "start_supervisord", "check_status",
        ]
        with contextlib.ExitStack() as stack:
            for n in run_only:
                stack.enter_context(patch(f"lib.hpc_client.{n}", return_value=self._OK))
            # scp fails (e.g. SFTP channel unavailable on motley-cue).
            stack.enter_context(patch(
                "lib.hpc_client._copy_mccli",
                return_value={"success": False, "output": "", "error": "scp: no SFTP channel"},
            ))
            # cat-over-ssh fallback succeeds — assert it was used.
            cat = stack.enter_context(patch(
                "lib.hpc_client._copy_via_stdin_mccli",
                return_value={"success": True, "output": "copied", "error": ""},
            ))
            # Avoid real template/tempfile I/O inside the copy step functions;
            # the copier closure still runs for real and exercises the fallback.
            stack.enter_context(patch("lib.hpc_client._render_template", return_value="rendered"))
            tf = MagicMock()
            tf.name = "/tmp/pilot_temp/x"
            stack.enter_context(patch("lib.hpc_client._write_to_tempfile", return_value=tf))

            res = hpc.deploy(
                FAKE_TOKEN, FAKE_HOST, FAKE_PORT,
                "wss.example.org", 443, "secret", 4000, plugin="slurm",
            )

        assert res["success"] is True
        # The fallback was invoked for both copy steps (supervisord + plugin conf).
        assert cat.call_count >= 2


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


class TestPublicHelpers:
    """
    Each public function is a thin wrapper around _run_mccli.
    We mock _run_mccli directly to verify correct delegation.
    """

    @pytest.fixture(autouse=True)
    def _patch_validate(self):
        with patch("lib.hpc_client.validate_token"):
            yield

    def test_check_connection_returns_true_on_success(self):
        with patch("lib.hpc_client._run_mccli", return_value={"success": True, "output": "user\n", "error": ""}):
            assert hpc.check_connection(FAKE_TOKEN, FAKE_HOST) is True

    def test_check_connection_returns_false_on_failure(self):
        with patch("lib.hpc_client._run_mccli", return_value={"success": False, "output": "", "error": "err"}):
            assert hpc.check_connection(FAKE_TOKEN, FAKE_HOST) is False

    def test_check_installed_true_when_installed_in_output(self):
        with patch("lib.hpc_client._run_mccli", return_value={"success": True, "output": "installed", "error": ""}):
            assert hpc.check_installed(FAKE_TOKEN, FAKE_HOST) is True

    def test_check_installed_false_when_missing_in_output(self):
        with patch("lib.hpc_client._run_mccli", return_value={"success": True, "output": "missing", "error": ""}):
            assert hpc.check_installed(FAKE_TOKEN, FAKE_HOST) is False

    def test_get_status_delegates_to_run_mccli(self):
        expected = {"success": True, "output": "wstunnel RUNNING", "error": ""}
        with patch("lib.hpc_client._run_mccli", return_value=expected) as mock_run:
            result = hpc.get_status(FAKE_TOKEN, FAKE_HOST, FAKE_PORT)
        assert result == expected

    def test_start_services_delegates_to_run_mccli(self):
        expected = {"success": True, "output": "started", "error": ""}
        with patch("lib.hpc_client._run_mccli", return_value=expected):
            result = hpc.start_services(FAKE_TOKEN, FAKE_HOST, FAKE_PORT)
        assert result == expected

    def test_stop_services_delegates_to_run_mccli(self):
        expected = {"success": True, "output": "stopped", "error": ""}
        with patch("lib.hpc_client._run_mccli", return_value=expected):
            result = hpc.stop_services(FAKE_TOKEN, FAKE_HOST, FAKE_PORT)
        assert result == expected

    def test_undeploy_success_returns_success_dict(self):
        """undeploy() succeeds when both remote steps succeed."""
        ok = {"success": True, "output": "done", "error": ""}
        with patch("lib.hpc_client._run_mccli", return_value=ok):
            result = hpc.undeploy(FAKE_TOKEN, FAKE_HOST, FAKE_PORT)
        assert result["success"] is True

    def test_undeploy_failure_on_remove_step(self):
        """undeploy() propagates failure when the rm -rf step fails.

        undeploy() runs three sequential steps — stop_services,
        stop_supervisord, remove_installation — each of which calls
        _run_mccli once.  Provide three responses so that only the final
        remove_installation step fails.
        """
        ok = {"success": True, "output": "done", "error": ""}
        remove_fail = {"success": False, "output": "", "error": "permission denied"}
        responses = [ok, ok, remove_fail]
        with patch("lib.hpc_client._run_mccli", side_effect=responses):
            result = hpc.undeploy(FAKE_TOKEN, FAKE_HOST, FAKE_PORT)
        assert result["success"] is False
        assert "remove_installation" in result["error"]


# ---------------------------------------------------------------------------
# SetupConfig
# ---------------------------------------------------------------------------


class TestSetupConfig:
    def test_url_constructed_from_version(self):
        cfg = hpc.SetupConfig(
            wstunnel_server_addr="example.com",
            wstunnel_server_port=8420,
            wstunnel_secret="mysecret",
            wstunnel_local_port=8420,
            wstunnel_version="v10.5.5",
        )
        assert "v10.5.5" in cfg.wstunnel_url
        assert "wstunnel" in cfg.wstunnel_url.lower()
        assert cfg.wstunnel_url.startswith("https://")

    def test_default_wstunnel_bin_set(self):
        cfg = hpc.SetupConfig(
            wstunnel_server_addr="example.com",
            wstunnel_server_port=8420,
            wstunnel_secret="secret",
            wstunnel_local_port=8420,
        )
        assert cfg.wstunnel_bin is not None
        assert "wstunnel" in cfg.wstunnel_bin

    def test_custom_wstunnel_bin_preserved(self):
        cfg = hpc.SetupConfig(
            wstunnel_server_addr="example.com",
            wstunnel_server_port=8420,
            wstunnel_secret="secret",
            wstunnel_local_port=8420,
            wstunnel_bin="/custom/path/wstunnel",
        )
        assert cfg.wstunnel_bin == "/custom/path/wstunnel"
