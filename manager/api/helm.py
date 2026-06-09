"""
api/helm.py — REST endpoints for InterLink Helm chart operations.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
POST /api/interlink
    Install the InterLink Helm chart using defaults from charts_config.yaml.

GET  /api/interlink
    Return the current values for the deployed InterLink Helm release.

DELETE /api/interlink
    Uninstall the InterLink Helm release.
"""

import json
import logging
import os

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from api.site_config import load_site_config
from lib.helm_client import helm_get_values, helm_install, helm_uninstall
from lib.k8s_client import K8sClient
from lib.saved_deployments import _resolve_placeholders, load_default_charts

logger = logging.getLogger(__name__)

helm_bp = Blueprint("api_helm", __name__, url_prefix="/api")

# Release name used by the interlink default chart
_INTERLINK_RELEASE = "interlink"


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    return K8sClient(kubeconfig_path=os.environ.get("KUBECONFIG"))


def _ok(data: dict | list, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


def _get_interlink_chart_config() -> dict | None:
    """Return the interlink default-chart config entry, or None if not found."""
    for chart in load_default_charts():
        if chart.get("release_name", "").lower() == _INTERLINK_RELEASE:
            return chart
    return None


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/interlink", methods=["POST"])
@require_token
def deploy_interlink():
    """
    Install the InterLink Helm chart into the user's namespace.

    All chart settings (chart reference, version, and default values) are read
    from *charts_config.yaml*.  No request body is required.

    Only one InterLink deployment per user is allowed (singleton constraint).
    """
    claims = get_request_claims()
    namespace = claims["namespace"]

    chart_cfg = _get_interlink_chart_config()
    if chart_cfg is None:
        return _err("InterLink chart configuration not found in charts_config.yaml.", 500)

    chart = chart_cfg.get("chart", "")
    version = chart_cfg.get("version") or None
    raw_values = chart_cfg.get("values_yaml") or ""
    site_cfg = load_site_config()
    values_yaml = _resolve_placeholders(raw_values, namespace, site_cfg) or None

    try:
        k8s = _get_k8s()

        # ── Singleton guard ───────────────────────────────────────────
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                return _err(
                    f"Failed to prepare namespace: {ns_result['error']}", 500
                )

        result = helm_install(
            release_name=_INTERLINK_RELEASE,
            chart=chart,
            namespace=namespace,
            values_yaml=values_yaml,
            version=version,
        )
        code = 201 if result.get("success") else 400
        return _ok(result, code)

    except Exception as exc:
        logger.error("deploy_interlink failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/interlink", methods=["GET"])
@require_token
def get_interlink_values():
    """Return the current values for the deployed InterLink Helm release."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        result = helm_get_values(
            release_name=_INTERLINK_RELEASE, namespace=namespace
        )
        if not result.get("success"):
            return _err(result.get("error", "Could not retrieve values"), 404)
        return _ok(result)
    except Exception as exc:
        logger.error("get_interlink_values failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/interlink", methods=["DELETE"])
@require_token
def delete_interlink():
    """Uninstall the InterLink Helm release."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        result = helm_uninstall(
            release_name=_INTERLINK_RELEASE, namespace=namespace
        )
        if result.get("success"):
            return _ok(result)
        return _err(result.get("error", "Uninstall failed"), 400)
    except Exception as exc:
        logger.error("delete_interlink failed: %s", exc)
        return _err(str(exc), 500)
