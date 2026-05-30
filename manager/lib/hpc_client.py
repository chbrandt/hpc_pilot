"""
hpc_client.py — mccli / SSH wrapper for HPC deployments.

Analogous to helm_client.py for Helm charts, this module wraps the
``mccli`` CLI (motley-cue client) via subprocess so callers never
have to call subprocess directly.

Every public function accepts the EGI Check-in *access token* that is
already stored in the Flask session, together with the HPC host details,
and returns a plain dict  ``{success: bool, output: str, error: str|None}``.

Prerequisites on the manager host
----------------------------------
- ``mccli``  (motley-cue client)  — wraps SSH with OIDC token auth
- ``flaat-userinfo``              — used to decode the token sub claim

The remote HPC node needs no pre-installed software other than a working
``python3``, ``curl``, and ``pip`` / ``pip3``; the :mod:`hpc_setup` step
functions take care of installing wstunnel + supervisord via mccli.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

from .token_auth import validate_token  # noqa: F401
# from . import hpc_setup

logger = logging.getLogger(__name__)

# Timeout for mccli SSH commands that may take a while (e.g. downloads)
_SHORT_TIMEOUT = 30   # seconds – simple probes (whoami, ls, …)
_LONG_TIMEOUT  = 300  # seconds – setup steps (pip install, curl download)

# Remote installation base directory
_REMOTE_BASE_DIR = "~/.hpc-pilot"

# Enable/diable verbose (debug-level) output from mccli subprocess calls.
_VERBOSE = False

# ── Internal helper ────────────────────────────────────────────────────


def _copy_mccli(
    token: str,
    hpc_host: str,
    ssh_port: int,
    local_path: str,
    remote_path: str,
    timeout: int = _SHORT_TIMEOUT,
    verbose: bool = _VERBOSE
) -> dict:
    """
    Copy a local file to the remote HPC node using ``mccli scp``.

    Parameters
    ----------
    token      : EGI Check-in access token (from Flask session).
    hpc_host   : Hostname or IP of the HPC login/edge node.
    ssh_port   : SSH port on the HPC node (usually 22).
    local_path : Path to the local file to upload.
    remote_path: Path to the remote file to upload.
    timeout    : Subprocess timeout in seconds.
    verbose    : If True, enables debug-level output from mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    validate_token(token)  # raises if invalid, so we don't run mccli with a bad token

    #TODO: Consider using the Python API of motley-cue instead of subprocess/CLI
    # - https://github.com/dianagudu/mccli/blob/develop/mccli/mccli.py
    # - https://mccli.readthedocs.io/en/latest/api/utils/mccli.ssh_wrapper.html
    cmd = [
        "mccli"
    ]
    if verbose:
        cmd.append("--debug")
    cmd += [
        "--token", token,
        "scp",
        "-q",
        "-P", str(ssh_port),
        f"{local_path}",
        f"{hpc_host}:{remote_path}"
    ]

    logger.info("mccli -> %s: %s", hpc_host, f"scp {local_path} {remote_path}")

    print("Running mccli command:", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        stdout = result.stdout.decode(errors="replace").replace("\r\n", "\n")

        # There is never stderr/unsuccsessful returncode from mccli!
        # stderr = result.stderr.decode(errors="replace").replace("\r\n", "\n")
        # success = result.returncode == 0
        success = not len(stdout.strip())  # mccli scp outputs nothing on success

        return {
            "success": success,
            "output": f"File copied successfully." if success else "",
            "error": stdout if not success else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": f"mccli command timed out after {timeout}s.",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": (
                "mccli not found on PATH. "
                "Install the motley-cue client: pip install mccli"
            ),
        }

def _run_mccli(
    token: str,
    hpc_host: str,
    ssh_port: int,
    command: str,
    stdin_data: Optional[bytes] = None,
    timeout: int = _SHORT_TIMEOUT,
    verbose: bool = _VERBOSE
) -> dict:
    """
    Execute *command* on *hpc_host* via ``mccli`` and return the result.

    Parameters
    ----------
    token      : EGI Check-in access token (from Flask session).
    hpc_host   : Hostname or IP of the HPC login/edge node.
    ssh_port   : SSH port on the HPC node (usually 22).
    command    : Shell command to run remotely.
    stdin_data : Optional bytes to pass as stdin (e.g. a config file).
    timeout    : Subprocess timeout in seconds.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    validate_token(token)  # raises if invalid, so we don't run mccli with a bad token

    #TODO: Consider using the Python API of motley-cue instead of subprocess/CLI
    # - https://github.com/dianagudu/mccli/blob/develop/mccli/mccli.py
    # - https://mccli.readthedocs.io/en/latest/api/utils/mccli.ssh_wrapper.html
    cmd = [
        "mccli"
    ]
    if verbose:
        cmd.append("--debug")
    cmd += [
        "--token", token,
        "ssh",
        "-p", str(ssh_port),
        "-o LogLevel=error",
        hpc_host,
        command + ' || echo "__MCCLI_COMMAND_FAILED__"',
    ]

    logger.info("mccli -> %s: %s", hpc_host, command)

    try:
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
        )
        stdout = result.stdout.decode(errors="replace").replace("\r\n", "\n")

        # There is never stderr/unsuccsessful returncode from mccli!
        # stderr = result.stderr.decode(errors="replace").replace("\r\n", "\n")
        # success = result.returncode == 0
        success = "__MCCLI_COMMAND_FAILED__" not in stdout
        # stdout = stdout.replace("__MCCLI_COMMAND_FAILED__", "").strip()

        return {
            "success": success,
            "output": stdout if success else "",
            "error": stdout if not success else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": f"mccli command timed out after {timeout}s.",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": (
                "mccli not found on PATH. "
                "Install the motley-cue client: pip install mccli"
            ),
        }


# ── Public API ─────────────────────────────────────────────────────────


def check_connection(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Verify that the HPC node is reachable and the token is accepted.

    Runs ``whoami`` remotely and returns the success of that.

    Returns
    -------
    bool  True if the connection succeeded, False otherwise
    """
    result = _run_mccli(token, hpc_host, ssh_port, "whoami", timeout=_SHORT_TIMEOUT)
    # username = result["output"].strip() if result["success"] else None
    return result["success"]


def check_installed(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Check whether the HPC Pilot stack is already installed on the remote node.

    Returns
    -------
    bool  True if the stack is installed, False otherwise
    """
    #TODO: Consider a more robust check than just the presence of the base dir.
    #   e.g. check for the supervisord.conf file or a running wstunnel process.
    result = _run_mccli(
        token, hpc_host, ssh_port,
        f"[ -d {_REMOTE_BASE_DIR} ] && echo installed || echo missing",
        timeout=_SHORT_TIMEOUT,
    )
    return result["success"] and "installed" in result["output"]


def get_status(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Query supervisorctl for the status of all managed services.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    #TODO: Improve the status check for something more meaningful/structured.
    status = _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf status",
        timeout=_SHORT_TIMEOUT,
    )
    return status


def start_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Start all supervisord-managed services (wstunnel)."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf start all",
        timeout=_SHORT_TIMEOUT,
    )


def stop_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Stop all supervisord-managed services."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf stop all",
        timeout=_SHORT_TIMEOUT,
    )


def deploy(
    token: str,
    hpc_host: str,
    ssh_port: int,
    wstunnel_server: str,
    wstunnel_port: int,
    wstunnel_secret: str,
    wstunnel_local_port: Optional[int] = None,
) -> dict:
    """
    Install wstunnel + supervisord on the remote HPC node and start them.

    Each setup step is executed individually on the remote node via ``mccli``
    / SSH using the step functions from :mod:`hpc_setup`.  No scripts are
    piped or copied to the remote node.

    Parameters
    ----------
    token            : EGI Check-in access token.
    hpc_host         : HPC login/edge node hostname or IP.
    ssh_port         : SSH port (usually 22).
    wstunnel_server  : Public hostname of the K8s-side wstunnel server
                       (e.g. ``user-abc123.dev.local``).
    wstunnel_port    : Port the wstunnel server listens on (e.g. 8420).
    wstunnel_secret  : Shared secret / bearer token for the tunnel.
    wstunnel_local_port : Local TCP port on the HPC node that wstunnel
                          exposes (defaults to *wstunnel_port*).

    Returns
    -------
    dict  ``{success, output, error}``
    """
    if wstunnel_local_port is None:
        wstunnel_local_port = wstunnel_port

    cfg = SetupConfig(
        wstunnel_server_addr=wstunnel_server,
        wstunnel_server_port=wstunnel_port,
        wstunnel_secret=wstunnel_secret,
        wstunnel_local_port=wstunnel_local_port,
    )

    # Build a runner that dispatches every remote command through _run_mccli.
    def runner(command: str, stdin_data: Optional[bytes] = None, timeout: int = _SHORT_TIMEOUT) -> dict:
        return _run_mccli(token, hpc_host, ssh_port, command, stdin_data=stdin_data, timeout=timeout)
    def copier(local_path: str, remote_path: str, timeout: int = _SHORT_TIMEOUT) -> dict:
        return _copy_mccli(token, hpc_host, ssh_port, local_path, remote_path, timeout=timeout)

    logger.info(
        "Deploying HPC stack to %s (wstunnel → wss://%s:%s)",
        hpc_host, wstunnel_server, wstunnel_port,
    )

    steps = [
        ("setup_directories",      lambda: setup_directories(runner)),
        ("install_wstunnel",       lambda: install_wstunnel(runner, cfg, force=False)),
        ("install_supervisord",    lambda: install_supervisord(runner)),
        ("copy_supervisord_conf", lambda: copy_supervisord_conf(copier)),
        ("start_supervisord",      lambda: start_supervisord(runner)),
        ("check_status",           lambda: check_status(runner)),
    ]

    #TODO: Flush output after each step so we can show the progress.
    all_output: list[str] = []
    for step_name, step_fn in steps:
        logger.info("Deploy step: %s", step_name)
        result = step_fn()
        if result.get("output"):
            all_output.append(f"[{step_name}] {result['output']}")
        if not result["success"]:
            return {
                "success": False,
                "output": "\n".join(all_output),
                "error": f"[{step_name}] {result.get('error', '')}",
            }

    return {
        "success": True,
        "output": "\n".join(all_output),
        "error": "",
    }


# ── Utilities ──────────────────────────────────────────────────────────


def _sh_quote(value: str) -> str:
    """
    Wrap *value* in single quotes suitable for passing to a remote shell.

    Single quotes inside the value are escaped by ending the quote, inserting
    a literal single-quote, and re-opening.
    """
    return "'" + value.replace("'", "'\\''") + "'"

"""
hpc_setup.py — Remote HPC node setup step functions.

Each public function accepts a *runner* callable that executes a shell command
on the remote HPC node (via mccli / SSH) and returns the standard
``{success, output, error}`` dict used throughout hpc_client.py:

    runner(command: str, stdin_data: bytes | None = None, timeout: int = …) -> dict

The functions translate the logic from manager/hpc/setup.sh into Python but
the actual execution always happens **remotely** through the runner —
no local subprocess calls are made here.

Usage (orchestrated by hpc_client.deploy):
    def runner(cmd, stdin_data=None, timeout=_SHORT_TIMEOUT):
        return _run_mccli(token, hpc_host, ssh_port, cmd, stdin_data, timeout)

    cfg = SetupConfig(wstunnel_server, wstunnel_port, wstunnel_secret, local_port)
    hpc_setup.setup_directories(runner)
    hpc_setup.install_wstunnel(runner, cfg)
    hpc_setup.install_supervisord(runner)
    hpc_setup.write_supervisord_conf(runner, cfg)
    hpc_setup.start_supervisord(runner)
    hpc_setup.check_status(runner)
"""

# from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

# Type alias for the runner callable provided by hpc_client
Runner = Callable[..., dict]

# Timeout constants (seconds) – mirrored from hpc_client for convenience.
# Step functions that can take a long time pass these explicitly to runner().
_SHORT_TIMEOUT = 30   # simple checks, mkdir, supervisorctl …
_LONG_TIMEOUT  = 300  # curl download + pip install

# Local path to supervisord.conf template file
_SUPERVISOR_CONF_TEMPLATE = os.path.join(
    os.path.dirname(__file__), "..", "hpc", "supervisord.conf.tpl"
)

# ── Remote path constants ──────────────────────────────────────────────────────
# All paths use shell variable $HOME so they expand correctly on the remote node
# when passed as part of a shell command string.
# The supervisord *config file* uses supervisord's own %(ENV_HOME)s syntax
# instead of $HOME because the file is written by Python (no shell expansion).

_BASE_DIR         = "$HOME/.hpc-pilot"
_TMP_DIR          = f"{_BASE_DIR}/tmp"
_BIN_DIR          = f"{_BASE_DIR}/bin"
_LOG_DIR          = f"{_BASE_DIR}/log"
_CONF_DIR         = f"{_BASE_DIR}/config"
_VENV             = f"{_BASE_DIR}/venv"
_SUPERVISOR_BIN   = f"{_VENV}/bin/supervisord"
_SUPERVISOR_CONF  = f"{_VENV}/supervisord.conf"
_SUPERVISOR_SOCK  = f"{_VENV}/supervisord.sock"
_SUPERVISOR_PID   = f"{_VENV}/supervisord.pid"

_WSTUNNEL_BIN     = f"{_BIN_DIR}/wstunnel"
_WSTUNNEL_VERSION_DEFAULT = "v10.5.5"


# ── Configuration ──────────────────────────────────────────────────────────────


@dataclass
class SetupConfig:
    """
    Parameters for the wstunnel/supervisord deployment on a remote HPC node.

    Parameters
    ----------
    wstunnel_server_addr : Public hostname of the K8s-side wstunnel server.
    wstunnel_server_port : Port the wstunnel server listens on (e.g. 8420).
    wstunnel_secret     : Shared bearer-token secret for the tunnel.
    wstunnel_local_port : Local TCP port on the HPC node wstunnel will expose.
    wstunnel_version    : GitHub release tag to download (default v10.1.0).
    """

    wstunnel_server_addr: str
    wstunnel_server_port: int
    wstunnel_secret: str
    wstunnel_local_port: int
    wstunnel_version: str = _WSTUNNEL_VERSION_DEFAULT

    def __post_init__(self) -> None:
        ver = self.wstunnel_version
        self.wstunnel_url: str = (
            f"https://github.com/erebe/wstunnel/releases/download/"
            f"{ver}/wstunnel_{ver.lstrip('v')}_linux_amd64.tar.gz"
        )


# ── Step functions ─────────────────────────────────────────────────────────────


def setup_directories(runner: Runner) -> dict:
    """
    Create the HPC Pilot base directories on the remote node.

    Equivalent to: ``mkdir -p ~/.hpc-pilot/{bin,logs,config}``

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = (
        f"mkdir -p {_TMP_DIR} {_BIN_DIR} {_LOG_DIR} {_CONF_DIR}" 
        f" && echo 'Directories ready: {_BASE_DIR}'"
    )
    return runner(cmd, timeout=_SHORT_TIMEOUT)


def install_wstunnel(runner: Runner, cfg: SetupConfig, force: bool = False) -> dict:
    """
    Download and install the wstunnel binary into ``~/.hpc-pilot/bin/``.

    Skips the download if the binary is already present and executable (idempotent).
    Uses ``curl`` + ``tar`` + ``install`` — all standard HPC tools.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.
    cfg    : :class:`SetupConfig` — provides ``wstunnel_version`` and ``wstunnel_url``.
    force  : if True, forces re-download and re-install even if the binary is present.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = (
        f"curl --fail --silent --show-error -L -o {_TMP_DIR}/wstunnel.tar.gz {cfg.wstunnel_url}"
        f" && tar -xzf {_TMP_DIR}/wstunnel.tar.gz -C {_TMP_DIR}"
        f" && install {_TMP_DIR}/wstunnel {_WSTUNNEL_BIN}" 
        f" && rm {_TMP_DIR}/wstunnel.tar.gz"
        f" && echo `{_WSTUNNEL_BIN} --version` installed"
    )

    if not force:
        cmd = (
            f"if [ ! -x {_WSTUNNEL_BIN} ]; then "
            f"{cmd}"
            f"; else "
            f"echo `{_WSTUNNEL_BIN} --version` already installed"
            f"; fi"
        )

    return runner(cmd, timeout=_LONG_TIMEOUT)


def install_supervisord(runner: Runner, force: bool = False) -> dict:
    """
    Ensure ``supervisord`` is available on the remote node, installing it via
    ``pip3`` or ``pip`` (``--user``) if not already present.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.
    force  : if True, forces re-installation even if the binary is already present.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = (
        f"python3 -m venv {_VENV} "
        f" && {_VENV}/bin/pip install --upgrade pip "
        f" && {_VENV}/bin/pip install --quiet supervisor"
        f" && echo supervisord installed in virtualenv '{_VENV}'"
    )

    if not force:
        cmd = (
            f"if [ ! -x {_SUPERVISOR_BIN} ]; then "
            f"{cmd}"
            f"; else "
            f"echo supervisord already installed: `{_SUPERVISOR_BIN} --version`"
            f"; fi"
        )

    return runner(cmd, timeout=_LONG_TIMEOUT)


def copy_supervisord_conf(copier: Runner, cfg: SetupConfig) -> dict:
    """
    Copy the supervisord.conf file to the remote node.

    Parameters
    ----------
    copier : callable that copies a local file to the remote node via mccli.
    cfg    : :class:`SetupConfig` — provides configuration values for the supervisord.conf file.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    result_copy = copier(local_path=_SUPERVISOR_CONF_TEMPLATE, 
                    remote_path=_SUPERVISOR_CONF.replace("$HOME", "~"), 
                    timeout=_SHORT_TIMEOUT)
    
    return result_copy


def start_supervisord(runner: Runner) -> dict:
    """
    Start ``supervisord`` on the remote node, or reload its config if already running.

    If the PID file exists and the process is alive the function issues
    ``supervisorctl reread / update / restart all``.  Otherwise it starts a
    fresh ``supervisord`` daemon and waits up to 2 s for the Unix socket.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
#     # Subshell ensures exit 1 doesn't kill the outer SSH shell.
#     cmd = f"""\
# (
#     export PATH="$HOME/.local/bin:$PATH"
#     CONF="{_SUPERVISOR_CONF}"
#     PID_FILE="{_SUPERVISOR_PID}"
#     SOCK="{_SUPERVISOR_SOCK}"
#     if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
#         supervisorctl -c "$CONF" reread  >/dev/null 2>&1 || true
#         supervisorctl -c "$CONF" update  >/dev/null 2>&1 || true
#         supervisorctl -c "$CONF" restart all 2>&1 || true
#         echo "Config reloaded and services restarted"
#     else
#         supervisord -c "$CONF" \
#             || {{ echo 'Failed to start supervisord' >&2; exit 1; }}
#         sleep 2
#         if [ -S "$SOCK" ]; then
#             echo "supervisord started successfully"
#         else
#             echo 'supervisord socket not found after startup' >&2
#             exit 1
#         fi
#     fi
# )"""
    cmd = "echo 'yay'"
    return runner(cmd, timeout=_SHORT_TIMEOUT)


def check_status(runner: Runner) -> dict:
    """
    Query ``supervisorctl status`` on the remote node.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = f"{_SUPERVISOR_BIN} -c {_SUPERVISOR_CONF} status"
    return runner(cmd, timeout=_SHORT_TIMEOUT)
