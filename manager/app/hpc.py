"""
app/hpc.py — Web GUI routes for HPC node deployments.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/ except for listing the
available HPC nodes (from config files) and plugin metadata.

Routes (all under /hpc prefix)
------
GET  /hpc                   HPC deployment form
POST /hpc/deploy            Run setup.sh on remote HPC node via mccli
POST /hpc/status            Query supervisorctl status on remote node
POST /hpc/start             Start managed services (supervisorctl start all)
POST /hpc/stop              Stop managed services (supervisorctl stop all)
"""

import logging

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from app.api_client import api_post
from api.site_config import load_site_config
from lib.hpc_config import list_hpc_nodes

logger = logging.getLogger(__name__)

hpc_bp = Blueprint("app_hpc", __name__, url_prefix="/hpc")


# ── Helpers ────────────────────────────────────────────────────────────


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


def _wstunnel_config(namespace: str) -> dict:
    """
    Build sensible wstunnel defaults from the user's namespace and the
    site config (cluster_domain).
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


# ── Routes ─────────────────────────────────────────────────────────────


@hpc_bp.route("/", methods=["GET"])
@require_login
def hpc_page():
    """Render the HPC deployment form."""
    namespace = session.get("namespace", "")
    defaults = _wstunnel_config(namespace)
    hpc_nodes = list_hpc_nodes()
    return render_template(
        "hpc.html",
        hpc_nodes=hpc_nodes,
        defaults=defaults,
    )


@hpc_bp.route("/deploy", methods=["POST"])
@require_login
def hpc_deploy():
    """Run setup.sh on the remote HPC node via mccli."""
    namespace = session["namespace"]
    defaults = _wstunnel_config(namespace)

    hpc_name = request.form.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    logger.info(
        "HPC deploy: user=%s hpc_name=%s",
        namespace, hpc_name,
    )

    try:
        result = api_post(
            "/api/hpc/deploy",
            {"hpc_name": hpc_name},
        )
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc)}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return render_template(
        "hpc_result.html",
        result=result,
        action="deploy",
        hpc_name=hpc_name,
        wstunnel_server=defaults["wstunnel_server"],
        wstunnel_port=defaults["wstunnel_port"],
    )


@hpc_bp.route("/status", methods=["POST"])
@require_login
def hpc_status():
    """Query supervisorctl status on the remote HPC node."""
    hpc_name = request.form.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        result = api_post("/api/hpc/status", {"hpc_name": hpc_name})
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc)}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return render_template(
        "hpc_result.html", result=result, action="status", hpc_name=hpc_name
    )


@hpc_bp.route("/start", methods=["POST"])
@require_login
def hpc_start():
    """Start all supervisord-managed services on the remote HPC node."""
    hpc_name = request.form.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        result = api_post("/api/hpc/start", {"hpc_name": hpc_name})
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc)}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return render_template(
        "hpc_result.html", result=result, action="start", hpc_name=hpc_name
    )


@hpc_bp.route("/stop", methods=["POST"])
@require_login
def hpc_stop():
    """Stop all supervisord-managed services on the remote HPC node."""
    hpc_name = request.form.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        result = api_post("/api/hpc/stop", {"hpc_name": hpc_name})
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc)}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return render_template(
        "hpc_result.html", result=result, action="stop", hpc_name=hpc_name
    )
