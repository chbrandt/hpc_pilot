"""
app/helm.py — Web GUI routes for InterLink Helm chart operations.

All backend operations are performed via the REST API (app.api_client),
so this module has no direct dependency on lib/.

The InterLink deploy/list/uninstall UI has been merged into the "Manage
Nodes" page (see app/hpc.py, GET /nodes). The routes below are kept only
as redirects for backward compatibility with old bookmarks/links.

Routes
------
GET  /releases                  Redirect to /nodes
GET  /helm                      Redirect to /nodes
"""

import logging

from flask import Blueprint, redirect, url_for

from app.auth import require_login

logger = logging.getLogger(__name__)

helm_bp = Blueprint("app_helm", __name__)


# ── Routes ────────────────────────────────────────────────────────────


@helm_bp.route("/releases")
@require_login
def releases():
    """Deprecated standalone releases page — redirects to the merged page."""
    return redirect(url_for("app_hpc.manage_nodes"))


@helm_bp.route("/helm")
@require_login
def helm_deploy():
    """Deprecated standalone deploy form — redirects to the merged page."""
    return redirect(url_for("app_hpc.manage_nodes"))
