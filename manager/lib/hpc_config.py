"""
hpc_config.py — Loader for per-HPC node configuration.

HPC node connection details now live under the ``hpc.nodes`` section of the
unified configuration file (``manager/pilot_config.yaml`` by default, see
``lib.config``)::

    hpc:
      nodes:
        test-echo:
          hostname: 161.9.255.206
          ssh_port: 3333
          plugin: echo

The mapping key (e.g. ``test-echo``) serves as the HPC node's unique
identifier (``hpc_name``) used by the API and web GUI.

This module provides helpers to list all available HPC nodes and to load
a single node's configuration by name.
"""

import logging
from typing import Optional

from lib.config import get_hpc_nodes_config

logger = logging.getLogger(__name__)


def list_hpc_nodes() -> list[dict]:
    """
    Return all HPC node configs defined under ``hpc.nodes`` in the unified
    configuration file.

    Each entry is a dict with the following keys:

    * ``name``      — the mapping key (e.g. ``"test-echo"``)
    * ``hostname``  — HPC login node hostname or IP
    * ``ssh_port``  — SSH port (int, default 22)
    * ``plugin``    — InterLink plugin name (e.g. ``"echo"``)

    Returns
    -------
    list[dict]
        Sorted alphabetically by ``name``.  Returns an empty list if the
        config file is missing, contains no ``hpc.nodes`` section, or none
        of the entries are valid.
    """
    nodes_cfg = get_hpc_nodes_config()
    if not nodes_cfg:
        logger.warning("No HPC nodes configured under 'hpc.nodes'")
        return []

    nodes: list[dict] = []
    for name in sorted(nodes_cfg):
        cfg = _normalise_node(nodes_cfg[name], name)
        if cfg is not None:
            nodes.append(cfg)

    return nodes


def load_hpc_config(name: str) -> dict:
    """
    Load and return the configuration for the HPC node identified by *name*.

    Parameters
    ----------
    name : str
        The HPC node name (key under ``hpc.nodes`` in the unified config).

    Returns
    -------
    dict
        A dict with keys ``name``, ``hostname``, ``ssh_port``, and ``plugin``.

    Raises
    ------
    ValueError
        If the node is not defined or is missing required fields.
    """
    nodes_cfg = get_hpc_nodes_config()
    raw = nodes_cfg.get(name)
    if raw is None:
        raise ValueError(f"HPC config '{name}' not found in configuration.")

    cfg = _normalise_node(raw, name)
    if cfg is None:
        raise ValueError(f"HPC config '{name}' is invalid or incomplete.")

    return cfg


def _normalise_node(data: dict, name: str) -> Optional[dict]:
    """
    Normalise a single HPC node config mapping.

    Returns ``None`` if *data* is not a mapping or is missing ``hostname``.
    """
    if not isinstance(data, dict):
        logger.warning("HPC config '%s' is not a valid mapping", name)
        return None

    hostname = data.get("hostname")
    if not hostname:
        logger.warning("HPC config '%s' is missing 'hostname'", name)
        return None

    return {
        "name": name,
        "hostname": str(hostname),
        "ssh_port": int(data.get("ssh_port", 22)),
        "plugin": str(data.get("plugin", "echo")),
    }

