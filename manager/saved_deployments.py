"""
saved_deployments.py — Per-user saved deployment configuration store.

Configs are persisted as a JSON file per namespace under the ``data/``
directory that sits alongside this module.  No database dependency.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Directory where JSON store files live; created on first write.
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


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
