"""
HPC Pilot - Kubernetes Pod Deployer Web Application

A minimalist Flask web app for deploying pods to a Kubernetes cluster.
The app runs outside the cluster and connects via kubeconfig.

Usage:
    export KUBECONFIG=/path/to/kubeconfig   # optional, defaults to ~/.kube/config
    python app.py
"""

import json
import logging
import os
import re

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_k8s_client():
    """Create and return a K8sClient instance."""
    from k8s_client import K8sClient

    kubeconfig = os.environ.get("KUBECONFIG")
    return K8sClient(kubeconfig_path=kubeconfig)


def validate_k8s_name(name: str) -> bool:
    """Validate a Kubernetes resource name (RFC 1123 subdomain)."""
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


# ── Routes ────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Main page with pod deployment form."""
    error = None
    namespaces = ["default"]

    try:
        k8s = get_k8s_client()
        namespaces = k8s.list_namespaces()
    except Exception as e:
        error = f"Cannot connect to Kubernetes cluster: {e}"
        logger.error(error)

    return render_template("index.html", namespaces=namespaces, error=error)


@app.route("/deploy", methods=["POST"])
def deploy():
    """Handle pod deployment form submission."""
    # Extract form data
    pod_name = request.form.get("pod_name", "").strip()
    image = request.form.get("image", "").strip()
    namespace = request.form.get("namespace", "default").strip()
    new_namespace = request.form.get("new_namespace", "").strip()
    cpu_request = request.form.get("cpu_request", "").strip() or None
    cpu_limit = request.form.get("cpu_limit", "").strip() or None
    mem_request = request.form.get("mem_request", "").strip() or None
    mem_limit = request.form.get("mem_limit", "").strip() or None
    port = request.form.get("port", "").strip()
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

    # Parse port
    port_int = None
    if port:
        try:
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                flash("Port must be between 1 and 65535.", "error")
                return redirect(url_for("index"))
        except ValueError:
            flash("Port must be a valid number.", "error")
            return redirect(url_for("index"))

    # Determine effective namespace
    if namespace == "__new__" and new_namespace:
        namespace = new_namespace
    elif namespace == "__new__" and not new_namespace:
        flash("Please enter a name for the new namespace.", "error")
        return redirect(url_for("index"))

    # Validate required fields
    if not pod_name:
        flash("Pod name is required.", "error")
        return redirect(url_for("index"))
    if not validate_k8s_name(pod_name):
        flash(
            "Invalid pod name. Must be lowercase alphanumeric and hyphens, "
            "start/end with alphanumeric, max 63 characters.",
            "error",
        )
        return redirect(url_for("index"))
    if not image:
        flash("Container image is required.", "error")
        return redirect(url_for("index"))
    if not validate_k8s_name(namespace):
        flash("Invalid namespace name.", "error")
        return redirect(url_for("index"))

    try:
        k8s = get_k8s_client()

        # Create namespace if it doesn't exist
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                flash(f"Failed to create namespace: {ns_result['error']}", "error")
                return redirect(url_for("index"))

        # Create the pod
        result = k8s.create_pod(
            name=pod_name,
            image=image,
            namespace=namespace,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            mem_request=mem_request,
            mem_limit=mem_limit,
            env_vars=env_vars,
            port=port_int,
            command=command,
        )

        return render_template("status.html", result=result)

    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        flash(f"Deployment failed: {e}", "error")
        return redirect(url_for("index"))


@app.route("/pods")
def pods():
    """List deployed pods."""
    namespace = request.args.get("namespace", "__all__")
    error = None
    pod_list = []
    namespaces = ["default"]

    try:
        k8s = get_k8s_client()
        namespaces = k8s.list_namespaces()
        pod_list = k8s.list_pods(namespace=namespace)
    except Exception as e:
        error = f"Cannot connect to Kubernetes cluster: {e}"
        logger.error(error)

    return render_template(
        "pods.html",
        pods=pod_list,
        namespaces=namespaces,
        selected_namespace=namespace,
        error=error,
    )


@app.route("/pods/<namespace>/<name>/delete", methods=["POST"])
def delete_pod(namespace, name):
    """Delete a pod and its associated service."""
    try:
        k8s = get_k8s_client()
        result = k8s.delete_pod(name=name, namespace=namespace)
        if result["pod"] and result["pod"]["success"]:
            flash(f"Pod '{name}' deleted successfully.", "success")
        else:
            error = result["pod"]["error"] if result["pod"] else "Unknown error"
            flash(f"Failed to delete pod: {error}", "error")
    except Exception as e:
        flash(f"Error: {e}", "error")

    return redirect(url_for("pods"))


@app.route("/pods/<namespace>/<name>/status")
def pod_status(namespace, name):
    """Get pod status as JSON (for AJAX refresh)."""
    try:
        k8s = get_k8s_client()
        status = k8s.get_pod_status(name=name, namespace=namespace)
        return json.dumps(status), 200, {"Content-Type": "application/json"}
    except Exception as e:
        return json.dumps({"error": str(e)}), 500, {"Content-Type": "application/json"}


# ── API endpoint for namespaces (used by the form's JS) ──────────────


@app.route("/api/namespaces")
def api_namespaces():
    """Return list of namespaces as JSON."""
    try:
        k8s = get_k8s_client()
        namespaces = k8s.list_namespaces()
        return json.dumps(namespaces), 200, {"Content-Type": "application/json"}
    except Exception as e:
        return json.dumps({"error": str(e)}), 500, {"Content-Type": "application/json"}


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=debug)
