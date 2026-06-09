"""
api — REST API layer for HPC Pilot.

Exposes all manager operations as JSON endpoints under the ``/api`` prefix,
suitable for use with cURL or any HTTP client.

Authentication: pass the EGI Check-in access token as a Bearer token in the
``Authorization`` header::

    curl -H "Authorization: Bearer <token>" http://localhost:5000/api/deployments

Blueprints
----------
k8s_bp    /api/deployments  — container deployments & namespaces
helm_bp   /api/helm         — Helm chart operations
hpc_bp    /api/hpc          — HPC node operations
"""
