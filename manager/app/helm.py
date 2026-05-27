"""
app/helm.py — Web GUI routes for Helm chart operations.

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

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from lib.helm_client import helm_get_values, helm_install, helm_list, helm_uninstall
from lib.k8s_client import K8sClient
from lib.saved_deployments import def_chart_is_singleton, list_configs, save_config
import os

logger = logging.getLogger(__name__)

helm_bp = Blueprint("app_helm", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    return K8sClient(kubeconfig_path=os.environ.get("KUBECONFIG"))


def _validate_k8s_name(name: str) -> bool:
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


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
    try:
        if def_chart_is_singleton(chart):
            existing = helm_list(namespace=namespace)
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
        logger.warning(f"Singleton check failed (non-fatal): {exc}")

    try:
        k8s = _get_k8s()

        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                flash(f"Failed to prepare namespace: {ns_result['error']}", "error")
                return redirect(url_for("app_helm.helm_page"))

        result = helm_install(
            release_name=release_name,
            chart=chart,
            namespace=namespace,
            values_yaml=values_yaml,
            version=version,
        )

    except Exception as exc:
        logger.error(f"Helm install failed: {exc}")
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
        release_list = helm_list(namespace=namespace)
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
    namespace = session["namespace"]

    try:
        result = helm_uninstall(release_name=name, namespace=namespace)
        if result["success"]:
            flash(f"Release '{name}' uninstalled successfully.", "success")
        else:
            flash(f"Failed to uninstall release: {result['error']}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))


@helm_bp.route("/releases/<name>/save", methods=["POST"])
@require_login
def save_release(name):
    """Read the Helm release values from the cluster and save it."""
    namespace = session["namespace"]

    try:
        releases_list = helm_list(namespace=namespace)
        release_info = next((r for r in releases_list if r["name"] == name), None)
        if release_info is None:
            flash(f"Release '{name}' not found.", "error")
            return redirect(url_for("app_k8s.deployments"))

        values_result = helm_get_values(release_name=name, namespace=namespace)
        values_yaml = (
            values_result.get("values_yaml") if values_result.get("success") else None
        )

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
    except Exception as exc:
        logger.error(f"Save release failed: {exc}")
        flash(f"Failed to save release configuration: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))
