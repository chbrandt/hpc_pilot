"""
api/hpc.py — REST endpoints for HPC node operations.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
POST /api/hpc/deploy
    Install wstunnel + supervisord on the remote HPC node.
    JSON body: hpc_host, ssh_port (opt), wstunnel_server, wstunnel_port (opt),
               wstunnel_secret, wstunnel_local_port (opt).

DELETE /api/hpc/deploy
    Stop all services and remove the HPC Pilot installation from the remote node.
    JSON body: hpc_host, ssh_port (opt).

POST /api/hpc/status
    Query supervisorctl status on the remote HPC node.
    JSON body: hpc_host, ssh_port (opt).

POST /api/hpc/start
    Start all supervisord-managed services.
    JSON body: hpc_host, ssh_port (opt).

POST /api/hpc/stop
    Stop all supervisord-managed services.
    JSON body: hpc_host, ssh_port (opt).
"""

import json
import logging

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from api.site_config import load_site_config
from lib import hpc_client

logger = logging.getLogger(__name__)

hpc_bp = Blueprint("api_hpc", __name__, url_prefix="/api/hpc")


# ── Helpers ───────────────────────────────────────────────────────────


def _ok(data: dict, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


def _parse_host(body: dict) -> tuple[str, int]:
    """Extract and validate hpc_host and ssh_port from a request body."""
    hpc_host = body.get("hpc_host", "").strip()
    if not hpc_host:
        raise ValueError("'hpc_host' is required.")
    ssh_port = int(body.get("ssh_port", 22))
    return hpc_host, ssh_port


# ── Routes ────────────────────────────────────────────────────────────


@hpc_bp.route("/deploy", methods=["POST"])
@require_token
def hpc_deploy():
    """
    Install the HPC Pilot stack (wstunnel + supervisord) on the remote node.

    JSON body keys:
        hpc_host*            str   HPC login node hostname (required)
        ssh_port             int   SSH port (default 22)
        wstunnel_server*     str   K8s-side wstunnel server hostname (required)
        wstunnel_port        int   wstunnel listen port (default from site_config)
        wstunnel_secret*     str   Shared tunnel secret (required)
        wstunnel_local_port  int   Local port on HPC node (default = wstunnel_port)
        plugin               str   InterLink plugin: "echo" | "docker" | "slurm" (default "echo")
    """
    claims = get_request_claims()
    token = claims["_token"]

    site_cfg = load_site_config()

    body = request.get_json(silent=True) or {}
    try:
        hpc_host, ssh_port = _parse_host(body)
    except ValueError as exc:
        return _err(str(exc))

    wstunnel_server = body.get("wstunnel_server", "").strip()
    wstunnel_secret = body.get("wstunnel_secret", "").strip()
    if not wstunnel_server:
        return _err("'wstunnel_server' is required.")
    if not wstunnel_secret:
        return _err("'wstunnel_secret' is required.")

    wstunnel_port = int(body.get("wstunnel_port",
                                 site_cfg["wstunnel"]["port"]))
    wstunnel_local_port = int(body.get("wstunnel_local_port",
                                       site_cfg["wstunnel"]["local_port"]))

    plugin = body.get("plugin", hpc_client._DEFAULT_PLUGIN).strip().lower()
    if plugin not in hpc_client._VALID_PLUGINS:
        return _err(
            f"'plugin' must be one of: {', '.join(hpc_client._VALID_PLUGINS)}."
        )

    result = hpc_client.deploy(
        token=token,
        hpc_host=hpc_host,
        ssh_port=ssh_port,
        wstunnel_server=wstunnel_server,
        wstunnel_port=wstunnel_port,
        wstunnel_secret=wstunnel_secret,
        wstunnel_local_port=wstunnel_local_port,
        plugin=plugin,
    )
    code = 200 if result.get("success") else 500
    return _ok(result, code)


@hpc_bp.route("/deploy", methods=["DELETE"])
@require_token
def hpc_undeploy():
    """
    Stop all services and uninstall the HPC Pilot stack from the remote node.

    This is the inverse of POST /api/hpc/deploy.  It stops all supervisord-managed
    services, shuts down supervisord, and removes the ``~/.pilot`` directory.

    JSON body keys:
        hpc_host*  str   HPC login node hostname (required)
        ssh_port   int   SSH port (default 22)
    """
    claims = get_request_claims()
    token = claims["_token"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_host, ssh_port = _parse_host(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.undeploy(token=token, hpc_host=hpc_host, ssh_port=ssh_port)
    code = 200 if result.get("success") else 500
    return _ok(result, code)


@hpc_bp.route("/status", methods=["POST"])
@require_token
def hpc_status():
    """Query supervisorctl status on the remote HPC node."""
    claims = get_request_claims()
    token = claims["_token"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_host, ssh_port = _parse_host(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.get_status(token=token, hpc_host=hpc_host, ssh_port=ssh_port)
    code = 200 if result.get("success") else 500
    return _ok(result, code)


@hpc_bp.route("/start", methods=["POST"])
@require_token
def hpc_start():
    """Start all supervisord-managed services on the remote HPC node."""
    claims = get_request_claims()
    token = claims["_token"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_host, ssh_port = _parse_host(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.start_services(
        token=token, hpc_host=hpc_host, ssh_port=ssh_port
    )
    code = 200 if result.get("success") else 500
    return _ok(result, code)


@hpc_bp.route("/stop", methods=["POST"])
@require_token
def hpc_stop():
    """Stop all supervisord-managed services on the remote HPC node."""
    claims = get_request_claims()
    token = claims["_token"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_host, ssh_port = _parse_host(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.stop_services(
        token=token, hpc_host=hpc_host, ssh_port=ssh_port
    )
    code = 200 if result.get("success") else 500
    return _ok(result, code)
