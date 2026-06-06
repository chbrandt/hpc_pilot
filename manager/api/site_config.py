"""
api/site_config.py — Operator-level site configuration loader.

Reads ``site_config.yaml`` from the manager root directory and exposes helpers
used by API endpoints that need site-level settings.

The ``lib/`` layer is intentionally kept unaware of this file; site config is
always supplied explicitly to any ``lib`` function that needs it.
"""

import logging
import os

import yaml

logger = logging.getLogger(__name__)

# manager/ root — two levels above this api/ directory
_MANAGER_DIR = os.path.dirname(os.path.dirname(__file__))

# Allow the operator to point at an arbitrary config file via SITE_CONFIG.
# Falls back to manager/site_config.yaml when the variable is not set.
_SITE_CONFIG_PATH = os.environ.get(
    "SITE_CONFIG",
    os.path.join(_MANAGER_DIR, "site_config.yaml"),
)

# ── OIDC defaults (also the hardcoded fallbacks) ──────────────────────────────

_OIDC_DEFAULTS = {
    "issuer": "https://aai.egi.eu/auth/realms/egi",
    "client_id": "oidc-agent",
    "scope": "openid offline_access profile email",
    "redirect_uri": "http://localhost:5000/api/auth/checkin/callback",
}


def load_site_config() -> dict:
    """
    Parse ``site_config.yaml`` and return its contents as a dict.

    Currently defined top-level keys:

    * ``cluster_domain`` (str) — wildcard base domain of the Kubernetes cluster
      (e.g. ``"dev.local"``).  Each user's InterLink deployment will be
      reachable at ``<namespace>.<cluster_domain>``.
    * ``oidc`` (dict) — OIDC / OAuth 2.0 settings; see :func:`get_oidc_config`.

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


def get_oidc_config() -> dict:
    """
    Return the resolved OIDC configuration for EGI Check-in.

    Values are resolved with the following priority (highest first):

    1. **Environment variables** — ``CHECKIN_ISSUER``, ``CHECKIN_CLIENT_ID``,
       ``CHECKIN_SCOPE``, ``CHECKIN_REDIRECT_URI``.
    2. **site_config.yaml** — the ``oidc`` mapping inside the file.
    3. **Hardcoded defaults** — production EGI Check-in with the
       ``oidc-agent`` public client.

    Returns
    -------
    dict
        A dict with keys ``issuer``, ``client_id``, ``scope``,
        ``redirect_uri`` — all guaranteed to be present and non-empty strings.

    Example::

        cfg = get_oidc_config()
        # cfg["issuer"] → "https://aai.egi.eu/auth/realms/egi"
        # cfg["client_id"] → "oidc-agent"
    """
    site = load_site_config().get("oidc") or {}

    # Merge: defaults ← site_config ← env vars
    resolved = {**_OIDC_DEFAULTS, **{k: v for k, v in site.items() if v}}

    env_overrides = {
        "issuer": os.environ.get("CHECKIN_ISSUER", "").strip(),
        "client_id": os.environ.get("CHECKIN_CLIENT_ID", "").strip(),
        "scope": os.environ.get("CHECKIN_SCOPE", "").strip(),
        "redirect_uri": os.environ.get("CHECKIN_REDIRECT_URI", "").strip(),
    }
    for key, val in env_overrides.items():
        if val:
            resolved[key] = val

    return resolved
