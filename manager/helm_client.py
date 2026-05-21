"""
helm_client.py — Thin wrapper around the Helm CLI for HPC Pilot.

All functions call the `helm` binary via subprocess and return plain dicts so
the Flask app never needs to import subprocess directly.
"""

import json
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Default timeout for 'helm install --wait'
_INSTALL_TIMEOUT = "5m0s"
_SUBPROCESS_TIMEOUT = 360  # seconds — slightly longer than Helm's own timeout


def helm_install(
    release_name: str,
    chart: str,
    namespace: str,
    values_yaml: Optional[str] = None,
    version: Optional[str] = None,
    timeout: str = _INSTALL_TIMEOUT,
) -> dict:
    """
    Install a Helm chart and wait until it is ready.

    Parameters
    ----------
    release_name : str
        Kubernetes-valid release name.
    chart : str
        Any chart reference accepted by `helm install`, e.g.:
        ``bitnami/nginx``, ``oci://registry-1.docker.io/bitnamicharts/nginx``,
        ``https://...`` (tarball URL).
    namespace : str
        Target namespace (must already exist).
    values_yaml : str, optional
        Raw YAML string passed to ``--values -`` (stdin).
    version : str, optional
        Pin a specific chart version via ``--version``.
    timeout : str
        Helm --timeout value (default ``5m0s``).

    Returns
    -------
    dict
        ``{success: bool, output: str, error: str | None}``
    """
    cmd = [
        "helm",
        "install",
        release_name,
        chart,
        "--namespace",
        namespace,
        "--wait",
        f"--timeout={timeout}",
    ]

    if version:
        cmd += ["--version", version]

    input_data: Optional[bytes] = None
    if values_yaml and values_yaml.strip():
        cmd += ["--values", "-"]
        input_data = values_yaml.encode()

    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        stdout = result.stdout.decode(errors="replace")
        stderr = result.stderr.decode(errors="replace")
        success = result.returncode == 0
        return {
            "success": success,
            "output": stdout,
            "error": stderr if not success else None,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": f"Helm install timed out after {timeout}.",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": (
                "helm CLI not found. Please install Helm 3 and make sure it is "
                "on the PATH."
            ),
        }


def helm_list(namespace: str) -> list[dict]:
    """
    Return a list of Helm releases in *namespace*.

    Each entry is a dict with keys: ``name``, ``namespace``, ``revision``,
    ``updated``, ``status``, ``chart``, ``app_version``.

    Raises
    ------
    RuntimeError
        If ``helm list`` exits with a non-zero return code.
    """
    cmd = ["helm", "list", "--namespace", namespace, "--output", "json"]
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError(
            "helm CLI not found. Please install Helm 3 and make sure it is on the PATH."
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))

    raw = result.stdout.decode(errors="replace").strip()
    releases = json.loads(raw) if raw else []

    # Normalise field names (helm uses camelCase in some versions)
    normalised = []
    for r in releases:
        normalised.append(
            {
                "name": r.get("name", ""),
                "namespace": r.get("namespace", namespace),
                "revision": r.get("revision", ""),
                "updated": r.get("updated", ""),
                "status": r.get("status", ""),
                "chart": r.get("chart", ""),
                "app_version": r.get("app_version", r.get("app_version", "")),
            }
        )
    return normalised


def helm_get_values(release_name: str, namespace: str) -> dict:
    """
    Retrieve the user-supplied values for an installed Helm release.

    Runs ``helm get values <release> --namespace <ns> --output yaml`` and
    returns the raw YAML string so it can be saved and later passed back
    to ``helm install --values -``.

    Returns
    -------
    dict
        ``{success: bool, values_yaml: str | None, error: str | None}``
    """
    cmd = [
        "helm",
        "get",
        "values",
        release_name,
        "--namespace",
        namespace,
        "--output",
        "yaml",
    ]
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        stdout = result.stdout.decode(errors="replace").strip()
        stderr = result.stderr.decode(errors="replace")
        if result.returncode != 0:
            return {"success": False, "values_yaml": None, "error": stderr}
        # helm returns "null\n" when no user-supplied values exist
        values_yaml = None if stdout in ("null", "") else stdout
        return {"success": True, "values_yaml": values_yaml, "error": None}
    except FileNotFoundError:
        return {
            "success": False,
            "values_yaml": None,
            "error": "helm CLI not found.",
        }


def helm_uninstall(release_name: str, namespace: str) -> dict:
    """
    Uninstall a Helm release.

    Returns
    -------
    dict
        ``{success: bool, output: str, error: str | None}``
    """
    cmd = ["helm", "uninstall", release_name, "--namespace", namespace]
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        stdout = result.stdout.decode(errors="replace")
        stderr = result.stderr.decode(errors="replace")
        success = result.returncode == 0
        return {
            "success": success,
            "output": stdout,
            "error": stderr if not success else None,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": "helm CLI not found.",
        }
