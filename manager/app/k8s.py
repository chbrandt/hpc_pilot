"""
app/k8s.py — Web GUI routes for Kubernetes job management.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

Routes
------
GET  /                           Main job submission form
POST /submit                     Submit a new job
GET  /jobs                       List all jobs + Helm releases
POST /jobs/<ns>/<name>/delete    Delete a job
GET  /jobs/<ns>/<name>/status    AJAX status poll (JSON)
POST /jobs/<ns>/<name>/save      Save job config
"""

import json
import logging
import re

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import require_login
from app.api_client import api_delete, api_get, api_post
from lib.saved_deployments import list_configs, save_config

logger = logging.getLogger(__name__)

k8s_bp = Blueprint("app_k8s", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _validate_k8s_name(name: str) -> bool:
    """Validate a Kubernetes resource name (RFC 1123 subdomain)."""
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


# ── Routes ────────────────────────────────────────────────────────────


@k8s_bp.route("/")
@require_login
def index():
    """Main page with job submission form."""
    namespace = session.get("namespace", "")
    saved = list_configs(namespace, kind="container") if namespace else []

    # Fetch available InterLink virtual-kubelet nodes for the dropdown
    interlink_nodes = []
    error = None
    try:
        result = api_get("/api/nodes/interlink")
        interlink_nodes = result.get("nodes", [])
    except requests.HTTPError as exc:
        if exc.response.status_code != 401:
            error = f"Cannot reach API: {exc}"
    except Exception as exc:
        error = f"Cannot reach API: {exc}"

    return render_template(
        "index.html",
        error=error,
        saved_configs=saved,
        interlink_nodes=interlink_nodes,
    )


@k8s_bp.route("/submit", methods=["POST"])
@require_login
def submit_job():
    """Handle job submission form."""
    namespace = session["namespace"]

    name = request.form.get("name", "").strip()
    image = request.form.get("image", "").strip()
    node_name = request.form.get("node_name", "").strip()
    command = request.form.get("command", "").strip() or None

    # Environment variables (from dynamic form fields)
    env_keys = request.form.getlist("env_key")
    env_values = request.form.getlist("env_value")
    env_vars = {}
    for k, v in zip(env_keys, env_values):
        k = k.strip()
        if k:
            env_vars[k] = v.strip()
    env_vars = env_vars or None

    # Validate required fields
    if not name:
        flash("Job name is required.", "error")
        return redirect(url_for("app_k8s.index"))
    if not _validate_k8s_name(name):
        flash(
            "Invalid job name. Must be lowercase alphanumeric and hyphens, "
            "start/end with alphanumeric, max 63 characters.",
            "error",
        )
        return redirect(url_for("app_k8s.index"))
    if not image:
        flash("Container image is required.", "error")
        return redirect(url_for("app_k8s.index"))
    if not node_name:
        flash("InterLink node name is required.", "error")
        return redirect(url_for("app_k8s.index"))

    try:
        result = api_post(
            "/api/jobs",
            {
                "name": name,
                "image": image,
                "node_name": node_name,
                "env_vars": env_vars,
                "command": command,
            },
        )
        return render_template("status.html", result=result)

    except requests.HTTPError as exc:
        msg = _api_error(exc)
        logger.error("Job submission failed: %s", msg)
        flash(f"Job submission failed: {msg}", "error")
        return redirect(url_for("app_k8s.index"))
    except Exception as exc:
        logger.error("Job submission failed: %s", exc)
        flash(f"Job submission failed: {exc}", "error")
        return redirect(url_for("app_k8s.index"))


@k8s_bp.route("/jobs")
@require_login
def jobs():
    """List all user workloads: jobs and Helm releases merged."""
    errors = []
    workloads = []

    # ── Container jobs (K8s Deployments) ──────────────────────────────
    try:
        for job in api_get("/api/jobs"):
            workloads.append(
                {
                    "kind": "container",
                    "name": job["name"],
                    "namespace": job["namespace"],
                    "detail": job.get("image", ""),
                    "node_name": job.get("node_name", ""),
                    "status": job.get("status", "unknown"),
                    "created": job.get("created", ""),
                }
            )
    except Exception as exc:
        errors.append(f"Jobs: {exc}")
        logger.error("Could not list jobs: %s", exc)

    # ── Helm releases ─────────────────────────────────────────────────
    # The only managed Helm release is 'interlink'; check its presence
    # via GET /api/interlink (returns {"success": true, ...} when deployed).
    try:
        result = api_get("/api/interlink")
        if result.get("success"):
            workloads.append(
                {
                    "kind": "helm",
                    "name": "interlink",
                    "namespace": session["namespace"],
                    "detail": "oci://ghcr.io/chbrandt/interlink",
                    "status": "deployed",
                    "created": "",
                }
            )
    except Exception as exc:
        errors.append(f"Helm releases: {exc}")
        logger.error("Could not list Helm releases: %s", exc)

    namespace = session["namespace"]
    error = "; ".join(errors) if errors else None
    return render_template(
        "deployments.html",
        workloads=workloads,
        namespace=namespace,
        error=error,
    )


@k8s_bp.route("/jobs/<namespace>/<name>/delete", methods=["POST"])
@require_login
def delete_job(namespace, name):
    """Delete a job."""
    # Security: users can only delete from their own namespace
    if namespace != session["namespace"]:
        flash("You can only delete jobs in your own namespace.", "error")
        return redirect(url_for("app_k8s.jobs"))

    try:
        api_delete(f"/api/jobs/{name}")
        flash(f"Job '{name}' deleted successfully.", "success")
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Failed to delete job: {msg}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.jobs"))


@k8s_bp.route("/jobs/<namespace>/<name>/status")
@require_login
def job_status(namespace, name):
    """Get job status as JSON (for AJAX refresh)."""
    try:
        status = api_get(f"/api/jobs/{name}/status")
        return json.dumps(status), 200, {"Content-Type": "application/json"}
    except requests.HTTPError as exc:
        try:
            body = exc.response.json()
        except Exception:
            body = {"error": str(exc)}
        return (
            json.dumps(body),
            exc.response.status_code,
            {"Content-Type": "application/json"},
        )
    except Exception as exc:
        return (
            json.dumps({"error": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )


@k8s_bp.route("/jobs/<namespace>/<name>/save", methods=["POST"])
@require_login
def save_job(namespace, name):
    """Read the full job spec from the cluster and save it."""
    # Security: only allow saving from the user's own namespace
    if namespace != session["namespace"]:
        flash("You can only save jobs from your own namespace.", "error")
        return redirect(url_for("app_k8s.jobs"))

    try:
        spec = api_get(f"/api/jobs/{name}")
        save_config(namespace=namespace, kind="container", config=spec)
        flash(
            f"Configuration for '{name}' saved. Load it from the Submit page.",
            "success",
        )
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Could not read job spec: {msg}", "error")
    except Exception as exc:
        logger.error("Save job failed: %s", exc)
        flash(f"Failed to save configuration: {exc}", "error")

    return redirect(url_for("app_k8s.jobs"))
