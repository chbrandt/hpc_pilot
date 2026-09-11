"""
api/site_config.py — Operator-level site configuration loader.

Reads the ``site`` section of the unified configuration file
(``manager/pilot_config.yaml`` by default, see ``lib.config``) and exposes a
single :func:`load_site_config` helper used by API endpoints that need
site-level settings (e.g. ``hostname`` for placeholder resolution in
Helm values).

The ``lib/`` layer's business logic is intentionally kept unaware of this
module; site config is always supplied explicitly to any ``lib`` function
that needs it (only the generic ``lib.config`` loader parses the file).
"""

import logging

from lib.config import get_site_config

logger = logging.getLogger(__name__)


def load_site_config() -> dict:
    """
    Return the ``site`` section of the unified configuration file.

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
    return get_site_config()

