"""
app/saved.py — Web GUI routes for saved deployment configuration management.

Routes
------
POST /saved/<config_id>/delete   Remove a saved configuration entry
"""

import logging

from flask import Blueprint, flash, redirect, session, url_for

from app.auth import require_login
from lib.saved_deployments import delete_config, get_config

logger = logging.getLogger(__name__)

saved_bp = Blueprint("app_saved", __name__)


@saved_bp.route("/saved/<config_id>/delete", methods=["POST"])
@require_login
def delete_saved_config(config_id):
    """Remove a saved deployment configuration."""
    namespace = session["namespace"]
    entry = get_config(namespace, config_id)
    if entry is None:
        flash("Saved configuration not found.", "error")
    else:
        delete_config(namespace, config_id)
        label = entry.get("name") or entry.get("release_name") or config_id
        flash(f"Saved configuration '{label}' removed.", "success")

    # Redirect back to the appropriate deploy form
    kind = entry.get("kind") if entry else None
    if kind == "helm":
        return redirect(url_for("app_helm.helm_page"))
    if kind == "hpc":
        return redirect(url_for("app_hpc.hpc_page"))
    return redirect(url_for("app_k8s.index"))
