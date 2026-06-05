"""
app/helm.py — Web GUI routes for Helm chart operations.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

Routes
------
GET  /helm                      Helm chart deployment form
POST /helm/install              Install a Helm chart
GET  /releases                  List Helm releases
POST /releases/<name>/delete    Uninstall a release
POST /releases/<name>/save      Save release config
"""

import logging
import re

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from app.api_client import api_delete, api_get, api_post
from lib.saved_deployments import def_chart_is_singleton, list_configs, save_config

logger = logging.getLogger(__name__)

helm_bp = Blueprint("app_helm", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _validate_k8s_name(name: str) -> bool:
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/helm")
@require_login
def helm_page():
    """Helm chart deployment form."""
    namespace = session.get("namespace", "")
    saved = list_configs(namespace, kind="helm") if namespace else []
    return render_template("helm.html", saved_configs=saved)


@helm_bp.route("/helm/install", methods=["POST"])
@require_login
def helm_install_route():
    """Handle Helm chart installation."""
    namespace = session["namespace"]

    release_name = request.form.get("release_name", "").strip()
    chart = request.form.get("chart", "").strip()
    version = request.form.get("version", "").strip() or None
    values_yaml = request.form.get("values_yaml", "").strip() or None

    # Validate required fields
    if not release_name:
        flash("Release name is required.", "error")
        return redirect(url_for("app_helm.helm_page"))
    if not _validate_k8s_name(release_name):
        flash(
            "Invalid release name. Must be lowercase alphanumeric and hyphens, "
            "start/end with alphanumeric, max 63 characters.",
            "error",
        )
        return redirect(url_for("app_helm.helm_page"))
    if not chart:
        flash("Chart reference is required.", "error")
        return redirect(url_for("app_helm.helm_page"))

    # ── Singleton guard ───────────────────────────────────────────────
    # def_chart_is_singleton reads a local config file — app-side concern.
    try:
        if def_chart_is_singleton(chart):
            existing = api_get("/api/releases")
            chart_basename = chart.rstrip("/").split("/")[-1].lower()
            conflict = next(
                (
                    r for r in existing
                    if r["chart"].lower().startswith(chart_basename)
                    or r["name"].lower() == release_name.lower()
                ),
                None,
            )
            if conflict:
                flash(
                    f"You already have an '{chart_basename}' deployment "
                    f"(release '{conflict['name']}'). "
                    f"Only one '{chart_basename}' deployment is allowed per user. "
                    f"Delete the existing release first.",
                    "error",
                )
                return redirect(url_for("app_helm.helm_page"))
    except Exception as exc:
        logger.warning("Singleton check failed (non-fatal): %s", exc)

    try:
        result = api_post(
            "/api/helm/install",
            {
                "release_name": release_name,
                "chart": chart,
                "version": version,
                "values_yaml": values_yaml,
            },
        )
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        logger.error("Helm install failed: %s", msg)
        flash(f"Helm install failed: {msg}", "error")
        return redirect(url_for("app_helm.helm_page"))
    except Exception as exc:
        logger.error("Helm install failed: %s", exc)
        flash(f"Helm install failed: {exc}", "error")
        return redirect(url_for("app_helm.helm_page"))

    return render_template(
        "helm_result.html",
        result=result,
        release_name=release_name,
        chart=chart,
        namespace=namespace,
    )


@helm_bp.route("/releases")
@require_login
def releases():
    """List Helm releases in the user's namespace."""
    namespace = session["namespace"]
    error = None
    release_list = []

    try:
        release_list = api_get("/api/releases")
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
    """Uninstall a Helm release."""
    try:
        api_delete(f"/api/releases/{name}")
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
    """Read the Helm release values from the cluster and save it."""
    namespace = session["namespace"]

    try:
        releases_list = api_get("/api/releases")
        release_info = next((r for r in releases_list if r["name"] == name), None)
        if release_info is None:
            flash(f"Release '{name}' not found.", "error")
            return redirect(url_for("app_k8s.deployments"))

        values_result = api_get(f"/api/releases/{name}/values")
        values_yaml = values_result.get("values_yaml")

        chart_field = release_info.get("chart", "")
        config = {
            "release_name": name,
            "chart": chart_field,
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
