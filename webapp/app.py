"""
HPC Pilot - Kubernetes Deployment Web Application

A Flask web app for deploying workloads to a Kubernetes cluster.
Authenticated via EGI Check-in access tokens (JWT, JWKS signature validated).
Each user gets a personal, isolated Kubernetes namespace.

Usage:
    export KUBECONFIG=/path/to/kubeconfig   # optional, defaults to ~/.kube/config
    python app.py
"""

import json
import logging
import os
import re

from flask import Flask, flash, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────


def get_k8s_client():
    """Create and return a K8sClient instance."""
    from k8s_client import K8sClient

    kubeconfig = os.environ.get("KUBECONFIG")
    return K8sClient(kubeconfig_path=kubeconfig)


def validate_k8s_name(name: str) -> bool:
    """Validate a Kubernetes resource name (RFC 1123 subdomain)."""
    pattern = r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$"
    return bool(re.match(pattern, name))


# ── Context processor — injects current_user into every template ──────


@app.context_processor
def inject_user():
    """Make current_user available in all templates automatically."""
    from token_auth import get_session_user

    return {"current_user": get_session_user()}


# ── Auth routes ───────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page: accepts and validates an EGI Check-in access token."""
    from token_auth import derive_namespace, validate_token

    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if not token:
            flash("Please paste your EGI Check-in access token.", "error")
            return redirect(url_for("login"))

        # Validate the token (JWKS signature + expiry + trusted issuer)
        try:
            claims = validate_token(token)
        except ValueError as exc:
            flash(f"Token validation failed: {exc}", "error")
            return redirect(url_for("login"))

        # Derive the user's personal namespace from the sub claim
        sub = claims["sub"]
        namespace = derive_namespace(sub)

        # Store validated credentials in the session
        session.clear()
        session["token"] = token
        session["claims"] = claims
        session["namespace"] = namespace

        # Auto-create the user's namespace if it doesn't exist yet
        try:
            k8s = get_k8s_client()
            if not k8s.namespace_exists(namespace):
                result = k8s.create_namespace(namespace)
                if result["success"]:
                    logger.info(
                        f"Auto-created namespace '{namespace}' for {sub[:20]}..."
                    )
                else:
                    logger.warning(
                        f"Could not auto-create namespace '{namespace}': {result.get('error')}"
                    )
        except Exception as exc:
            # Non-fatal: namespace may be created on first deploy
            logger.warning(f"Namespace pre-creation skipped: {exc}")

        flash(f"Welcome! Your namespace is {namespace}.", "success")
        next_url = request.form.get("next") or url_for("index")
        return redirect(next_url)

    # GET — render the login form
    reason = request.args.get("reason")  # "expired"
    refresh = request.args.get("refresh")  # "1"
    next_url = request.args.get("next", "")
    return render_template(
        "login.html", reason=reason, refresh=refresh, next_url=next_url
    )


@app.route("/logout")
def logout():
    """Clear the session and redirect to the login page."""
    reason = request.args.get("reason")
    session.clear()
    if reason == "expired":
        flash(
            "Your session has expired. Please paste a new token to continue.", "error"
        )
    else:
        flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ── Main app routes ───────────────────────────────────────────────────


@app.route("/")
def index():
    """Main page with deployment form."""
    from token_auth import require_token, get_session_user

    user = get_session_user()
    if user is None:
        return redirect(url_for("login"))

    error = None
    try:
        # Light connectivity check
        get_k8s_client()
    except Exception as exc:
        error = f"Cannot connect to Kubernetes cluster: {exc}"
        logger.error(error)

    return render_template("index.html", error=error)


@app.route("/deploy", methods=["POST"])
def deploy():
    """Handle deployment form submission."""
    from token_auth import get_session_user

    user = get_session_user()
    if user is None:
        flash("Please log in first.", "error")
        return redirect(url_for("login"))

    # The namespace is always the user's personal namespace — not from the form
    namespace = session["namespace"]

    # Extract form data
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
        return redirect(url_for("index"))

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
                return redirect(url_for("index"))
        except ValueError:
            flash(f"Port '{num_str}' is not a valid number.", "error")
            return redirect(url_for("index"))
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
        return redirect(url_for("index"))
    if not validate_k8s_name(name):
        flash(
            "Invalid deployment name. Must be lowercase alphanumeric and hyphens, "
            "start/end with alphanumeric, max 63 characters.",
            "error",
        )
        return redirect(url_for("index"))
    if not image:
        flash("Container image is required.", "error")
        return redirect(url_for("index"))

    try:
        k8s = get_k8s_client()

        # Ensure user's namespace exists (may have been missed at login)
        if not k8s.namespace_exists(namespace):
            ns_result = k8s.create_namespace(namespace)
            if not ns_result["success"]:
                flash(f"Failed to prepare namespace: {ns_result['error']}", "error")
                return redirect(url_for("index"))

        # Create the deployment
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
        return redirect(url_for("index"))


@app.route("/deployments")
def deployments():
    """List the user's deployed workloads."""
    from token_auth import get_session_user

    user = get_session_user()
    if user is None:
        return redirect(url_for("login"))

    namespace = session["namespace"]
    error = None
    deployment_list = []

    try:
        k8s = get_k8s_client()
        deployment_list = k8s.list_deployments(namespace=namespace)
    except Exception as exc:
        error = f"Cannot connect to Kubernetes cluster: {exc}"
        logger.error(error)

    return render_template(
        "deployments.html",
        deployments=deployment_list,
        namespace=namespace,
        error=error,
    )


@app.route("/deployments/<namespace>/<name>/delete", methods=["POST"])
def delete_deployment(namespace, name):
    """Delete a deployment and its associated service."""
    from token_auth import get_session_user

    user = get_session_user()
    if user is None:
        return redirect(url_for("login"))

    # Security: users can only delete from their own namespace
    if namespace != session["namespace"]:
        flash("You can only delete deployments in your own namespace.", "error")
        return redirect(url_for("deployments"))

    try:
        k8s = get_k8s_client()
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

    return redirect(url_for("deployments"))


@app.route("/deployments/<namespace>/<name>/status")
def deployment_status(namespace, name):
    """Get deployment status as JSON (for AJAX refresh)."""
    from token_auth import get_session_user

    user = get_session_user()
    if user is None:
        return (
            json.dumps({"error": "Not authenticated"}),
            401,
            {"Content-Type": "application/json"},
        )

    try:
        k8s = get_k8s_client()
        status = k8s.get_deployment_status(name=name, namespace=namespace)
        return json.dumps(status), 200, {"Content-Type": "application/json"}
    except Exception as exc:
        return (
            json.dumps({"error": str(exc)}),
            500,
            {"Content-Type": "application/json"},
        )


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=debug)
