"""
app/k8s.py — Web GUI routes for Kubernetes container deployments.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

Routes
------
GET  /                           Main deployment form (index)
POST /deploy                     Submit a new container deployment
GET  /deployments                List all deployments + Helm releases
POST /deployments/<ns>/<name>/delete   Delete a deployment
GET  /deployments/<ns>/<name>/status   AJAX status poll (JSON)
POST /deployments/<ns>/<name>/save     Save deployment config
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
    """Main page with deployment form."""
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


@k8s_bp.route("/deploy", methods=["POST"])
@require_login
def deploy():
    """Handle deployment form submission."""
    namespace = session["namespace"]

    name = request.form.get("name", "").strip()
    image = request.form.get("image", "").strip()
    node_name = request.form.get("node_name", "").strip()
    replicas_str = request.form.get("replicas", "1").strip()
    cpu_request = request.form.get("cpu_request", "").strip() or None
    cpu_limit = request.form.get("cpu_limit", "").strip() or None
    mem_request = request.form.get("mem_request", "").strip() or None
    mem_limit = request.form.get("mem_limit", "").strip() or None
    command = request.form.get("command", "").strip() or None

    # Parse replicas
    try:
        replicas = int(replicas_str)
        if replicas < 1:
            raise ValueError
    except (ValueError, TypeError):
        flash("Replicas must be a positive integer.", "error")
        return redirect(url_for("app_k8s.index"))

    # Environment variables (from dynamic form fields)
    env_keys = request.form.getlist("env_key")
    env_values = request.form.getlist("env_value")
    env_vars = {}
    for k, v in zip(env_keys, env_values):
        k = k.strip()
        if k:
            env_vars[k] = v.strip()
    env_vars = env_vars or None

    # Parse ports (multi-port support)
    port_numbers = request.form.getlist("port_number")
    port_names = request.form.getlist("port_name")
    port_protocols = request.form.getlist("port_protocol")
    ports = []
    for num_str, pname, proto in zip(port_numbers, port_names, port_protocols):
        num_str = num_str.strip()
        if not num_str:
            continue
        try:
            num = int(num_str)
            if not (1 <= num <= 65535):
                flash(f"Port {num_str} must be between 1 and 65535.", "error")
                return redirect(url_for("app_k8s.index"))
        except ValueError:
            flash(f"Port '{num_str}' is not a valid number.", "error")
            return redirect(url_for("app_k8s.index"))
        ports.append(
            {
                "number": num,
                "name": pname.strip() or None,
                "protocol": proto.strip() or "TCP",
            }
        )
    ports = ports or None

    # Parse ingress config (only valid when ports are defined)
    ingress = None
    if ports and request.form.get("ingress_enabled"):
        ingress = {
            "host": request.form.get("ingress_host", "").strip(),
            "path": request.form.get("ingress_path", "/").strip() or "/",
            "port": request.form.get("ingress_port", "").strip() or None,
            "class": request.form.get("ingress_class", "").strip() or None,
        }

    # Validate required fields
    if not name:
        flash("Deployment name is required.", "error")
        return redirect(url_for("app_k8s.index"))
    if not _validate_k8s_name(name):
        flash(
            "Invalid deployment name. Must be lowercase alphanumeric and hyphens, "
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
            "/api/deployments",
            {
                "name": name,
                "image": image,
                "node_name": node_name,
                "replicas": replicas,
                "cpu_request": cpu_request,
                "cpu_limit": cpu_limit,
                "mem_request": mem_request,
                "mem_limit": mem_limit,
                "env_vars": env_vars,
                "ports": ports,
                "command": command,
                "ingress": ingress,
            },
        )
        return render_template("status.html", result=result)

    except requests.HTTPError as exc:
        msg = _api_error(exc)
        logger.error("Deployment failed: %s", msg)
        flash(f"Deployment failed: {msg}", "error")
        return redirect(url_for("app_k8s.index"))
    except Exception as exc:
        logger.error("Deployment failed: %s", exc)
        flash(f"Deployment failed: {exc}", "error")
        return redirect(url_for("app_k8s.index"))


@k8s_bp.route("/deployments")
@require_login
def deployments():
    """List all user workloads: container deployments and Helm releases merged."""
    errors = []
    workloads = []

    # ── Container deployments (K8s Deployments) ───────────────────────
    try:
        for dep in api_get("/api/deployments"):
            workloads.append(
                {
                    "kind": "container",
                    "name": dep["name"],
                    "namespace": dep["namespace"],
                    "detail": dep.get("image", ""),
                    "status": dep.get("status", "unknown"),
                    "status_label": dep.get("replicas_status", ""),
                    "created": dep.get("created", ""),
                    "service_ports": dep.get("service_ports"),
                    "ingress_url": dep.get("ingress_url"),
                }
            )
    except Exception as exc:
        errors.append(f"Deployments: {exc}")
        logger.error("Could not list container deployments: %s", exc)

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
                    "status_label": "deployed",
                    "created": "",
                    "service_ports": None,
                    "ingress_url": None,
                    "app_version": "",
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


@k8s_bp.route("/deployments/<namespace>/<name>/delete", methods=["POST"])
@require_login
def delete_deployment(namespace, name):
    """Delete a deployment and its associated service."""
    # Security: users can only delete from their own namespace
    if namespace != session["namespace"]:
        flash("You can only delete deployments in your own namespace.", "error")
        return redirect(url_for("app_k8s.deployments"))

    try:
        api_delete(f"/api/deployments/{name}")
        flash(f"Deployment '{name}' deleted successfully.", "success")
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Failed to delete deployment: {msg}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))


@k8s_bp.route("/deployments/<namespace>/<name>/status")
@require_login
def deployment_status(namespace, name):
    """Get deployment status as JSON (for AJAX refresh)."""
    try:
        status = api_get(f"/api/deployments/{name}/status")
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


@k8s_bp.route("/deployments/<namespace>/<name>/save", methods=["POST"])
@require_login
def save_deployment(namespace, name):
    """Read the full deployment spec from the cluster and save it."""
    # Security: only allow saving from the user's own namespace
    if namespace != session["namespace"]:
        flash("You can only save deployments from your own namespace.", "error")
        return redirect(url_for("app_k8s.deployments"))

    try:
        spec = api_get(f"/api/deployments/{name}")
        save_config(namespace=namespace, kind="container", config=spec)
        flash(
            f"Configuration for '{name}' saved. Load it from the Deploy page.",
            "success",
        )
    except requests.HTTPError as exc:
        msg = _api_error(exc)
        flash(f"Could not read deployment spec: {msg}", "error")
    except Exception as exc:
        logger.error("Save deployment failed: %s", exc)
        flash(f"Failed to save configuration: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))
