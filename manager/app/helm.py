"""
app/helm.py — Web GUI routes for InterLink Helm chart operations.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

Routes
------
GET  /releases                  List Helm releases
POST /releases/<name>/delete    Uninstall the InterLink release
POST /releases/<name>/save      Save InterLink release config
GET  /helm                      Deploy-a-Chart form
POST /helm/install              Submit the InterLink install form
"""

import logging

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from app.api_client import LONG_TIMEOUT, api_delete, api_get, api_post
from lib.saved_deployments import list_configs

logger = logging.getLogger(__name__)

helm_bp = Blueprint("app_helm", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/releases")
@require_login
def releases():
    """List Helm releases in the user's namespace."""
    namespace = session["namespace"]
    error = None
    release_list = []

    try:
        # InterLink is the only managed Helm deployment; retrieve its values
        # as a single-item list to populate the releases table.
        result = api_get("/api/interlink")
        if result.get("success"):
            release_list = [{"name": "interlink", "namespace": namespace, "status": "deployed"}]
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            pass  # No release deployed yet — empty list is the correct state
        else:
            error = f"Cannot list Helm releases: {_api_error(exc)}"
            logger.error(error)
    except Exception as exc:
        error = f"Cannot list Helm releases: {exc}"
        logger.error(error)

    return render_template(
        "releases.html",
        releases=release_list,
        namespace=namespace,
        error=error,
    )


@helm_bp.route("/releases/<name>/delete", methods=["POST"])
@require_login
def delete_release(name):
    """Uninstall the InterLink Helm release."""
    try:
        api_delete("/api/interlink")
        flash(f"Release '{name}' uninstalled successfully.", "success")
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Failed to uninstall release: {msg}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.jobs"))


@helm_bp.route("/helm")
@require_login
def helm_deploy():
    """Render the Deploy a Chart form (helm.html)."""
    namespace = session["namespace"]
    saved = list_configs(namespace, kind="helm") if namespace else []
    return render_template("helm.html", saved_configs=saved)


@helm_bp.route("/helm/install", methods=["POST"])
@require_login
def helm_install_route():
    """
    Submit the InterLink Helm install form.

    Reads the release_name, chart, version and values_yaml fields from the
    submitted form for display purposes, but always calls POST /api/interlink
    (the only managed Helm release).  Renders helm_result.html with the
    outcome.
    """
    namespace = session["namespace"]
    # Form fields are kept for display in the result template.
    release_name = request.form.get("release_name", "interlink").strip() or "interlink"
    chart = request.form.get("chart", "").strip()
    try:
        result = api_post("/api/interlink", timeout=LONG_TIMEOUT)
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc), "output": ""}
    except Exception as exc:
        result = {"success": False, "error": str(exc), "output": ""}

    return render_template(
        "helm_result.html",
        result=result,
        release_name=release_name,
        chart=chart,
        namespace=namespace,
    )


@helm_bp.route("/releases/<name>/save", methods=["POST"])
@require_login
def save_release(name):
    """Read the InterLink Helm release values from the cluster and save it."""
    from lib.saved_deployments import save_config

    namespace = session["namespace"]

    try:
        values_result = api_get("/api/interlink")
        values_yaml = values_result.get("values_yaml")

        config = {
            "release_name": name,
            "chart": "oci://ghcr.io/chbrandt/interlink",
            "version": None,
            "values_yaml": values_yaml,
        }
        save_config(namespace=namespace, kind="helm", config=config)
        flash(
            f"Configuration for release '{name}' saved. Load it from the Deploy Chart page.",
            "success",
        )
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Could not read release values: {msg}", "error")
    except Exception as exc:
        logger.error("Save release failed: %s", exc)
        flash(f"Failed to save release configuration: {exc}", "error")

    return redirect(url_for("app_k8s.jobs"))
