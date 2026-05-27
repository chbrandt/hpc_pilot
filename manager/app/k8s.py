"""
app/k8s.py — Web GUI routes for Kubernetes container deployments.

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
import os
import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import get_session_user, require_login
from lib.helm_client import helm_list
from lib.k8s_client import K8sClient
from lib.saved_deployments import list_configs, save_config

logger = logging.getLogger(__name__)

k8s_bp = Blueprint("app_k8s", __name__)


# ── Helpers ───────────────────────────────────────────────────────────


def _get_k8s() -> K8sClient:
    return K8sClient(kubeconfig_path=os.environ.get("KUBECONFIG"))


def _validate_k8s_name(name: str) -> bool:
    """Validate a Kubernetes resource name (RFC 1123 subdomain)."""
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


# ── Routes ────────────────────────────────────────────────────────────


@k8s_bp.route("/")
@require_login
def index():
    """Main page with deployment form."""
    error = None
    try:
        _get_k8s()
    except Exception as exc:
        error = f"Cannot connect to Kubernetes cluster: {exc}"
        logger.error(error)

    namespace = session.get("namespace", "")
    saved = list_configs(namespace, kind="container") if namespace else []
    return render_template("index.html", error=error, saved_configs=saved)


@k8s_bp.route("/deploy", methods=["POST"])
@require_login
def deploy():
    """Handle deployment form submission."""
    namespace = session["namespace"]

    name = request.form.get("name", "").strip()
    image = request.form.get("image", "").strip()
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

    try:
        k8s = _get_k8s()

        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                flash(f"Failed to prepare namespace: {ns_result['error']}", "error")
                return redirect(url_for("app_k8s.index"))

        result = k8s.create_deployment(
            name=name,
            image=image,
            namespace=namespace,
            replicas=replicas,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            mem_request=mem_request,
            mem_limit=mem_limit,
            env_vars=env_vars,
            ports=ports,
            command=command,
            ingress=ingress,
        )
        return render_template("status.html", result=result)

    except Exception as exc:
        logger.error(f"Deployment failed: {exc}")
        flash(f"Deployment failed: {exc}", "error")
        return redirect(url_for("app_k8s.index"))


@k8s_bp.route("/deployments")
@require_login
def deployments():
    """List all user workloads: container deployments and Helm releases merged."""
    namespace = session["namespace"]
    errors = []
    workloads = []

    # ── Container deployments (K8s Deployments) ───────────────────────
    try:
        k8s = _get_k8s()
        for dep in k8s.list_deployments(namespace=namespace):
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
        logger.error(f"Could not list container deployments: {exc}")

    # ── Helm releases ─────────────────────────────────────────────────
    try:
        for rel in helm_list(namespace=namespace):
            workloads.append(
                {
                    "kind": "helm",
                    "name": rel["name"],
                    "namespace": rel["namespace"],
                    "detail": rel.get("chart", ""),
                    "status": rel.get("status", "unknown"),
                    "status_label": rel.get("status", ""),
                    "created": rel.get("updated", ""),
                    "service_ports": None,
                    "ingress_url": None,
                    "app_version": rel.get("app_version", ""),
                }
            )
    except Exception as exc:
        errors.append(f"Helm releases: {exc}")
        logger.error(f"Could not list Helm releases: {exc}")

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
        k8s = _get_k8s()
        result = k8s.delete_deployment(name=name, namespace=namespace)
        if result["deployment"] and result["deployment"]["success"]:
            flash(f"Deployment '{name}' deleted successfully.", "success")
        else:
            error = (
                result["deployment"]["error"]
                if result["deployment"]
                else "Unknown error"
            )
            flash(f"Failed to delete deployment: {error}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))


@k8s_bp.route("/deployments/<namespace>/<name>/status")
@require_login
def deployment_status(namespace, name):
    """Get deployment status as JSON (for AJAX refresh)."""
    try:
        k8s = _get_k8s()
        status = k8s.get_deployment_status(name=name, namespace=namespace)
        return json.dumps(status), 200, {"Content-Type": "application/json"}
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
        k8s = _get_k8s()
        spec = k8s.get_deployment_spec(name=name, namespace=namespace)
        if "error" in spec:
            flash(f"Could not read deployment spec: {spec['error']}", "error")
            return redirect(url_for("app_k8s.deployments"))

        save_config(namespace=namespace, kind="container", config=spec)
        flash(
            f"Configuration for '{name}' saved. Load it from the Deploy page.",
            "success",
        )
    except Exception as exc:
        logger.error(f"Save deployment failed: {exc}")
        flash(f"Failed to save configuration: {exc}", "error")

    return redirect(url_for("app_k8s.deployments"))
