"""
saved_deployments.py — Per-user saved deployment configuration store.

Configs are persisted as a JSON file per namespace under the ``data/``
directory that sits alongside this module.  No database dependency.

Global default chart presets are read from ``charts_config.yaml`` (next to
this module) and automatically seeded into each user's store on first login.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Directory where JSON store files live; created on first write.
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Path to the global chart defaults configuration file.
_CHARTS_CONFIG = os.path.join(os.path.dirname(__file__), "charts_config.yaml")

# Stable ID prefix used for auto-seeded default configs.
_DEFAULT_ID_PREFIX = "default-"


# ── Internal helpers ──────────────────────────────────────────────────


def _store_path(namespace: str) -> str:
    """Return the path to the JSON store file for *namespace*."""
    safe = namespace.replace("/", "_").replace("..", "_")
    return os.path.join(_DATA_DIR, f"{safe}.json")


def _load(namespace: str) -> list[dict]:
    """Load and return the list of saved configs for *namespace*."""
    path = _store_path(namespace)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read saved configs from %s: %s", path, exc)
        return []


def _dump(namespace: str, configs: list[dict]) -> None:
    """Persist *configs* for *namespace* to disk."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = _store_path(namespace)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(configs, fh, indent=2, default=str)
    except OSError as exc:
        logger.error("Could not write saved configs to %s: %s", path, exc)
        raise


# ── Public API ────────────────────────────────────────────────────────


def save_config(namespace: str, kind: str, config: dict) -> dict:
    """
    Persist a deployment configuration for *namespace*.

    Parameters
    ----------
    namespace : str
        The user's Kubernetes namespace (used as the store key).
    kind : str
        ``"container"`` or ``"helm"``.
    config : dict
        The full configuration dict to save.  Must contain at least
        ``"name"`` (container) or ``"release_name"`` (helm).

    Returns
    -------
    dict
        The saved entry (includes the generated ``id`` and ``saved_at``).
    """
    configs = _load(namespace)

    entry = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "saved_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        **config,
    }
    configs.append(entry)
    _dump(namespace, configs)
    logger.info(
        "Saved %s config '%s' for namespace %s",
        kind,
        entry.get("name") or entry.get("release_name"),
        namespace,
    )
    return entry


def list_configs(namespace: str, kind: Optional[str] = None) -> list[dict]:
    """
    Return saved configs for *namespace*, optionally filtered by *kind*.

    Parameters
    ----------
    namespace : str
    kind : str, optional
        If provided, only return entries where ``entry["kind"] == kind``.

    Returns
    -------
    list[dict]
        Configs sorted by ``saved_at`` descending (newest first).
    """
    configs = _load(namespace)
    if kind:
        configs = [c for c in configs if c.get("kind") == kind]
    return sorted(configs, key=lambda c: c.get("saved_at", ""), reverse=True)


def get_config(namespace: str, config_id: str) -> Optional[dict]:
    """Return the saved config with the given *config_id*, or ``None``."""
    for entry in _load(namespace):
        if entry.get("id") == config_id:
            return entry
    return None


# ── Default charts helpers ────────────────────────────────────────────


def load_default_charts() -> list[dict]:
    """
    Load and return the list of default chart configs from *charts_config.yaml*.

    Each entry is a plain dict with keys: ``kind``, ``release_name``, ``chart``,
    ``version``, ``singleton``, ``description``, ``values_yaml``.

    Returns an empty list if the file is missing or malformed.
    """
    if not os.path.exists(_CHARTS_CONFIG):
        logger.warning("charts_config.yaml not found at %s", _CHARTS_CONFIG)
        return []
    try:
        with open(_CHARTS_CONFIG, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        charts = data.get("default_charts", []) if isinstance(data, dict) else []
        return charts if isinstance(charts, list) else []
    except Exception as exc:
        logger.warning("Could not parse charts_config.yaml: %s", exc)
        return []


def seed_defaults(namespace: str) -> None:
    """
    Ensure every default chart from *charts_config.yaml* is present in the
    saved-configs store for *namespace*.

    Each default chart gets a stable, deterministic ID
    (``"default-<release_name>"``) so it is only inserted once regardless of
    how many times this function is called (e.g. on every login).
    """
    defaults = load_default_charts()
    if not defaults:
        return

    configs = _load(namespace)
    existing_ids = {c.get("id") for c in configs}

    added = []
    for chart in defaults:
        release_name = chart.get("release_name", "")
        if not release_name:
            continue
        stable_id = f"{_DEFAULT_ID_PREFIX}{release_name}"
        if stable_id in existing_ids:
            continue  # already seeded

        entry = {
            "id": stable_id,
            "kind": chart.get("kind", "helm"),
            "saved_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "is_default": True,
            "release_name": release_name,
            "chart": chart.get("chart", ""),
            "version": chart.get("version"),
            "singleton": chart.get("singleton", False),
            "description": chart.get("description", ""),
            "values_yaml": chart.get("values_yaml"),
        }
        configs.append(entry)
        added.append(release_name)

    if added:
        _dump(namespace, configs)
        logger.info(
            "Seeded default chart(s) %s for namespace %s", added, namespace
        )


def def_chart_is_singleton(chart_ref: str) -> bool:
    """
    Return ``True`` if *chart_ref* matches a singleton default chart.

    The comparison is exact on ``chart`` field (case-insensitive).
    """
    for chart in load_default_charts():
        if chart.get("singleton") and chart.get("chart", "").lower() == chart_ref.lower():
            return True
    return False


def delete_config(namespace: str, config_id: str) -> bool:
    """
    Remove the config with *config_id* from the store.

    Returns
    -------
    bool
        ``True`` if an entry was removed, ``False`` if it was not found.
    """
    configs = _load(namespace)
    new_configs = [c for c in configs if c.get("id") != config_id]
    if len(new_configs) == len(configs):
        return False  # not found
    _dump(namespace, new_configs)
    return True
