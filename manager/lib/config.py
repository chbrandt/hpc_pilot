"""
lib/config.py — Unified configuration loader for HPC Pilot Manager.

Merges what used to be three separate configuration files into a single
YAML document with three top-level sections:

    site:    operator-level site settings (was site_config.yaml)
    charts:  default Helm chart catalogue (was charts_config.yaml)
    hpc:     per-HPC-node connection details (was manager/hpc/<name>.yaml)
        nodes:
          <name>:
            hostname: ...
            ssh_port: ...
            plugin: ...

The config file path is resolved in this order:

    1. :func:`set_config_path` — set explicitly (used by the manager's
       ``--config`` CLI flag, see ``main.py``).
    2. ``PILOT_CONFIG_PATH`` environment variable.
    3. ``<manager_root>/pilot_config.yaml`` (default).

This module is the single source of truth for configuration; the thin
``api.site_config.load_site_config`` and ``lib.hpc_config``/
``lib.saved_deployments`` chart-loading helpers delegate to it so existing
call sites do not need to change.
"""

import logging
import os
import threading
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# manager/ root — one level above this lib/ directory
_MANAGER_DIR = os.path.dirname(os.path.dirname(__file__))
_DEFAULT_CONFIG_PATH = os.path.join(_MANAGER_DIR, "pilot_config.yaml")

_config_path_override: Optional[str] = None
_lock = threading.Lock()


def set_config_path(path: str) -> None:
    """
    Explicitly set the configuration file path, overriding the
    ``PILOT_CONFIG_PATH`` environment variable and the default location.

    Used by the ``--config`` CLI flag (see ``main.py``); call this before
    the application starts handling requests.
    """
    global _config_path_override
    with _lock:
        _config_path_override = path


def get_config_path() -> str:
    """Resolve the active configuration file path."""
    if _config_path_override:
        return _config_path_override
    return os.environ.get("PILOT_CONFIG_PATH", _DEFAULT_CONFIG_PATH)


def load_config() -> dict:
    """
    Parse the unified configuration file and return its contents as a dict.

    Returns an empty dict if the file is missing or cannot be parsed, so
    callers can always fall back to their own defaults.
    """
    path = get_config_path()
    if not os.path.exists(path):
        logger.warning("Configuration file not found at %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not parse configuration at %s: %s", path, exc)
        return {}


def get_site_config() -> dict:
    """Return the ``site`` section of the unified config (or ``{}``)."""
    data = load_config().get("site")
    return data if isinstance(data, dict) else {}


def get_charts_config() -> dict:
    """Return the ``charts`` section of the unified config (or ``{}``)."""
    data = load_config().get("charts")
    return data if isinstance(data, dict) else {}


def get_hpc_nodes_config() -> dict:
    """
    Return the ``hpc.nodes`` mapping of the unified config (or ``{}``).

    Keys are HPC node names; values are dicts with ``hostname``,
    ``ssh_port`` and ``plugin``.
    """
    hpc = load_config().get("hpc")
    if not isinstance(hpc, dict):
        return {}
    nodes = hpc.get("nodes")
    return nodes if isinstance(nodes, dict) else {}
