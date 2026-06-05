"""
api/helm.py — REST endpoints for Helm chart operations.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
GET  /api/releases
    List all Helm releases in the user's namespace.

POST /api/helm/install
    Install a Helm chart.
    JSON body: release_name, chart, version (opt), values_yaml (opt).

GET  /api/releases/<name>/values
    Return the current values for a deployed Helm release.

DELETE /api/releases/<name>
    Uninstall a Helm release.
"""

import json
import logging
import os

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from lib.helm_client import helm_get_values, helm_install, helm_list, helm_uninstall
from lib.k8s_client import K8sClient

logger = logging.getLogger(__name__)

helm_bp = Blueprint("api_helm", __name__, url_prefix="/api")


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    return K8sClient(kubeconfig_path=os.environ.get("KUBECONFIG"))


def _ok(data: dict | list, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/releases", methods=["GET"])
@require_token
def list_releases():
    """List Helm releases in the user's namespace."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        releases = helm_list(namespace=namespace)
        return _ok(releases)
    except Exception as exc:
        logger.error("list_releases failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/helm/install", methods=["POST"])
@require_token
def install_chart():
    """
    Install a Helm chart into the user's namespace.

    JSON body keys:
        release_name*  str  Kubernetes-valid release name (required)
        chart*         str  Chart reference, e.g. "bitnami/nginx" (required)
        version        str  Specific chart version to install (optional)
        values_yaml    str  Raw YAML overrides (optional)
    """
    claims = get_request_claims()
    namespace = claims["namespace"]

    body = request.get_json(silent=True) or {}
    release_name = body.get("release_name", "").strip()
    chart = body.get("chart", "").strip()

    if not release_name:
        return _err("'release_name' is required.")
    if not chart:
        return _err("'chart' is required.")

    try:
        k8s = _get_k8s()
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                return _err(f"Failed to prepare namespace: {ns_result['error']}", 500)

        result = helm_install(
            release_name=release_name,
            chart=chart,
            namespace=namespace,
            values_yaml=body.get("values_yaml"),
            version=body.get("version"),
        )
        code = 201 if result.get("success") else 400
        return _ok(result, code)

    except Exception as exc:
        logger.error("install_chart failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/releases/<name>/values", methods=["GET"])
@require_token
def get_release_values(name: str):
    """Return the current values for a deployed Helm release."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        result = helm_get_values(release_name=name, namespace=namespace)
        if not result.get("success"):
            return _err(result.get("error", "Could not retrieve values"), 404)
        return _ok(result)
    except Exception as exc:
        logger.error("get_release_values failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/releases/<name>", methods=["DELETE"])
@require_token
def delete_release(name: str):
    """Uninstall a Helm release."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        result = helm_uninstall(release_name=name, namespace=namespace)
        if result.get("success"):
            return _ok(result)
        return _err(result.get("error", "Uninstall failed"), 400)
    except Exception as exc:
        logger.error("delete_release failed: %s", exc)
        return _err(str(exc), 500)
