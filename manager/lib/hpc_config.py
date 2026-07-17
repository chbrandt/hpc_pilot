"""
hpc_config.py — Loader for per-HPC node configuration files.

Each HPC node is defined by a YAML file in ``manager/hpc/<name>.yaml``
with the following structure::

    hostname: 161.9.255.206
    ssh_port: 3333
    plugin: echo

The filename (without ``.yaml``) serves as the HPC node's unique identifier
(``hpc_name``) used by the API and web GUI.

This module provides helpers to list all available HPC nodes and to load
a single node's configuration by name.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# manager/ root — one level above this lib/ directory
_MANAGER_DIR = os.path.dirname(os.path.dirname(__file__))

# Directory containing per-HPC node YAML config files
_HPC_CONFIG_DIR = os.path.join(_MANAGER_DIR, "hpc")


def _hpc_config_path(name: str) -> str:
    """Return the filesystem path for the HPC config file with the given *name*."""
    return os.path.join(_HPC_CONFIG_DIR, f"{name}.yaml")


def list_hpc_nodes() -> list[dict]:
    """
    Scan the HPC config directory and return all available HPC node configs.

    Each YAML file in ``manager/hpc/`` is parsed and returned as a dict
    with the following keys:

    * ``name``      — the filename stem (e.g. ``"test-echo"``)
    * ``hostname``  — HPC login node hostname or IP
    * ``ssh_port``  — SSH port (int, default 22)
    * ``plugin``    — InterLink plugin name (e.g. ``"echo"``)

    Returns
    -------
    list[dict]
        Sorted alphabetically by ``name``.  Returns an empty list if the
        config directory does not exist or contains no valid YAML files.
    """
    config_dir = Path(_HPC_CONFIG_DIR)
    if not config_dir.is_dir():
        logger.warning("HPC config directory not found at %s", _HPC_CONFIG_DIR)
        return []

    nodes: list[dict] = []
    for yaml_file in sorted(config_dir.glob("*.yaml")):
        name = yaml_file.stem
        cfg = _parse_hpc_yaml(yaml_file, name)
        if cfg is not None:
            nodes.append(cfg)

    return nodes


def load_hpc_config(name: str) -> dict:
    """
    Load and return the configuration for the HPC node identified by *name*.

    Parameters
    ----------
    name : str
        The HPC node name (filename stem of ``manager/hpc/<name>.yaml``).

    Returns
    -------
    dict
        A dict with keys ``name``, ``hostname``, ``ssh_port``, and ``plugin``.

    Raises
    ------
    ValueError
        If the config file does not exist or is missing required fields.
    """
    path = _hpc_config_path(name)
    if not os.path.exists(path):
        raise ValueError(f"HPC config '{name}' not found at {path}")

    cfg = _parse_hpc_yaml(Path(path), name)
    if cfg is None:
        raise ValueError(f"HPC config '{name}' is invalid or incomplete.")

    return cfg


def _parse_hpc_yaml(path: Path, name: str) -> Optional[dict]:
    """
    Parse a single HPC YAML config file and return a normalised dict.

    Returns ``None`` if the file cannot be parsed or is missing the
    ``hostname`` field.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:
        logger.warning("Could not parse HPC config %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("HPC config %s is not a valid YAML mapping", path)
        return None

    hostname = data.get("hostname")
    if not hostname:
        logger.warning("HPC config %s is missing 'hostname'", path)
        return None

    return {
        "name": name,
        "hostname": str(hostname),
        "ssh_port": int(data.get("ssh_port", 22)),
        "plugin": str(data.get("plugin", "echo")),
    }
