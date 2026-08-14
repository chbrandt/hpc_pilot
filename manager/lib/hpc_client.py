"""
hpc_client.py — mccli / SSH wrapper for HPC deployments.

Analogous to helm_client.py for Helm charts, this module wraps the
``mccli`` CLI (motley-cue client) via subprocess so callers never
have to call subprocess directly.

Every public function accepts the EGI Check-in *access token* that is
already stored in the Flask session, together with the HPC host details,
and returns a plain dict  ``{success: bool, output: str, error: str|None}``
(the two ``check_*`` probes are the exception and return a bool).

Prerequisites on the manager host
----------------------------------
- ``mccli``  (motley-cue client)  — wraps SSH with OIDC token auth
- ``flaat-userinfo``              — used by mccli to decode the token

The remote HPC node needs no pre-installed software other than a working
``python3``, ``curl``, and ``pip``; the setup step functions below install
wstunnel + supervisord into a virtualenv under ``~/.pilot`` via mccli/SSH.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

from .token_auth import validate_token  # noqa: F401

logger = logging.getLogger(__name__)

# Timeout for mccli SSH commands that may take a while (e.g. downloads)
_SHORT_TIMEOUT = 30   # seconds – simple probes (whoami, ls, …)
_LONG_TIMEOUT  = 300  # seconds – setup steps (pip install, curl download)

# Enable/disable verbose (debug-level) output from mccli subprocess calls.
_VERBOSE = False

# Default interLink plugin — referenced by deploy() before the full plugin
# table is declared in the HPC setup section below.
_DEFAULT_PLUGIN = "echo"


#############################################################################
# Helpers 
# =======

# Jinja2 utils
# ------------
import tempfile
import os
import jinja2

def _render_template(filename: str, context: dict) -> str:
    """
    Return the rendered content of a Jinja2 template file with the given context.
    """
    with open(filename) as f:
        template = jinja2.Template(f.read())
    return template.render(**context)


def _write_to_tempfile(text: str) -> tempfile.NamedTemporaryFile:
    """"
    Return name of the temporary file after writing the given text to it.
    """
    # Define your custom directory
    temp_dir = "/tmp/pilot_temp"
    
    # Ensure the custom directory exists (optional, but good practice)
    os.makedirs(temp_dir, exist_ok=True)
    
    # Use dir, prefix, and suffix to control the file's location and name
    try:
        with tempfile.NamedTemporaryFile(
          dir=temp_dir, 
          mode='w+', 
          delete=False
        ) as temp_file:
            temp_file.write(text)
            temp_file.flush()
            return temp_file
    except Exception as e:
        raise RuntimeError(f"Failed to write to temporary file: {e}")
    

# mccli wrappers 
# --------------
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

    print("Running mccli command:", " ".join(
        [s for i,s in enumerate(cmd) if i != cmd.index("--token") + 1]))

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
        stdout = stdout.replace("__MCCLI_COMMAND_FAILED__", "").strip()

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


# def _sh_quote(value: str) -> str:
#     """
#     Wrap *value* in single quotes suitable for passing to a remote shell.
#
#     Single quotes inside the value are escaped by ending the quote, inserting
#     a literal single-quote, and re-opening.
#     """
#     return "'" + value.replace("'", "'\\''") + "'"


###########################################################################


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
        f"[ -d {_BASE_DIR} ] && echo installed || echo missing",
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
        f"source {_BASE_DIR}/bin/activate && supervisorctl status",
        timeout=_SHORT_TIMEOUT,
    )
    return status


def start_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Start all supervisord-managed services (wstunnel)."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"source {_BASE_DIR}/bin/activate && supervisorctl start all",
        timeout=_SHORT_TIMEOUT,
    )


