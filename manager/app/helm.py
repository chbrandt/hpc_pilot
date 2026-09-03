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
from lib.hpc_config import list_hpc_nodes

logger = logging.getLogger(__name__)

helm_bp = Blueprint("app_helm", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


def _hpc_name_from_release(release_name: str) -> str:
    """Recover the hpc_name from a release name of the form interlink-<hpc_name>."""
    return release_name.removeprefix("interlink-")


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/releases")
@require_login
def releases():
    """
    List InterLink Helm releases in the user's namespace.

    One release may exist per configured HPC node (interlink-<hpc_name>);
    each is checked individually via GET /api/interlink?hpc_name=<name>.
    """
    namespace = session["namespace"]
    error = None
    release_list = []

    for node in list_hpc_nodes():
        hpc_name = node["name"]
        try:
            result = api_get("/api/interlink", params={"hpc_name": hpc_name})
            if result.get("success"):
                release_list.append(
                    {
                        "name": f"interlink-{hpc_name}",
                        "hpc_name": hpc_name,
                        "namespace": namespace,
                        "status": "deployed",
                    }
                )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                continue  # No release deployed on this HPC node yet
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
    """Uninstall the InterLink Helm release bound to the HPC node in *name*."""
    hpc_name = _hpc_name_from_release(name)
    try:
        api_delete("/api/interlink", body={"hpc_name": hpc_name})
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
    hpc_nodes = list_hpc_nodes()
    return render_template("helm.html", hpc_nodes=hpc_nodes)


@helm_bp.route("/helm/install", methods=["POST"])
@require_login
def helm_install_route():
    """
    Submit the InterLink Helm install form.

    Reads hpc_name (required) from the submitted form and calls
    POST /api/interlink, which deploys one InterLink virtual-kubelet per
    (user, HPC node) pair.  Renders helm_result.html with the outcome.
    """
    namespace = session["namespace"]
    hpc_name = request.form.get("hpc_name", "").strip()
    release_name = f"interlink-{hpc_name}" if hpc_name else "interlink"

    if not hpc_name:
        flash("HPC node selection is required.", "error")
        return redirect(url_for("app_helm.helm_deploy"))

    try:
        result = api_post(
            "/api/interlink", {"hpc_name": hpc_name}, timeout=LONG_TIMEOUT
        )
    except requests.HTTPError as exc:
        result = {"success": False, "error": _api_error(exc), "output": ""}
    except Exception as exc:
        result = {"success": False, "error": str(exc), "output": ""}

    return render_template(
        "helm_result.html",
        result=result,
        release_name=release_name,
        chart="",
        namespace=namespace,
    )


@helm_bp.route("/releases/<name>/save", methods=["POST"])
@require_login
def save_release(name):
    """Read the InterLink Helm release values from the cluster and save it."""
    from lib.saved_deployments import save_config

    namespace = session["namespace"]
    hpc_name = _hpc_name_from_release(name)

    try:
        values_result = api_get("/api/interlink", params={"hpc_name": hpc_name})
        values_yaml = values_result.get("values_yaml")

        config = {
            "release_name": name,
            "chart": "oci://ghcr.io/chbrandt/interlink",
            "version": None,
            "hpc_name": hpc_name,
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
