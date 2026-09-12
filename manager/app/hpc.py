"""
app/hpc.py — Web GUI routes for node management (HPC + InterLink).

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/ except for listing the
available HPC nodes (from config files) and plugin metadata.

Routes (all under /hpc prefix, plus the merged /nodes page)
-------
GET  /nodes                 "Manage Nodes" page (HPC nodes + InterLink releases)
POST /nodes/start           Start a node (deploy HPC + InterLink in one action)
POST /nodes/stop            Stop a node (stop HPC + uninstall InterLink)
POST /nodes/interlink/deploy      Deploy InterLink for an HPC node
POST /nodes/interlink/delete      Uninstall the InterLink release for an HPC node
GET  /hpc                   Redirect to /nodes
POST /hpc/deploy            Run setup.sh on remote HPC node via mccli
GET  /hpc/status            Query supervisorctl status on remote node
POST /hpc/start             Start managed services (supervisorctl start all)
POST /hpc/stop              Stop managed services (supervisorctl stop all)
"""

import logging

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from app.api_client import LONG_TIMEOUT, api_delete, api_get, api_post
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
    Build wstunnel defaults from the user's namespace and the site config.

    The server hostname is ``site_config.hostname`` (shared, path-prefix
    routing); the secret/path-prefix is the user's full namespace; the ports
    come from ``site_config.wstunnel``.
    """
    site_cfg = load_site_config()
    hostname = site_cfg.get("hostname", "dev.local")
    wstunnel_port = site_cfg["wstunnel"]["port"]
    wstunnel_server = hostname
    wstunnel_local_port = site_cfg["wstunnel"]["local_port"]
    return {
        "wstunnel_server": wstunnel_server,
        "wstunnel_port": wstunnel_port,
        "wstunnel_secret": namespace,
        "wstunnel_local_port": wstunnel_local_port,
    }


# ── Routes ─────────────────────────────────────────────────────────────


@hpc_bp.route("/nodes", methods=["GET"])
@require_login
def manage_nodes():
    """
    "Manage Nodes" page: the single place to manage HPC nodes and their
    InterLink virtual-kubelet deployments.

    For every configured HPC node the page shows the connection details, the
    HPC-side deployment actions (deploy/status/start/stop) and the state of
    the corresponding InterLink Helm release (interlink-<hpc_name>).
    """
    namespace = session.get("namespace", "")
    defaults = _wstunnel_config(namespace)
    hpc_nodes = list_hpc_nodes()

    nodes = []
    errors = []
    for node in hpc_nodes:
        hpc_name = node["name"]
        entry = {
            **node,
            "interlink_deployed": False,
            "interlink_error": None,
        }
        try:
            result = api_get("/api/interlink", params={"hpc_name": hpc_name})
            entry["interlink_deployed"] = bool(result.get("success"))
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                msg = _api_error(exc)
                errors.append(f"InterLink ({hpc_name}): {msg}")
                entry["interlink_error"] = msg
        except Exception as exc:
            errors.append(f"InterLink ({hpc_name}): {exc}")
            entry["interlink_error"] = str(exc)
        nodes.append(entry)

    return render_template(
        "nodes.html",
        nodes=nodes,
        defaults=defaults,
        error="; ".join(errors) if errors else None,
    )


@hpc_bp.route("/nodes/interlink/deploy", methods=["POST"])
@require_login
def interlink_deploy():
    """Deploy the InterLink virtual-kubelet bound to the selected HPC node."""
    hpc_name = request.form.get("hpc_name", "").strip()
    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    logger.info("InterLink deploy: user=%s hpc_name=%s", session["namespace"], hpc_name)

    try:
        result = api_post(
            "/api/interlink", {"hpc_name": hpc_name}, timeout=LONG_TIMEOUT
        )
        if result.get("success"):
            flash(
                f"InterLink deployed for '{hpc_name}' "
                f"(virtual node vk-node for this session).",
                "success",
            )
        else:
            flash(f"InterLink deployment failed: {result.get('error')}", "error")
    except requests.HTTPError as exc:
        flash(f"InterLink deployment failed: {_api_error(exc)}", "error")
    except Exception as exc:
        logger.error("InterLink deploy failed: %s", exc)
        flash(f"InterLink deployment failed: {exc}", "error")

    return redirect(url_for("app_hpc.manage_nodes"))


@hpc_bp.route("/nodes/interlink/delete", methods=["POST"])
@require_login
def interlink_delete():
    """Uninstall the InterLink release bound to the selected HPC node."""
    hpc_name = request.form.get("hpc_name", "").strip()
    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    try:
        result = api_delete("/api/interlink", body={"hpc_name": hpc_name})
        if result.get("success"):
            flash(f"InterLink release for '{hpc_name}' uninstalled.", "success")
        else:
            flash(f"Uninstall failed: {result.get('error')}", "error")
    except requests.HTTPError as exc:
        flash(f"Uninstall failed: {_api_error(exc)}", "error")
    except Exception as exc:
        flash(f"Uninstall failed: {exc}", "error")

    return redirect(url_for("app_hpc.manage_nodes"))


@hpc_bp.route("/nodes/start", methods=["POST"])
@require_login
def node_start():
    """Start a node: deploy HPC stack, then install InterLink (GA8 + GA10)."""
    hpc_name = request.form.get("hpc_name", "").strip()
    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    namespace = session["namespace"]
    logger.info("Node start: user=%s hpc_name=%s", namespace, hpc_name)

    logs = []
    overall_success = True

    logs.append(f"=== Deploying HPC stack on '{hpc_name}' ===")
    try:
        hpc_result = api_post(
            "/api/hpc/deploy", {"hpc_name": hpc_name}, timeout=LONG_TIMEOUT
        )
        if hpc_result.get("success"):
            logs.append("[OK] HPC stack deployed successfully.")
            if hpc_result.get("output"):
                logs.append(hpc_result["output"])
        else:
            overall_success = False
            logs.append(f"[FAIL] HPC deploy: {hpc_result.get('error', 'unknown')}")
            if hpc_result.get("output"):
                logs.append(hpc_result["output"])
    except requests.HTTPError as exc:
        overall_success = False
        logs.append(f"[FAIL] HPC deploy: {_api_error(exc)}")
    except Exception as exc:
        overall_success = False
        logs.append(f"[FAIL] HPC deploy: {exc}")

    if overall_success:
        logs.append("")
        logs.append(f"=== Deploying InterLink for '{hpc_name}' ===")
        try:
            il_result = api_post(
                "/api/interlink", {"hpc_name": hpc_name}, timeout=LONG_TIMEOUT
            )
            if il_result.get("success"):
                logs.append("[OK] InterLink deployed successfully.")
                if il_result.get("output"):
                    logs.append(il_result["output"])
            else:
                overall_success = False
                logs.append(f"[FAIL] InterLink: {il_result.get('error', 'unknown')}")
                if il_result.get("output"):
                    logs.append(il_result["output"])
        except requests.HTTPError as exc:
            overall_success = False
            logs.append(f"[FAIL] InterLink: {_api_error(exc)}")
        except Exception as exc:
            overall_success = False
            logs.append(f"[FAIL] InterLink: {exc}")

    if overall_success:
        flash(f"Node '{hpc_name}' started successfully.", "success")
    else:
        flash(f"Node '{hpc_name}' start failed — see logs.", "error")

    return render_template(
        "node_result.html", action="start", hpc_name=hpc_name,
        success=overall_success, logs="\n".join(logs),
    )


@hpc_bp.route("/nodes/stop", methods=["POST"])
@require_login
def node_stop():
    """Stop a node: stop HPC services, then uninstall InterLink (GA8 + GA10)."""
    hpc_name = request.form.get("hpc_name", "").strip()
    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    namespace = session["namespace"]
    logger.info("Node stop: user=%s hpc_name=%s", namespace, hpc_name)

    logs = []
    overall_success = True

    logs.append(f"=== Stopping HPC services on '{hpc_name}' ===")
    try:
        hpc_result = api_post("/api/hpc/stop", {"hpc_name": hpc_name})
        if hpc_result.get("success"):
            logs.append("[OK] HPC services stopped.")
            if hpc_result.get("output"):
                logs.append(hpc_result["output"])
        else:
            overall_success = False
            logs.append(f"[FAIL] HPC stop: {hpc_result.get('error', 'unknown')}")
    except requests.HTTPError as exc:
        overall_success = False
        logs.append(f"[FAIL] HPC stop: {_api_error(exc)}")
    except Exception as exc:
        overall_success = False
        logs.append(f"[FAIL] HPC stop: {exc}")

    logs.append("")
    logs.append(f"=== Uninstalling InterLink for '{hpc_name}' ===")
    try:
        il_result = api_delete("/api/interlink", body={"hpc_name": hpc_name})
        if il_result.get("success"):
            logs.append("[OK] InterLink uninstalled.")
            if il_result.get("output"):
                logs.append(il_result["output"])
        else:
            logs.append(f"[WARN] InterLink: {il_result.get('error', 'not deployed')}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logs.append("[WARN] InterLink was not deployed — skipping.")
        else:
            overall_success = False
            logs.append(f"[FAIL] InterLink uninstall: {_api_error(exc)}")
    except Exception as exc:
        overall_success = False
        logs.append(f"[FAIL] InterLink uninstall: {exc}")

    if overall_success:
        flash(f"Node '{hpc_name}' stopped successfully.", "success")
    else:
        flash(f"Node '{hpc_name}' stop failed — see logs.", "error")

    return render_template(
        "node_result.html", action="stop", hpc_name=hpc_name,
        success=overall_success, logs="\n".join(logs),
    )


@hpc_bp.route("/nodes/prune", methods=["POST"])
@require_login
def userspace_prune():
    """
    Prune the user's userspace: remove every InterLink release, undeploy
    every HPC edge stack and delete the user's Kubernetes namespace
    (forwards to POST /api/userspace/prune).
    """
    logger.info("Prune userspace: user=%s", session["namespace"])
    try:
        result = api_post("/api/userspace/prune", timeout=LONG_TIMEOUT)
        if result.get("error"):
            flash(f"Prune failed: {result['error']}", "error")
        else:
            flash(
                "Userspace pruned: InterLink releases, HPC deployments and "
                "your namespace were removed.",
                "success",
            )
    except requests.HTTPError as exc:
        flash(f"Prune failed: {_api_error(exc)}", "error")
    except Exception as exc:
        logger.error("Prune failed: %s", exc)
        flash(f"Prune failed: {exc}", "error")

    return redirect(url_for("app_hpc.manage_nodes"))


@hpc_bp.route("/", methods=["GET"])
@require_login
def hpc_page():
    """Deprecated standalone HPC form — redirects to the merged page."""
    return redirect(url_for("app_hpc.manage_nodes"))


@hpc_bp.route("/deploy", methods=["POST"])
@require_login
def hpc_deploy():
    """Run setup.sh on the remote HPC node via mccli."""
    namespace = session["namespace"]
    defaults = _wstunnel_config(namespace)

    hpc_name = request.form.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    logger.info(
        "HPC deploy: user=%s hpc_name=%s",
        namespace, hpc_name,
    )

    try:
        result = api_post(
            "/api/hpc/deploy",
            {"hpc_name": hpc_name},
            timeout=LONG_TIMEOUT,
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


@hpc_bp.route("/status", methods=["GET"])
@require_login
def hpc_status():
    """Query supervisorctl status on the remote HPC node."""
    hpc_name = request.args.get("hpc_name", "").strip()

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_hpc.manage_nodes"))

    try:
        result = api_get("/api/hpc/status", params={"hpc_name": hpc_name})
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
        return redirect(url_for("app_hpc.manage_nodes"))

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
        return redirect(url_for("app_hpc.manage_nodes"))

    try:
        result = api_post("/api/hpc/stop", {"hpc_name": hpc_name})
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc)}
    except Exception as exc:
        result = {"success": False, "error": str(exc)}

    return render_template(
        "hpc_result.html", result=result, action="stop", hpc_name=hpc_name
    )
