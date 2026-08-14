"""
api/site_config.py — Operator-level site configuration loader.

Reads ``site_config.yaml`` from the manager root directory and exposes a
single :func:`load_site_config` helper used by API endpoints that need
site-level settings (e.g. ``hostname`` for placeholder resolution in
Helm values).

The ``lib/`` layer is intentionally kept unaware of this file; site config is
always supplied explicitly to any ``lib`` function that needs it.
"""

import logging
import os

import yaml

logger = logging.getLogger(__name__)

# manager/ root — two levels above this api/ directory
_MANAGER_DIR = os.path.dirname(os.path.dirname(__file__))
_SITE_CONFIG_PATH = os.path.join(_MANAGER_DIR, "site_config.yaml")


def load_site_config() -> dict:
    """
    Parse ``site_config.yaml`` and return its contents as a dict.

    Currently defined keys:

    * ``hostname`` (str) — the single fixed hostname the manager and its
      per-user InterLink wstunnel endpoints are exposed on (e.g.
      ``"dev.local"``).  No wildcard DNS/TLS is required: each user's
      wstunnel is reachable at ``<hostname>/<namespace>`` (a path-prefixed
      route on the shared hostname), not a per-user subdomain.

    Returns
    -------
    dict
        The parsed site config.  Returns an empty dict if the file is missing
        or cannot be parsed, so callers can always fall back to their own
        defaults.
    """
    if not os.path.exists(_SITE_CONFIG_PATH):
        logger.warning("site_config.yaml not found at %s", _SITE_CONFIG_PATH)
        return {}
    try:
        with open(_SITE_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not parse site_config.yaml: %s", exc)
        return {}
