"""
app/hpc.py — Web GUI routes for HPC node deployments.

Routes (all under /hpc prefix)
------
GET  /hpc                   HPC deployment form
POST /hpc/deploy            Run setup.sh on remote HPC node via mccli
POST /hpc/status            Query supervisorctl status on remote node
POST /hpc/start             Start managed services (supervisorctl start all)
POST /hpc/stop              Stop managed services (supervisorctl stop all)
POST /hpc/<id>/save         Persist HPC config to saved_deployments
"""

import logging
import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from lib import hpc_client
from lib.saved_deployments import list_configs, load_app_config, save_config

logger = logging.getLogger(__name__)

hpc_bp = Blueprint("app_hpc", __name__, url_prefix="/hpc")


# ── Helpers ────────────────────────────────────────────────────────────


def _get_token() -> str:
    return session.get("token", "")


def _default_wstunnel_config(namespace: str) -> dict:
    """
    Build sensible wstunnel defaults from the user's namespace and the
    global app_config (cluster_domain).
    """
    app_cfg = load_app_config()
    cluster_domain = app_cfg.get("cluster_domain", "dev.local")
    namespace_hash = namespace.removeprefix("user-")
    return {
        "wstunnel_server": f"{namespace}.{cluster_domain}",
        "wstunnel_port": 8420,
        "wstunnel_secret": namespace_hash,
        "wstunnel_local_port": 8420,
    }


# ── Routes ─────────────────────────────────────────────────────────────


@hpc_bp.route("/", methods=["GET"])
@require_login
def hpc_page():
    """Render the HPC deployment form."""
    namespace = session.get("namespace", "")
    saved = list_configs(namespace, kind="hpc") if namespace else []
    defaults = _default_wstunnel_config(namespace)
    return render_template("hpc.html", saved_configs=saved, defaults=defaults)


@hpc_bp.route("/deploy", methods=["POST"])
@require_login
def hpc_deploy():
    """Run setup.sh on the remote HPC node via mccli."""
    token = _get_token()
    namespace = session["namespace"]

    hpc_host          = request.form.get("hpc_host", "").strip()
    ssh_port_str      = request.form.get("ssh_port", "22").strip()
    wstunnel_server   = request.form.get("wstunnel_server", "").strip()
    wstunnel_port_str = request.form.get("wstunnel_port", "8420").strip()
    wstunnel_secret   = request.form.get("wstunnel_secret", "").strip()
    wstunnel_local_port_str = request.form.get(
        "wstunnel_local_port", wstunnel_port_str
    ).strip()
    label = request.form.get("label", "").strip() or hpc_host

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))
    if not wstunnel_server:
        flash("wstunnel server hostname is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))
    if not wstunnel_secret:
        flash("wstunnel secret is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
        wstunnel_port = int(wstunnel_port_str)
        wstunnel_local_port = int(wstunnel_local_port_str)
    except ValueError:
        flash("SSH port and wstunnel ports must be integers.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    logger.info(
        "HPC deploy: user=%s host=%s wstunnel_server=%s port=%s",
        namespace, hpc_host, wstunnel_server, wstunnel_port,
    )

    result = hpc_client.deploy(
        token=token,
        hpc_host=hpc_host,
        ssh_port=ssh_port,
        wstunnel_server=wstunnel_server,
        wstunnel_port=wstunnel_port,
        wstunnel_secret=wstunnel_secret,
        wstunnel_local_port=wstunnel_local_port,
    )

    # Auto-save config on success
    if result["success"]:
        try:
            save_config(
                namespace=namespace,
                kind="hpc",
                config={
                    "label": label,
                    "hpc_host": hpc_host,
                    "ssh_port": ssh_port,
                    "wstunnel_server": wstunnel_server,
                    "wstunnel_port": wstunnel_port,
                    "wstunnel_secret": wstunnel_secret,
                    "wstunnel_local_port": wstunnel_local_port,
                },
            )
        except Exception as exc:
            logger.warning("Could not auto-save HPC config: %s", exc)

    return render_template(
        "hpc_result.html",
        result=result,
        action="deploy",
        hpc_host=hpc_host,
        wstunnel_server=wstunnel_server,
        wstunnel_port=wstunnel_port,
    )


@hpc_bp.route("/status", methods=["POST"])
@require_login
def hpc_status():
    """Query supervisorctl status on the remote HPC node."""
    token = _get_token()
    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    result = hpc_client.get_status(token=token, hpc_host=hpc_host, ssh_port=ssh_port)
    return render_template(
        "hpc_result.html", result=result, action="status", hpc_host=hpc_host
    )


@hpc_bp.route("/start", methods=["POST"])
@require_login
def hpc_start():
    """Start all supervisord-managed services on the remote HPC node."""
    token = _get_token()
    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    result = hpc_client.start_services(
        token=token, hpc_host=hpc_host, ssh_port=ssh_port
    )
    return render_template(
        "hpc_result.html", result=result, action="start", hpc_host=hpc_host
    )


@hpc_bp.route("/stop", methods=["POST"])
@require_login
def hpc_stop():
    """Stop all supervisord-managed services on the remote HPC node."""
    token = _get_token()
    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("app_hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    result = hpc_client.stop_services(
        token=token, hpc_host=hpc_host, ssh_port=ssh_port
    )
    return render_template(
        "hpc_result.html", result=result, action="stop", hpc_host=hpc_host
    )


@hpc_bp.route("/<config_id>/save", methods=["POST"])
@require_login
def hpc_save(config_id: str):
    """Persist (or re-persist) an HPC config to the saved_deployments store."""
    namespace = session["namespace"]

    hpc_host          = request.form.get("hpc_host", "").strip()
    ssh_port_str      = request.form.get("ssh_port", "22").strip()
    wstunnel_server   = request.form.get("wstunnel_server", "").strip()
    wstunnel_port_str = request.form.get("wstunnel_port", "8420").strip()
    wstunnel_secret   = request.form.get("wstunnel_secret", "").strip()
    wstunnel_local_port_str = request.form.get(
        "wstunnel_local_port", wstunnel_port_str
    ).strip()
    label = request.form.get("label", "").strip() or hpc_host

    try:
        save_config(
            namespace=namespace,
            kind="hpc",
            config={
                "label": label,
                "hpc_host": hpc_host,
                "ssh_port": int(ssh_port_str),
                "wstunnel_server": wstunnel_server,
                "wstunnel_port": int(wstunnel_port_str),
                "wstunnel_secret": wstunnel_secret,
                "wstunnel_local_port": int(wstunnel_local_port_str),
            },
        )
        flash(f"HPC config for '{label}' saved.", "success")
    except Exception as exc:
        flash(f"Failed to save HPC config: {exc}", "error")

    return redirect(url_for("app_hpc.hpc_page"))
