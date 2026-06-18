"""
api/saved.py — REST endpoints for saved deployment configuration management.

All routes are JSON-only and protected by Bearer-token auth.

Endpoints
---------
POST /api/saved/seed
    Idempotently seed the default Helm chart configs (from ``charts_config.yaml``)
    and the default HPC node configs (from ``hpc_config.yaml``) into the
    authenticated user's saved-config store.  Safe to call on every login —
    entries already present (by stable ID) are left untouched.
"""

import json
import logging

from flask import Blueprint

from api.auth import get_request_claims, require_token
from api.site_config import load_site_config
from lib.saved_deployments import seed_defaults, seed_hpc_defaults

logger = logging.getLogger(__name__)

saved_bp = Blueprint("api_saved", __name__, url_prefix="/api")


def _ok(data: dict | list, code: int = 200):
    return json.dumps(data), code, {"Content-Type": "application/json"}


def _err(message: str, code: int = 400):
    return json.dumps({"error": message}), code, {"Content-Type": "application/json"}


@saved_bp.route("/saved/seed", methods=["POST"])
@require_token
def seed_saved_defaults():
    """
    Idempotently seed the default configs for the authenticated user.

    Seeds both Helm chart defaults (from ``charts_config.yaml``) and HPC node
    defaults (from ``hpc_config.yaml``) in a single call.

    Reads the default lists and the site-level settings from
    ``site_config.yaml``, then inserts any missing default entries into the
    user's saved-config store.

    Already-seeded entries (identified by their stable IDs) are never
    duplicated.
    """
    claims = get_request_claims()
    namespace = claims["namespace"]
    try:
        site_cfg = load_site_config()
        seed_defaults(namespace, site_cfg)
        seed_hpc_defaults(namespace, site_cfg)
        return _ok({"seeded": True, "namespace": namespace})
    except Exception as exc:
        logger.error("seed_saved_defaults failed for %s: %s", namespace, exc)
        return _err(str(exc), 500)
