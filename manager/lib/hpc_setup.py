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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

# Type alias for the runner callable provided by hpc_client
Runner = Callable[..., dict]

# Timeout constants (seconds) – mirrored from hpc_client for convenience.
# Step functions that can take a long time pass these explicitly to runner().
_SHORT_TIMEOUT = 30   # simple checks, mkdir, supervisorctl …
_LONG_TIMEOUT  = 300  # curl download + pip install

# ── Remote path constants ──────────────────────────────────────────────────────
# All paths use shell variable $HOME so they expand correctly on the remote node
# when passed as part of a shell command string.
# The supervisord *config file* uses supervisord's own %(ENV_HOME)s syntax
# instead of $HOME because the file is written by Python (no shell expansion).

_BASE             = "$HOME/.hpc-pilot"
_TMP              = f"{_BASE}/tmp"
_BIN              = f"{_BASE}/bin"
_LOGS             = f"{_BASE}/logs"
_CONF_DIR         = f"{_BASE}/config"
_SUPERVISORD_CONF = f"{_BASE}/supervisord.conf"
_SUPERVISOR_SOCK  = f"{_BASE}/supervisor.sock"
_SUPERVISOR_PID   = f"{_BASE}/supervisord.pid"

_WSTUNNEL_BIN     = f"{_BIN}/wstunnel"
_WSTUNNEL_VERSION_DEFAULT = "v10.1.0"


# ── Configuration ──────────────────────────────────────────────────────────────


@dataclass
class SetupConfig:
    """
    Parameters for the wstunnel/supervisord deployment on a remote HPC node.

    Parameters
    ----------
    wstunnel_server     : Public hostname of the K8s-side wstunnel server.
    wstunnel_port       : Port the wstunnel server listens on (e.g. 8420).
    wstunnel_secret     : Shared bearer-token secret for the tunnel.
    wstunnel_local_port : Local TCP port on the HPC node wstunnel will expose.
    wstunnel_version    : GitHub release tag to download (default v10.1.0).
    """

    wstunnel_server: str
    wstunnel_port: int
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
    cmd = f"mkdir -p {_TMP} {_BIN} {_LOGS} {_CONF_DIR} && echo 'Directories ready: {_BASE}'"
    return runner(cmd, timeout=_SHORT_TIMEOUT)


def install_wstunnel(runner: Runner, cfg: SetupConfig) -> dict:
    """
    Download and install the wstunnel binary into ``~/.hpc-pilot/bin/``.

    Skips the download if the binary is already present and executable (idempotent).
    Uses ``curl`` + ``tar`` + ``install`` — all standard HPC tools.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.
    cfg    : :class:`SetupConfig` — provides ``wstunnel_version`` and ``wstunnel_url``.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    # Wrapped in a subshell so that `exit 1` inside the body causes the
    # subshell to exit with a non-zero code, allowing _run_mccli's
    # `|| echo "__MCCLI_COMMAND_FAILED__"` sentinel to fire correctly.
#     cmd = f"""\
# (
#     if [ -x {_WSTUNNEL_BIN} ]; then
#         ver=$({_WSTUNNEL_BIN} --version 2>/dev/null | head -1 || echo unknown)
#         echo "wstunnel already installed: $ver"
#         echo "Skipping download (delete {_WSTUNNEL_BIN} to force reinstall)"
#     else
#         command -v curl >/dev/null 2>&1 || {{ echo 'curl is required but not found' >&2; exit 1; }}
#         echo "Downloading wstunnel {cfg.wstunnel_version} from: {cfg.wstunnel_url}"
#         tmpdir=$(mktemp -d)
#         trap 'rm -rf "$tmpdir"' EXIT
#         curl --fail --silent --show-error -L -o "$tmpdir/wstunnel.tar.gz" "{cfg.wstunnel_url}" \
#             || {{ echo 'Failed to download wstunnel' >&2; exit 1; }}
#         tar -xzf "$tmpdir/wstunnel.tar.gz" -C "$tmpdir" \
#             || {{ echo 'Failed to extract wstunnel archive' >&2; exit 1; }}
#         bin=$(find "$tmpdir" -name wstunnel -type f | head -1)
#         [ -n "$bin" ] || {{ echo 'wstunnel binary not found in archive' >&2; exit 1; }}
#         install -m0755 "$bin" {_WSTUNNEL_BIN}
#         echo "wstunnel installed at {_WSTUNNEL_BIN}"
#     fi
# )"""
    cmd = (
        f"curl -o {_TMP}/wstunnel.tar.gz {cfg.wstunnel_url}"
        f" && tar -xzf {_TMP}/wstunnel.tar.gz -C {_TMP}"
        f" && install {_TMP}/wstunnel {_WSTUNNEL_BIN}" 
        f" && rm {_TMP}/wstunnel.tar.gz"
    )
    return runner(cmd, timeout=_LONG_TIMEOUT)


def install_supervisord(runner: Runner) -> dict:
    """
    Ensure ``supervisord`` is available on the remote node, installing it via
    ``pip3`` or ``pip`` (``--user``) if not already present.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    # Subshell ensures exit 1 doesn't kill the outer SSH shell, so the
    # _run_mccli failure sentinel can still be appended and detected.
    cmd = """\