def stop_services(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """Stop all supervisord-managed services."""
    return _run_mccli(
        token, hpc_host, ssh_port,
        f"source {_BASE_DIR}/bin/activate && supervisorctl stop all",
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
    plugin: str = _DEFAULT_PLUGIN,
) -> dict:
    """
    Install wstunnel + supervisord on the remote HPC node and start them.

    Each setup step is executed individually on the remote node via ``mccli``
    / SSH using the step functions in this module.  No scripts are
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
    plugin           : InterLink plugin to install on the HPC node.
                       Allowed values: ``"echo"``, ``"docker"``, ``"slurm"``
                       (default: ``"echo"``).

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
        "Deploying HPC stack to %s (wstunnel → wss://%s:%s, plugin=%s)",
        hpc_host, wstunnel_server, wstunnel_port, plugin,
    )

    steps = [
        ("setup_directories",      lambda: setup_directories(runner)),
        ("install_supervisord",    lambda: install_supervisord(runner)),
        ("copy_supervisord_conf",  lambda: copy_supervisord_conf(copier, cfg)),
        ("install_wstunnel",       lambda: install_wstunnel(runner, cfg)),
        ("install_plugin",         lambda: install_plugin(runner, cfg, plugin=plugin)),
        ("copy_plugin_conf",       lambda: copy_plugin_conf(copier, cfg, plugin=plugin)),
        ("start_supervisord",      lambda: start_supervisord(runner)),
        ("check_status",           lambda: check_status(runner)),
    ]

    #TODO: Flush output after each step so we can show the progress.
    all_output: list[str] = []
    for step_name, step_fn in steps:
        logger.info("Deploy step: %s", step_name)
        result = step_fn()
        if result.get("output"):
            step_output = f"[{step_name}] {result['output']}"
            print(step_output)
            all_output.append(step_output)
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


def undeploy(token: str, hpc_host: str, ssh_port: int = 22) -> dict:
    """
    Stop all services and remove the HPC Pilot installation from the remote node.

    This is the inverse of :func:`deploy`.  It performs two remote steps:

    1. Gracefully stop all supervisord-managed services and shut down supervisord.
       This step tolerates failures (e.g. supervisord was never started).
    2. Remove the ``~/.pilot`` base directory entirely.

    Parameters
    ----------
    token    : EGI Check-in access token.
    hpc_host : HPC login/edge node hostname or IP.
    ssh_port : SSH port (usually 22).

    Returns
    -------
    dict  ``{success, output, error}``
    """
    def runner(command: str, timeout: int = _SHORT_TIMEOUT) -> dict:
        return _run_mccli(token, hpc_host, ssh_port, command, timeout=timeout)

    logger.info("Undeploying HPC stack from %s", hpc_host)

    steps = [
        ("stop_services", lambda: stop_services(token, hpc_host, ssh_port)),
        ("stop_supervisord", lambda: stop_supervisord(runner)),
        ("remove_installation", lambda: remove_installation(runner))
    ]

    #TODO: Flush output after each step so we can show the progress.
    all_output: list[str] = []
    for step_name, step_fn in steps:
        logger.info("Undeploy step: %s", step_name)
        result = step_fn()
        if result.get("output"):
            step_output = f"[{step_name}] {result['output']}"
            print(step_output)
            all_output.append(step_output)
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


#############################################################################
# HPC setup
# =========
#
# Remote HPC node setup step functions.
#
# Each public function accepts a *runner* callable that executes a shell command
# on the remote HPC node (via mccli / SSH) and returns the standard
# ``{success, output, error}`` dict used throughout hpc_client.py:
#
#     runner(command: str, stdin_data: bytes | None = None, timeout: int = …) -> dict
#
# The functions translate the logic from manager/hpc/setup.sh into Python but
# the actual execution always happens **remotely** through the runner —
# no local subprocess calls are made here.
#
#############################################################################

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
hpc = {
    "pilot": {
        "supervisord_conf": {
            "template": os.path.join(
                os.path.dirname(__file__), "..", 
                "hpc", "pilot","supervisord.conf.jinja"
            )
        }
    },
    "plugins": {
        "echo": {
            "config_template": os.path.join(
                os.path.dirname(__file__), "..", 
                "hpc", "plugins", "echo", "InterLinkConfig.yaml"
            )
        },
        "docker": {
            "config_template": os.path.join(
                os.path.dirname(__file__), "..", 
                "hpc", "plugins", "docker", "InterLinkConfig.yaml"
            )
        },
        "slurm": {
            "config_template": os.path.join(
                os.path.dirname(__file__), "..", 
                "hpc", "plugins", "slurm", "InterLinkConfig.yaml"
            )
        }
    }
}

SUPERVISOR_CONF_TEMPLATE = hpc["pilot"]["supervisord_conf"]["template"]
PLUGIN_CONFIG_TEMPLATES = {
    "echo": hpc["plugins"]["echo"]["config_template"],
    "docker": hpc["plugins"]["docker"]["config_template"],
    "slurm": hpc["plugins"]["slurm"]["config_template"]
}

# ── Remote path constants ──────────────────────────────────────────────────────
# All paths use shell variable $HOME so they expand correctly on the remote node
# when passed as part of a shell command string.
# The supervisord *config file* uses supervisord's own %(ENV_HOME)s syntax
# instead of $HOME because the file is written by Python (no shell expansion).

_BASE_DIR         = "$HOME/.pilot"
_TMP_DIR          = f"{_BASE_DIR}/tmp"
_BIN_DIR          = f"{_BASE_DIR}/bin"
_LOG_DIR          = f"{_BASE_DIR}/log"
# _ETC_DIR          = f"{_BASE_DIR}/etc"

_SUPERVISOR_CONF  = f"{_BIN_DIR}/../supervisord.conf"
# _SUPERVISORD_BIN  = f"{_BIN_DIR}/supervisord"
# _SUPERVISORCTL_BIN= f"{_BIN_DIR}/supervisorctl"
# _SUPERVISOR_SOCK  = f"{_BASE_DIR}/supervisord.sock"
# _SUPERVISOR_PID   = f"{_BASE_DIR}/supervisord.pid"

# _WSTUNNEL_BIN     = f"{_BIN_DIR}/wstunnel"
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
    wstunnel_bin: Optional[str] = None  # computed in __post_init__   

    def __post_init__(self) -> None:
        ver = self.wstunnel_version
        self.wstunnel_url: str = (
            f"https://github.com/erebe/wstunnel/releases/download/"
            f"{ver}/wstunnel_{ver.lstrip('v')}_linux_amd64.tar.gz"
        )
        if self.wstunnel_bin is None:
            self.wstunnel_bin = os.path.join(_BIN_DIR, "wstunnel")


# ── Step functions ─────────────────────────────────────────────────────────────


def setup_directories(runner: Runner) -> dict:
    """
    Create the HPC Pilot base directories on the remote node.

    Equivalent to: ``mkdir -p ~/.pilot/{tmp,bin,log}``

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = (
        f"echo 'Creating directories: {_TMP_DIR} {_BIN_DIR} {_LOG_DIR}'"
        f" && mkdir -p {_TMP_DIR} {_BIN_DIR} {_LOG_DIR}" 
        f" && echo 'Directories ready'"
    )
    return runner(cmd, timeout=_SHORT_TIMEOUT)


# ── Plugin definitions ──────────────────────────────────────────────────────

# Mapping of plugin name → pip-installable package URL/reference.
# The "echo" plugin is a minimal test plugin that echoes job requests.
# "docker" and "slurm" are placeholders — update the URLs when packages are
# published.
_PLUGIN_PACKAGES: dict[str, dict[str, str]] = {
    "echo": {
        "type": "pip",
        "url": "https://github.com/chbrandt/interlink-echo-plugin/archive/refs/tags/v0.2.1.tar.gz"
        },
    "docker": {
        "type": "binary",
        "url": "https://github.com/chbrandt/interlink-docker-plugin/releases/download/v0.0.3/docker-plugin_Linux_x86_64"
        },
    "slurm": {
        "type": "binary",
        "url": "https://github.com/interlink-hq/interlink-slurm-plugin/releases/download/0.6.2-pre5/interlink-sidecar-slurm_Linux_x86_64" # placeholder — update when published
        },     
}

_VALID_PLUGINS = tuple(_PLUGIN_PACKAGES.keys())


def install_plugin(runner: Runner, cfg: SetupConfig, plugin: str = _DEFAULT_PLUGIN) -> dict:
    """
    Install the specified interLink plugin on the remote HPC node.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.
    cfg    : :class:`SetupConfig` — provides base directory paths.
    plugin : Name of the plugin to install.  Allowed values: ``"echo"``,
             ``"docker"``, ``"slurm"`` (default: ``"echo"``).

    Returns
    -------
    dict  ``{success, output, error}``
    """
    if plugin not in _VALID_PLUGINS:
        logger.warning(
            "Unknown plugin '%s'; falling back to '%s'. Valid options: %s",
            plugin, _DEFAULT_PLUGIN, _VALID_PLUGINS,
        )
        plugin = _DEFAULT_PLUGIN

    pkg_conf = _PLUGIN_PACKAGES[plugin]
    logger.info("Installing interLink plugin '%s'", plugin)

    assert isinstance(pkg_conf, dict)
    if plugin == "echo":
        package_url = pkg_conf["url"]
        cmd = (
            f"source {_BASE_DIR}/bin/activate"
            f" && pip install --quiet {package_url}"
            f" && ln -sf {_BIN_DIR}/interlink-echo-plugin {_BIN_DIR}/plugin"
            f" && chmod +x {_BIN_DIR}/plugin"
            f" && echo 'Plugin {plugin} installed'"
        )
    elif plugin == "docker":
        package_url = pkg_conf["url"]
        cmd = (
            f"curl --fail --silent --show-error -L -o {_BIN_DIR}/plugin {package_url}"
            f" && chmod +x {_BIN_DIR}/plugin"
            f" && echo 'Plugin {plugin} installed'"
        )
    elif plugin == "slurm":
        package_url = pkg_conf["url"]
        cmd = (
            f"curl --fail --silent --show-error -L -o {_BIN_DIR}/plugin {package_url}"
            f" && chmod +x {_BIN_DIR}/plugin"
            f" && echo 'Plugin {plugin} installed'"
        )
    else:
        cmd = (
            f"echo 'Unknown plugin {plugin}; no installation performed'"
        ) 

    return runner(cmd, timeout=_LONG_TIMEOUT)


def copy_plugin_conf(copier: Runner, cfg: SetupConfig, plugin: str = _DEFAULT_PLUGIN) -> dict:
    """
    Copy the plugin configuration file to the remote HPC node.

    Parameters
    ----------
    copier : callable that copies a local file to the remote node via mccli.
    cfg    : :class:`SetupConfig` — provides configuration values for the plugin.
    plugin : Name of the plugin whose config to copy.  Allowed values: ``"echo"``,
             ``"docker"``, ``"slurm"`` (default: ``"echo"``).

    Returns
    -------
    dict  ``{success, output, error}``
    """
    if plugin not in _VALID_PLUGINS:
        logger.warning(
            "Unknown plugin '%s'; falling back to '%s'. Valid options: %s",
            plugin, _DEFAULT_PLUGIN, _VALID_PLUGINS,
        )
        plugin = _DEFAULT_PLUGIN

    result_copy = _copy_jinja_template(copier, 
        {
            "sidecar_port": cfg.wstunnel_local_port,
            "plugin_data_root": os.path.join(_BASE_DIR, "data"),
        },
        PLUGIN_CONFIG_TEMPLATES[plugin],
        os.path.join(_BASE_DIR, f"InterLinkConfig.yaml")
    )

    return result_copy


def install_wstunnel(runner: Runner, cfg: SetupConfig, force: bool = False) -> dict:
    """
    Download and install the wstunnel binary into ``~/.pilot/bin/``.

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
        f" && install {_TMP_DIR}/wstunnel {cfg.wstunnel_bin}" 
        f" && rm {_TMP_DIR}/wstunnel.tar.gz"
        f" && echo `{cfg.wstunnel_bin} --version` installed"
    )

    if not force:
        cmd = (
            f"if [ ! -x {cfg.wstunnel_bin} ]; then "
            f"{cmd}"
            f"; else "
            f"echo `{cfg.wstunnel_bin} --version` already installed"
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
        f"python3.12 -m venv {_BASE_DIR}"
        f" && source {_BASE_DIR}/bin/activate"
        f" && pip install --quiet --upgrade pip "
        f" && pip install --quiet supervisor"
        f" && echo supervisord installed in virtualenv '{_BASE_DIR}'"
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

    # rendered_conf = _render_template(SUPERVISOR_CONF_TEMPLATE, {
    #     "wstunnel_bin": cfg.wstunnel_bin.replace("$HOME", "%(ENV_HOME)s"),
    #     "wstunnel_server_addr": cfg.wstunnel_server_addr,
    #     "wstunnel_local_port": cfg.wstunnel_local_port,
    #     "wstunnel_server_port": cfg.wstunnel_server_port,
    #     "wstunnel_secret": cfg.wstunnel_secret,
    #     "interlink_plugin_cmd": f"{_BASE_DIR}/bin/interlink-echo-plugin --port {cfg.wstunnel_local_port}".replace("$HOME", "%(ENV_HOME)s"),
    # })

    # #TODO: Delete the temporary file after copying, or use a context manager that does it automatically.
    # rendered_tempfile = _write_to_tempfile(rendered_conf)

    # logger.info("Copying supervisord.conf to remote node via mccli")
    # result_copy = copier(local_path=rendered_tempfile.name, 
    #                 remote_path=_SUPERVISOR_CONF.replace("$HOME", "~"), 
    #                 timeout=_SHORT_TIMEOUT)
    
    conf_values = {
        "wstunnel_bin": cfg.wstunnel_bin,
        "wstunnel_server_addr": cfg.wstunnel_server_addr,
        "wstunnel_local_port": cfg.wstunnel_local_port,
        "wstunnel_server_port": cfg.wstunnel_server_port,
        "wstunnel_secret": cfg.wstunnel_secret,
        "wstunnel_protocol": "wss" if cfg.wstunnel_server_port == 443 else "ws",
        # "interlink_plugin_cmd": f"{_BASE_DIR}/bin/plugin --port {cfg.wstunnel_local_port}",
        "plugin_bin": f"{_BASE_DIR}/bin/plugin",
        "plugin_conf": f"{_BASE_DIR}/plugin_config.yaml",
    } 

    result_copy = _copy_jinja_template(copier, 
        conf_values,
        SUPERVISOR_CONF_TEMPLATE, 
        _SUPERVISOR_CONF
    )

    return result_copy


def _copy_jinja_template(copier: Runner, cfg: dict, templ_orig: str, templ_dest: str) -> dict:
    """
    Copy Jinja2 template from origin to destiny, substitute variables from cfg.

    Parameters
    ----------
    copier : callable that copies a local file to the remote node via mccli.
    cfg    : provides configuration values for the supervisord.conf file.
    templ_orig : path to the original Jinja2 template file.
    templ_dest : path to the destination file on the remote node.

    Returns
    -------
    dict  ``{success, output, error}``
    """

    vals = {}
    for key, value in cfg.items():
        if isinstance(value, str):
            vals[key] = value.replace("$HOME", "%(ENV_HOME)s")
        else:
            vals[key] = value

    rendered_conf = _render_template(templ_orig, vals)

    #TODO: Delete the temporary file after copying, or use a context manager that does it automatically.
    rendered_tempfile = _write_to_tempfile(rendered_conf)

    logger.info(f"Copying Jinja2 template '{templ_orig}' to remote node via mccli")
    result_copy = copier(local_path=rendered_tempfile.name, 
                    remote_path=templ_dest.replace("$HOME", "~"), 
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
    if check_status(runner).get("success"):
        logger.info("supervisord is already running, reloading config and restarting services")
        cmd = (
            f"source {_BASE_DIR}/bin/activate &&"
            f"supervisorctl reread && "
            f"supervisorctl update && "
            f"supervisorctl restart all"
        )
    else:
        logger.info("supervisord is not running, starting supervisord daemon")
        cmd = f"source {_BASE_DIR}/bin/activate && supervisord -c {_SUPERVISOR_CONF}"

    result = runner(cmd, timeout=_SHORT_TIMEOUT)
    return result


def stop_supervisord(runner: Runner) -> dict:
    """
    Stop the supervisord daemon on the remote node.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    logger.info("Stopping supervisord on remote node")
    cmd = f"source {_BASE_DIR}/bin/activate && supervisorctl shutdown"
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
    logger.info("Checking supervisord status")
    cmd = f"source {_BASE_DIR}/bin/activate && supervisorctl status"
    return runner(cmd, timeout=_SHORT_TIMEOUT)


def remove_installation(runner: Runner) -> dict:
    """
    Remove the HPC Pilot installation from the remote node.

    This is the final step of undeploy and is separated here so it can be
    called independently after stopping services.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    cmd = f"rm -rf {_BASE_DIR} && echo 'Installation removed.'"
    return runner(cmd, timeout=_SHORT_TIMEOUT)
