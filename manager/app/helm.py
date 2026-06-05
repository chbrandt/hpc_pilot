"""
app/helm.py — Web GUI routes for InterLink Helm chart operations.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

Routes
------
GET  /releases                  List Helm releases
POST /releases/<name>/delete    Uninstall the InterLink release
POST /releases/<name>/save      Save InterLink release config
"""

import logging

import requests
from flask import Blueprint, flash, redirect, render_template, session, url_for

from app.auth import require_login
from app.api_client import api_delete, api_get

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
        result = api_get("/api/interlink/values")
        if result.get("success"):
            release_list = [{"name": "interlink", "namespace": namespace, "status": "deployed"}]
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
        api_delete("/api/interlink/deploy")
        flash(f"Release '{name}' uninstalled successfully.", "success")
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Failed to uninstall release: {msg}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))


@helm_bp.route("/releases/<name>/save", methods=["POST"])
@require_login
def save_release(name):
    """Read the InterLink Helm release values from the cluster and save it."""
    from lib.saved_deployments import save_config

    namespace = session["namespace"]

    try:
        values_result = api_get("/api/interlink/values")
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

    return redirect(url_for("app_k8s.deployments"))
