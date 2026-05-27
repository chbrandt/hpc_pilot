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

The remote HPC node needs no pre-installed software; the setup script
downloaded and run via mccli takes care of wstunnel + supervisord.
"""

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Timeout for mccli SSH commands that may take a while (e.g. downloads)
_SHORT_TIMEOUT = 30   # seconds – simple probes (whoami, ls, …)
_LONG_TIMEOUT  = 300  # seconds – setup script (pip install, curl download)

# Remote installation base directory
_REMOTE_BASE_DIR = "~/.hpc-pilot"

# Location of the setup.sh script relative to this file's package directory.
# The script lives in manager/hpc/ alongside the supervisord template.
_SETUP_SH = os.path.join(os.path.dirname(__file__), "..", "hpc", "setup.sh")


# ── Internal helper ────────────────────────────────────────────────────


def _run_mccli(
    token: str,
    hpc_host: str,
    ssh_port: int,
    command: str,
    stdin_data: Optional[bytes] = None,
    timeout: int = _SHORT_TIMEOUT,
) -> dict:
    """
    Execute *command* on *hpc_host* via ``mccli`` and return the result.

    Parameters
    ----------
    token      : EGI Check-in access token (from Flask session).
    hpc_host   : Hostname or IP of the HPC login/edge node.
    ssh_port   : SSH port on the HPC node (usually 22).
    command    : Shell command to run remotely.
    stdin_data : Optional bytes to pass as stdin (e.g. a shell script).
    timeout    : Subprocess timeout in seconds.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = [
        "mccli",
        "--token", token,
        "ssh",
        "-p", str(ssh_port),
        hpc_host,
        command,
    ]

    logger.info("mccli → %s: %s", hpc_host, command[:120])

    try:
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
        )
        stdout = result.stdout.decode(errors="replace").replace("\r\n", "\n")
        stderr = result.stderr.decode(errors="replace").replace("\r\n", "\n")
        success = result.returncode == 0
        return {
            "success": success,
            "output": stdout,
            "error": stderr if not success else None,
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

    Runs ``whoami`` remotely and returns the remote username on success.

    Returns
    -------
    dict  ``{success, username, output, error}``
    """
    result = _run_mccli(token, hpc_host, ssh_port, "whoami", timeout=_SHORT_TIMEOUT)
    username = result["output"].strip() if result["success"] else None
    return {**result, "username": username}


def check_installed(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Check whether the HPC Pilot stack is already installed on the remote node.

    Returns
    -------
    dict  ``{success, installed: bool, output, error}``
    """
    result = _run_mccli(
        token, hpc_host, ssh_port,
        f"[ -d {_REMOTE_BASE_DIR} ] && echo installed || echo missing",
        timeout=_SHORT_TIMEOUT,
    )
    installed = result["success"] and "installed" in result["output"]
    return {**result, "installed": installed}


def get_status(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Query supervisorctl for the status of all managed services.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf status 2>&1 || true",
        timeout=_SHORT_TIMEOUT,
    )


def start_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Start all supervisord-managed services (wstunnel)."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf start all 2>&1",
        timeout=_SHORT_TIMEOUT,
    )


def stop_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Stop all supervisord-managed services."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"supervisorctl -c {_REMOTE_BASE_DIR}/supervisord.conf stop all 2>&1",
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

    The ``setup.sh`` script (located at ``manager/hpc/setup.sh``) is read from
    disk, rendered with the provided parameters, and piped into ``bash -s`` via
    ``mccli`` so the HPC node does not need to have the repository cloned.

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

    setup_sh_path = os.path.normpath(_SETUP_SH)
    try:
        with open(setup_sh_path, "rb") as fh:
            setup_script = fh.read()
    except OSError as exc:
        return {
            "success": False,
            "output": "",
            "error": f"Cannot read setup.sh: {exc}",
        }

    # Build the environment prefix so setup.sh picks up parameters
    env_prefix = (
        f"WSTUNNEL_SERVER={_sh_quote(wstunnel_server)} "
        f"WSTUNNEL_PORT={wstunnel_port} "
        f"WSTUNNEL_SECRET={_sh_quote(wstunnel_secret)} "
        f"WSTUNNEL_LOCAL_PORT={wstunnel_local_port} "
    )

    remote_command = f"{env_prefix} bash -s"

    logger.info(
        "Deploying HPC stack to %s (wstunnel → wss://%s:%s)",
        hpc_host, wstunnel_server, wstunnel_port,
    )

    return _run_mccli(
        token, hpc_host, ssh_port,
        remote_command,
        stdin_data=setup_script,
        timeout=_LONG_TIMEOUT,
    )


# ── Utilities ──────────────────────────────────────────────────────────


def _sh_quote(value: str) -> str:
    """
    Wrap *value* in single quotes suitable for passing to a remote shell.

    Single quotes inside the value are escaped by ending the quote, inserting
    a literal single-quote, and re-opening.
    """
    return "'" + value.replace("'", "'\\''") + "'"