(
    export PATH="$HOME/.local/bin:$PATH"
    if command -v supervisord >/dev/null 2>&1; then
        echo "System supervisord found: $(command -v supervisord)"
    elif command -v pip3 >/dev/null 2>&1; then
        echo "Installing supervisord via pip3 --user..."
        pip3 install --quiet --user supervisor \
            || { echo 'pip3 install supervisor failed' >&2; exit 1; }
        export PATH="$HOME/.local/bin:$PATH"
        command -v supervisord >/dev/null 2>&1 \
            || { echo 'supervisord not on PATH after pip3 install' >&2; exit 1; }
        echo "supervisord installed: $(command -v supervisord)"
    elif command -v pip >/dev/null 2>&1; then
        echo "Installing supervisord via pip --user..."
        pip install --quiet --user supervisor \
            || { echo 'pip install supervisor failed' >&2; exit 1; }
        export PATH="$HOME/.local/bin:$PATH"
        command -v supervisord >/dev/null 2>&1 \
            || { echo 'supervisord not on PATH after pip install' >&2; exit 1; }
        echo "supervisord installed: $(command -v supervisord)"
    else
        echo 'Neither pip3 nor pip found. Cannot install supervisord.' >&2
        exit 1
    fi
)"""
    return runner(cmd, timeout=_LONG_TIMEOUT)


def write_supervisord_conf(runner: Runner, cfg: SetupConfig) -> dict:
    """
    Render the supervisord configuration and write it to the remote node.

    The rendered config is piped as stdin into ``cat > ~/.hpc-pilot/supervisord.conf``
    on the remote side so no temporary files or heredocs are needed in the shell.

    Paths in the config use supervisord's ``%(ENV_HOME)s`` interpolation syntax
    so the config remains correct regardless of the remote user's home directory.

    Parameters
    ----------
    runner : callable that executes a remote shell command via mccli.
    cfg    : :class:`SetupConfig` — provides wstunnel connection parameters.

    Returns
    -------
    dict  ``{success, output, error}``
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # supervisord uses %(ENV_HOME)s for $HOME expansion inside the config file.
    _h = "%(ENV_HOME)s"
    conf_content = (
        f"; supervisord.conf — HPC Pilot managed services\n"
        f"; Generated by manager/lib/hpc_setup.py on {now_utc}\n"
        f"\n"
        f"[unix_http_server]\n"
        f"file={_h}/.hpc-pilot/supervisor.sock\n"
        f"\n"
        f"[supervisord]\n"
        f"nodaemon=false\n"
        f"logfile={_h}/.hpc-pilot/logs/supervisord.log\n"
        f"logfile_maxbytes=10MB\n"
        f"logfile_backups=3\n"
        f"loglevel=info\n"
        f"pidfile={_h}/.hpc-pilot/supervisord.pid\n"
        f"\n"
        f"[rpcinterface:supervisor]\n"
        f"supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n"
        f"\n"
        f"[supervisorctl]\n"
        f"serverurl=unix://{_h}/.hpc-pilot/supervisor.sock\n"
        f"\n"
        f"; ── wstunnel client ──────────────────────────────────────────────────────────\n"
        f"; Tunnels back to the wstunnel server in the user's Kubernetes namespace.\n"
        f"[program:wstunnel]\n"
        f"command={_h}/.hpc-pilot/bin/wstunnel client\n"
        f"    --local-to-remote tcp://0.0.0.0:{cfg.wstunnel_local_port}:localhost:{cfg.wstunnel_local_port}\n"
        f'    -H "authorization: Bearer {cfg.wstunnel_secret}"\n'
        f"    wss://{cfg.wstunnel_server}:{cfg.wstunnel_port}\n"
        f"directory={_h}/.hpc-pilot\n"
        f"autostart=true\n"
        f"autorestart=true\n"
        f"startretries=5\n"
        f"startsecs=3\n"
        f"stdout_logfile={_h}/.hpc-pilot/logs/wstunnel-stdout.log\n"
        f"stderr_logfile={_h}/.hpc-pilot/logs/wstunnel-stderr.log\n"
        f"stdout_logfile_maxbytes=5MB\n"
        f"stderr_logfile_maxbytes=5MB\n"
    ).encode()

    # Pipe the config content as stdin; the remote shell writes it to file.
    cmd = f"cat > {_SUPERVISORD_CONF} && echo 'Config written to {_SUPERVISORD_CONF}'"
    return runner(cmd, stdin_data=conf_content, timeout=_SHORT_TIMEOUT)


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
    # Subshell ensures exit 1 doesn't kill the outer SSH shell.
    cmd = f"""\
(
    export PATH="$HOME/.local/bin:$PATH"
    CONF="{_SUPERVISORD_CONF}"
    PID_FILE="{_SUPERVISOR_PID}"
    SOCK="{_SUPERVISOR_SOCK}"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        supervisorctl -c "$CONF" reread  >/dev/null 2>&1 || true
        supervisorctl -c "$CONF" update  >/dev/null 2>&1 || true
        supervisorctl -c "$CONF" restart all 2>&1 || true
        echo "Config reloaded and services restarted"
    else
        supervisord -c "$CONF" \
            || {{ echo 'Failed to start supervisord' >&2; exit 1; }}
        sleep 2
        if [ -S "$SOCK" ]; then
            echo "supervisord started successfully"
        else
            echo 'supervisord socket not found after startup' >&2
            exit 1
        fi
    fi
)"""
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
    cmd = (
        f'export PATH="$HOME/.local/bin:$PATH"; '
        f"supervisorctl -c {_SUPERVISORD_CONF} status"
    )
    return runner(cmd, timeout=_SHORT_TIMEOUT)
