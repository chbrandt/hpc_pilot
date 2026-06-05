"""
api/site_config.py — Operator-level site configuration loader.

Reads ``site_config.yaml`` from the manager root directory and exposes a
single :func:`load_site_config` helper used by API endpoints that need
site-level settings (e.g. ``cluster_domain`` for placeholder resolution in
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

    * ``cluster_domain`` (str) — wildcard base domain of the Kubernetes cluster
      (e.g. ``"dev.local"``).  Each user's InterLink deployment will be
      reachable at ``<namespace>.<cluster_domain>``.

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
