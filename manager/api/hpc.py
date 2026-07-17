"""
api/hpc.py — REST endpoints for HPC node operations.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
GET  /api/hpc/nodes
    List all available HPC nodes defined in ``manager/hpc/*.yaml``.

POST /api/hpc/deploy
    Install wstunnel + supervisord on the remote HPC node.
    JSON body: hpc_name.

DELETE /api/hpc/deploy
    Stop all services and remove the HPC Pilot installation from the remote node.
    JSON body: hpc_name.

POST /api/hpc/status
    Query supervisorctl status on the remote HPC node.
    JSON body: hpc_name.

POST /api/hpc/start
    Start all supervisord-managed services.
    JSON body: hpc_name.

POST /api/hpc/stop
    Stop all supervisord-managed services.
    JSON body: hpc_name.
"""

import json
import logging

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from api.site_config import load_site_config
from lib import hpc_client
from lib.hpc_config import list_hpc_nodes, load_hpc_config

logger = logging.getLogger(__name__)

hpc_bp = Blueprint("api_hpc", __name__, url_prefix="/api/hpc")


# ── Helpers ───────────────────────────────────────────────────────────


def _ok(data: dict, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


def _resolve_hpc(body: dict) -> dict:
    """
    Resolve ``hpc_name`` from the request body to a full HPC config dict.

    Returns
    -------
    dict
        A dict with keys ``name``, ``hostname``, ``ssh_port``, and ``plugin``.

    Raises
    ------
    ValueError
        If ``hpc_name`` is missing or the config file cannot be loaded.
    """
    hpc_name = body.get("hpc_name", "").strip()
    if not hpc_name:
        raise ValueError("'hpc_name' is required.")
    return load_hpc_config(hpc_name)


def _wstunnel_config(namespace: str) -> dict:
    """
    Compute wstunnel parameters from the user's namespace and site config.

    The wstunnel server hostname is derived from the namespace and
    ``cluster_domain``; the secret is the namespace hash; ports come from
    ``site_config.yaml``.

    Returns
    -------
    dict
        Keys: ``wstunnel_server``, ``wstunnel_port``, ``wstunnel_secret``,
        ``wstunnel_local_port``.
    """
    site_cfg = load_site_config()
    cluster_domain = site_cfg.get("cluster_domain", "dev.local")
    wstunnel_port = site_cfg["wstunnel"]["port"]
    wstunnel_server = f"{namespace}.{cluster_domain}"
    wstunnel_local_port = site_cfg["wstunnel"]["local_port"]
    namespace_hash = namespace.removeprefix("user-")
    return {
        "wstunnel_server": wstunnel_server,
        "wstunnel_port": wstunnel_port,
        "wstunnel_secret": namespace_hash,
        "wstunnel_local_port": wstunnel_local_port,
    }


# ── Routes ────────────────────────────────────────────────────────────


@hpc_bp.route("/nodes", methods=["GET"])
@require_token
def hpc_nodes():
    """List all available HPC nodes defined in ``manager/hpc/*.yaml``."""
    nodes = list_hpc_nodes()
    return _ok({"nodes": nodes})


@hpc_bp.route("/deploy", methods=["POST"])
@require_token
def hpc_deploy():
    """
    Install the HPC Pilot stack (wstunnel + supervisord) on the remote node.

    JSON body keys:
        hpc_name*  str   HPC node name (matches a config file in
                         ``manager/hpc/<name>.yaml``) (required)

    The ``ssh_port`` and ``plugin`` values are read from the HPC config file.
    The wstunnel parameters (server, port, secret, local port) are computed
    internally from the authenticated user's namespace and the site config.
    None of these are accepted from the request body.
    """
    claims = get_request_claims()
    token = claims["_token"]
    namespace = claims["namespace"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_cfg = _resolve_hpc(body)
    except ValueError as exc:
        return _err(str(exc))

    wst_cfg = _wstunnel_config(namespace)

    result = hpc_client.deploy(
        token=token,
        hpc_host=hpc_cfg["hostname"],
        ssh_port=hpc_cfg["ssh_port"],
        wstunnel_server=wst_cfg["wstunnel_server"],
        wstunnel_port=wst_cfg["wstunnel_port"],
        wstunnel_secret=wst_cfg["wstunnel_secret"],
        wstunnel_local_port=wst_cfg["wstunnel_local_port"],
        plugin=hpc_cfg["plugin"],
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
        hpc_name*  str   HPC node name (required)
    """
    claims = get_request_claims()
    token = claims["_token"]

    body = request.get_json(silent=True) or {}
    try:
        hpc_cfg = _resolve_hpc(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.undeploy(
        token=token,
        hpc_host=hpc_cfg["hostname"],
        ssh_port=hpc_cfg["ssh_port"],
    )
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
        hpc_cfg = _resolve_hpc(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.get_status(
        token=token,
        hpc_host=hpc_cfg["hostname"],
        ssh_port=hpc_cfg["ssh_port"],
    )
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
        hpc_cfg = _resolve_hpc(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.start_services(
        token=token,
        hpc_host=hpc_cfg["hostname"],
        ssh_port=hpc_cfg["ssh_port"],
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
        hpc_cfg = _resolve_hpc(body)
    except ValueError as exc:
        return _err(str(exc))

    result = hpc_client.stop_services(
        token=token,
        hpc_host=hpc_cfg["hostname"],
        ssh_port=hpc_cfg["ssh_port"],
    )
    code = 200 if result.get("success") else 500
    return _ok(result, code)
