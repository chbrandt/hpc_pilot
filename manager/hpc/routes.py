"""
manager/hpc/routes.py — Flask Blueprint for HPC deployments.

Registers the following URL rules (all prefixed with /hpc):

  GET  /hpc              – deployment form (with saved-config loader)
  POST /hpc/deploy       – run setup.sh on the remote HPC node via mccli
  POST /hpc/status       – query supervisorctl status on the remote node
  POST /hpc/start        – start managed services (supervisorctl start all)
  POST /hpc/stop         – stop managed services (supervisorctl stop all)
  POST /hpc/<id>/save    – persist the HPC config to saved_deployments
"""

import logging
import sys
import os

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# Make sure the manager/ directory is importable when this Blueprint is used
_manager_dir = os.path.dirname(os.path.dirname(__file__))
if _manager_dir not in sys.path:
    sys.path.insert(0, _manager_dir)

logger = logging.getLogger(__name__)

hpc_bp = Blueprint(
    "hpc",
    __name__,
    url_prefix="/hpc",
    # Re-use the manager app's templates/ folder — no Blueprint-local templates
    template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
)


# ── Helpers ────────────────────────────────────────────────────────────


def _require_login():
    """Return the current user or None if not authenticated."""
    from token_auth import get_session_user
    return get_session_user()


def _get_token() -> str:
    """Return the raw EGI Check-in token from the session."""
    return session.get("token", "")


def _default_wstunnel_config(namespace: str) -> dict:
    """
    Build sensible wstunnel defaults from the user's namespace and the
    global app_config (cluster_domain).  Mirrors the Helm chart defaults.
    """
    from saved_deployments import load_app_config
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
def hpc_page():
    """
    Render the HPC deployment form.

    Loads saved HPC configs for the current user so they can be
    re-deployed with one click, following the same pattern as
    index.html (container) and helm.html (Helm chart).
    """
    user = _require_login()
    if user is None:
        flash("Please log in with your EGI Check-in access token.", "error")
        return redirect(url_for("login"))

    from saved_deployments import list_configs
    namespace = session.get("namespace", "")
    saved = list_configs(namespace, kind="hpc") if namespace else []

    defaults = _default_wstunnel_config(namespace)

    return render_template(
        "hpc.html",
        saved_configs=saved,
        defaults=defaults,
    )


@hpc_bp.route("/deploy", methods=["POST"])
def hpc_deploy():
    """
    Run setup.sh on the remote HPC node via mccli.

    Form fields
    -----------
    hpc_host          (required) – HPC login node hostname or IP
    ssh_port          (default 22)
    wstunnel_server   (required) – K8s-side wstunnel server hostname
    wstunnel_port     (default 8420)
    wstunnel_secret   (required) – shared tunnel secret
    wstunnel_local_port (default = wstunnel_port)
    label             (optional) – human-friendly label for saved configs
    """
    user = _require_login()
    if user is None:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    token = _get_token()
    namespace = session["namespace"]

    # ── Parse form ──────────────────────────────────────────────────
    hpc_host          = request.form.get("hpc_host", "").strip()
    ssh_port_str      = request.form.get("ssh_port", "22").strip()
    wstunnel_server   = request.form.get("wstunnel_server", "").strip()
    wstunnel_port_str = request.form.get("wstunnel_port", "8420").strip()
    wstunnel_secret   = request.form.get("wstunnel_secret", "").strip()
    wstunnel_local_port_str = request.form.get(
        "wstunnel_local_port", wstunnel_port_str
    ).strip()
    label = request.form.get("label", "").strip() or hpc_host

    # ── Validate ────────────────────────────────────────────────────
    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("hpc.hpc_page"))
    if not wstunnel_server:
        flash("wstunnel server hostname is required.", "error")
        return redirect(url_for("hpc.hpc_page"))
    if not wstunnel_secret:
        flash("wstunnel secret is required.", "error")
        return redirect(url_for("hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
        wstunnel_port = int(wstunnel_port_str)
        wstunnel_local_port = int(wstunnel_local_port_str)
    except ValueError:
        flash("SSH port and wstunnel ports must be integers.", "error")
        return redirect(url_for("hpc.hpc_page"))

    # ── Deploy ──────────────────────────────────────────────────────
    from .hpc_client import deploy

    logger.info(
        "HPC deploy: user=%s host=%s wstunnel_server=%s port=%s",
        namespace, hpc_host, wstunnel_server, wstunnel_port,
    )

    result = deploy(
        token=token,
        hpc_host=hpc_host,
        ssh_port=ssh_port,
        wstunnel_server=wstunnel_server,
        wstunnel_port=wstunnel_port,
        wstunnel_secret=wstunnel_secret,
        wstunnel_local_port=wstunnel_local_port,
    )

    # ── Auto-save config on success ─────────────────────────────────
    if result["success"]:
        try:
            from saved_deployments import save_config
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
def hpc_status():
    """Query supervisorctl status on the remote HPC node."""
    user = _require_login()
    if user is None:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    token = _get_token()

    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    from .hpc_client import get_status
    result = get_status(token=token, hpc_host=hpc_host, ssh_port=ssh_port)

    return render_template(
        "hpc_result.html",
        result=result,
        action="status",
        hpc_host=hpc_host,
    )


@hpc_bp.route("/start", methods=["POST"])
def hpc_start():
    """Start all supervisord-managed services on the remote HPC node."""
    user = _require_login()
    if user is None:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    token = _get_token()
    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    from .hpc_client import start_services
    result = start_services(token=token, hpc_host=hpc_host, ssh_port=ssh_port)

    return render_template(
        "hpc_result.html",
        result=result,
        action="start",
        hpc_host=hpc_host,
    )


@hpc_bp.route("/stop", methods=["POST"])
def hpc_stop():
    """Stop all supervisord-managed services on the remote HPC node."""
    user = _require_login()
    if user is None:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    token = _get_token()
    hpc_host     = request.form.get("hpc_host", "").strip()
    ssh_port_str = request.form.get("ssh_port", "22").strip()

    if not hpc_host:
        flash("HPC hostname is required.", "error")
        return redirect(url_for("hpc.hpc_page"))

    try:
        ssh_port = int(ssh_port_str)
    except ValueError:
        ssh_port = 22

    from .hpc_client import stop_services
    result = stop_services(token=token, hpc_host=hpc_host, ssh_port=ssh_port)

    return render_template(
        "hpc_result.html",
        result=result,
        action="stop",
        hpc_host=hpc_host,
    )


@hpc_bp.route("/<config_id>/save", methods=["POST"])
def hpc_save(config_id: str):
    """
    Persist (or re-persist) an HPC config to the saved_deployments store.

    Called from the deployments page or hpc_result page.
    """
    user = _require_login()
    if user is None:
        return redirect(url_for("login"))

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
        from saved_deployments import save_config
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

    return redirect(url_for("hpc.hpc_page"))
