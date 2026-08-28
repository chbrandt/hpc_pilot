"""
api/k8s.py — REST endpoints for Kubernetes job management.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
POST /api/namespaces/ensure
    Idempotently create the user's personal namespace (derived from the token sub
    claim).  Safe to call on every login — does nothing if the namespace exists.

GET  /api/nodes/interlink
    Return the list of InterLink virtual-kubelet node names available in the cluster.

GET  /api/jobs
    List all jobs in the user's namespace.

POST /api/jobs
    Create a new job.
    JSON body mirrors the parameters of lib.k8s_client.K8sClient.create_job.

GET  /api/jobs/<name>
    Return the full spec of a single job (for saving / re-submitting).

GET  /api/jobs/<name>/status
    Get detailed status for a single job.

GET  /api/jobs/<name>/output
    Retrieve the job's output (stdout/stderr) via the pod log endpoint.

DELETE /api/jobs/<name>
    Delete a job.
"""

import json
import logging
import os

from flask import Blueprint, request

from api.auth import get_request_claims, require_token
from lib.k8s_client import K8sClient

logger = logging.getLogger(__name__)

k8s_bp = Blueprint("api_k8s", __name__, url_prefix="/api")


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    kubeconfig = os.environ.get("KUBECONFIG")
    return K8sClient(kubeconfig_path=kubeconfig)


def _ok(data: dict | list, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


# ── Routes ────────────────────────────────────────────────────────────


@k8s_bp.route("/namespaces/ensure", methods=["POST"])
@require_token
def ensure_namespace():
    """
    Idempotently create the user's personal namespace.

    Derives the namespace from the Bearer token's ``sub`` claim (same logic
    used by all other endpoints).  Safe to call on every login — returns
    ``{"created": false}`` when the namespace already exists.
    """
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        if k8s.namespace_exists(namespace):
            return _ok({"namespace": namespace, "created": False})
        result = k8s.create_namespace(namespace)
        if result["success"]:
            logger.info("ensure_namespace: created '%s'", namespace)
            return _ok({"namespace": namespace, "created": True}, 201)
        return _err(f"Failed to create namespace: {result.get('error')}", 500)
    except Exception as exc:
        logger.error("ensure_namespace failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/nodes/interlink", methods=["GET"])
@require_token
def list_interlink_nodes():
    """
    Return the names of InterLink virtual-kubelet nodes available in the cluster.

    A node is considered an interlink node when it carries the taint key
    ``virtual-node.interlink/no-schedule``.

    Returns:
        JSON object ``{"nodes": ["node-a", "node-b", ...]}``.
    """
    try:
        k8s = _get_k8s()
        nodes = k8s.list_interlink_nodes()
        return _ok({"nodes": nodes})
    except Exception as exc:
        logger.error("list_interlink_nodes failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs", methods=["GET"])
@require_token
def list_jobs():
    """List all jobs in the user's namespace."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        jobs = k8s.list_jobs(namespace=namespace)
        return _ok(jobs)
    except Exception as exc:
        logger.error("list_jobs failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs", methods=["POST"])
@require_token
def create_job():
    """
    Create a job targeting an InterLink virtual-kubelet node.

    InterLink maps the pod to an HPC batch job, so replica counts, resource
    requests/limits, container ports, and ingress are not supported.

    JSON body keys (all optional unless marked required):
        name*         str   job name (required)
        image*        str   container image (required)
        node_name*    str   InterLink virtual-kubelet node name (required)
        env_vars      dict  {"KEY": "value", ...}
        command       str   shell command override (run as /bin/sh -c)
    """
    claims = get_request_claims()
    namespace = claims["namespace"]

    body = request.get_json(silent=True) or {}

    name = body.get("name", "").strip()
    image = body.get("image", "").strip()
    node_name = body.get("node_name", "").strip()
    if not name:
        return _err("'name' is required.")
    if not image:
        return _err("'image' is required.")
    if not node_name:
        return _err("'node_name' is required.")

    try:
        k8s = _get_k8s()

        # Ensure namespace exists
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                return _err(f"Failed to prepare namespace: {ns_result['error']}", 500)

        result = k8s.create_job(
            name=name,
            image=image,
            node_name=node_name,
            namespace=namespace,
            env_vars=body.get("env_vars"),
            command=body.get("command"),
        )
        code = 201 if result.get("success") else 400
        return _ok(result, code)

    except Exception as exc:
        logger.error("create_job failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs/<name>", methods=["GET"])
@require_token
def get_job(name: str):
    """Return the full spec of a single job (for saving / re-submitting)."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        spec = k8s.get_job_spec(name=name, namespace=namespace)
        if "error" in spec:
            return _err(spec["error"], 404)
        return _ok(spec)
    except Exception as exc:
        logger.error("get_job failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs/<name>/status", methods=["GET"])
@require_token
def job_status(name: str):
    """Get detailed status for a single job."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        status = k8s.get_job_status(name=name, namespace=namespace)
        if "error" in status:
            return _err(status["error"], 404)
        return _ok(status)
    except Exception as exc:
        logger.error("job_status failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs/<name>/output", methods=["GET"])
@require_token
def job_output(name: str):
    """Retrieve a job's output (stdout/stderr) via the pod log endpoint."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        output = k8s.get_job_output(name=name, namespace=namespace)
        if "error" in output:
            return _err(output["error"], 404)
        return _ok(output)
    except Exception as exc:
        logger.error("job_output failed: %s", exc)
        return _err(str(exc), 500)


@k8s_bp.route("/jobs/<name>", methods=["DELETE"])
@require_token
def delete_job(name: str):
    """Delete a job."""
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        k8s = _get_k8s()
        result = k8s.delete_job(name=name, namespace=namespace)
        if result["job"] and result["job"]["success"]:
            return _ok(result)
        error = result["job"]["error"] if result["job"] else "Unknown error"
        return _err(error, 400)
    except Exception as exc:
        logger.error("delete_job failed: %s", exc)
        return _err(str(exc), 500)
