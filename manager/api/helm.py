"""
api/helm.py — REST endpoints for InterLink Helm chart operations.

All routes are JSON-only and protected by Bearer-token auth.

One InterLink virtual-kubelet is deployed per (user, HPC node) pair: the
Helm release is named interlink-<hpc_name> and the virtual-kubelet node
vk-node-<user-hash>-<hpc_name>, so a user may deploy multiple InterLink
nodes, one per configured HPC target (see manager/hpc/*.yaml).

Endpoints
---------
POST /api/interlink
    Install the InterLink Helm chart for a given hpc_name, using defaults
    from charts_config.yaml.

GET  /api/interlink
    Return the current values for the InterLink Helm release bound to a
    given hpc_name.

DELETE /api/interlink
    Uninstall the InterLink Helm release bound to a given hpc_name.
"""

import json
import logging
import os

import yaml

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from api.site_config import load_site_config
from lib.helm_client import helm_get_values, helm_install, helm_uninstall
from lib.hpc_config import load_hpc_config
from lib.k8s_client import K8sClient
from lib.saved_deployments import _resolve_placeholders, load_default_charts

logger = logging.getLogger(__name__)

helm_bp = Blueprint("api_helm", __name__, url_prefix="/api")

# Stable key used to look up the InterLink chart entry in charts_config.yaml.
# This is independent of the per-HPC Helm release name (interlink-<hpc_name>).
_INTERLINK_CHART_KEY = "interlink"


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    return K8sClient(kubeconfig_path=os.environ.get("KUBECONFIG"))


def _ok(data: dict | list, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


def _interlink_release_name(hpc_name: str) -> str:
    """
    Return the Helm release name for the InterLink deployment on *hpc_name*.
    One InterLink virtual-kubelet is deployed per (user, HPC node) pair,
    so each HPC target gets its own release: interlink-<hpc_name>.
    """
    return f"interlink-{hpc_name}"


def _vk_node_name(namespace: str, hpc_name: str) -> str:
    """
    Return the virtual-kubelet node name for the (user, HPC node) pair.

    Pattern: vk-node-<user-hash>-<hpc_name>, where <user-hash> is the
    16-hex-char hash of the user sub claim - the same digest used by
    derive_namespace (user-<hash>).
    """
    user_hash = namespace.removeprefix("user-")
    return f"vk-node-{user_hash}-{hpc_name}"


def _get_interlink_chart_config() -> dict | None:
    """Return the interlink default-chart config entry, or None if not found."""
    for chart in load_default_charts():
        if chart.get("release_name", "").lower() == _INTERLINK_CHART_KEY:
            return chart
    return None


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/interlink", methods=["POST"])
@require_token
def deploy_interlink():
    """
    Install the InterLink Helm chart into the user's namespace,
    bound to a specific HPC node.

    JSON body keys:
        hpc_name*  str  HPC node name (from manager/hpc/*.yaml) served by
                        this virtual-kubelet (required).

    The release is named interlink-<hpc_name> and the virtual-kubelet node
    vk-node-<user-hash>-<hpc_name>: one InterLink virtual node per
    (user, HPC target) pair.  All other chart settings come from
    *charts_config.yaml*.
    """
    claims = get_request_claims()
    namespace = claims["namespace"]

    body = request.get_json(silent=True) or {}
    hpc_name = body.get("hpc_name", "").strip()
    if not hpc_name:
        return _err("'hpc_name' is required.")
    try:
        load_hpc_config(hpc_name)
    except ValueError as exc:
        return _err(str(exc))

    chart_cfg = _get_interlink_chart_config()
    if chart_cfg is None:
        return _err("InterLink chart configuration not found in charts_config.yaml.", 500)

    chart = chart_cfg.get("chart", "")
    version = chart_cfg.get("version") or None
    raw_values = chart_cfg.get("values_yaml") or ""
    site_cfg = load_site_config()
    values_yaml = _resolve_placeholders(raw_values, namespace, site_cfg) or ""

    # Pin the virtual-kubelet node name to this (user, HPC) pair
    values = yaml.safe_load(values_yaml) or {}
    values["nodeName"] = _vk_node_name(namespace, hpc_name)
    values_yaml = yaml.safe_dump(values) or None

    release_name = _interlink_release_name(hpc_name)

    try:
        k8s = _get_k8s()

        # ── Namespace preparation ─────────────────────────────────────
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                return _err(
                    f"Failed to prepare namespace: {ns_result['error']}", 500
                )

        result = helm_install(
            release_name=release_name,
            chart=chart,
            namespace=namespace,
            values_yaml=values_yaml,
            version=version,
        )

        # ── Approve the virtual-kubelet's serving-cert CSR (fallback) ──
        # InterLink is deployed with virtualNode.disableCSR: true by default,
        # so the virtual-kubelet serves :10250 with a self-signed cert and no
        # CSR is created.  On clusters that verify kubelet certs against a CA,
        # disableCSR is false and the freshly installed virtual-kubelet
        # requests a kubelet-serving certificate via a CSR; no Kubernetes
        # controller auto-approves those, so without this the API server could
        # not proxy pod logs from the virtual node (TLS handshake errors).
        # Best-effort: failures are logged inside the helper and do not
        # fail the install.  The node name is the (user, HPC) virtual-kubelet
        # node that the freshly installed VK's ServiceAccount is named after.
        try:
            approved = k8s.approve_pending_csrs(
                namespace=namespace,
                node_names=[_vk_node_name(namespace, hpc_name)],
            )
            if approved:
                logger.info(
                    "Approved InterLink CSR(s) %s for namespace %s",
                    approved, namespace,
                )
        except Exception as exc:
            logger.warning(
                "CSR auto-approval failed for namespace %s: %s", namespace, exc
            )

        code = 201 if result.get("success") else 400
        return _ok(result, code)

    except Exception as exc:
        logger.error("deploy_interlink failed: %s", exc)
        return _err(str(exc), 500)


@helm_bp.route("/interlink", methods=["GET"])
@require_token
def get_interlink_values():
    """
    Return the current values for the deployed InterLink Helm release.

    Query parameters:
        hpc_name*  str  HPC node name identifying which InterLink release to
                        inspect (required; each HPC target has its own
                        release, interlink-<hpc_name>).
    """
    claims = get_request_claims()
    namespace = claims["namespace"]
    hpc_name = request.args.get("hpc_name", "").strip()
    if not hpc_name:
        return _err("'hpc_name' is required.")
    try:
        result = helm_get_values(
            release_name=_interlink_release_name(hpc_name), namespace=namespace
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
    """
    Uninstall the InterLink Helm release.

    JSON body keys:
        hpc_name*  str  HPC node name identifying which InterLink release to
                        remove (required).
    """
    claims = get_request_claims()
    namespace = claims["namespace"]
    body = request.get_json(silent=True) or {}
    hpc_name = body.get("hpc_name", "").strip()
    if not hpc_name:
        return _err("'hpc_name' is required.")
    try:
        result = helm_uninstall(
            release_name=_interlink_release_name(hpc_name), namespace=namespace
        )
        if result.get("success"):
            return _ok(result)
        return _err(result.get("error", "Uninstall failed"), 400)
    except Exception as exc:
        logger.error("delete_interlink failed: %s", exc)
        return _err(str(exc), 500)
