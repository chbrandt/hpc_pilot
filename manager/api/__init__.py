"""
api — REST API layer for HPC Pilot.

Exposes all manager operations as JSON endpoints under the ``/api`` prefix,
suitable for use with cURL or any HTTP client.

Authentication: pass the EGI Check-in access token as a Bearer token in the
``Authorization`` header::

    curl -H "Authorization: Bearer <token>" http://localhost:5000/api/jobs

Blueprints
----------
k8s_bp    /api  — namespaces, InterLink nodes, jobs
helm_bp   /api  — InterLink chart (one release per user/HPC-node pair)
hpc_bp    /api/hpc — HPC node operations (wstunnel + supervisord)
saved_bp  /api  — saved configuration seeding
docs_bp   /api  — OpenAPI spec + Swagger UI (``/api/docs``)
"""
