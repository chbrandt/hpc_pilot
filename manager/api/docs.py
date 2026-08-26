"""
api.docs — serves the OpenAPI spec file.

Exposes the raw YAML spec at ``/api/openapi.yaml`` so that Swagger UI
(mounted at ``/api/docs``) can load it.

The ``servers`` entry in ``openapi.yaml`` is intentionally a *relative* URL
(``/``) so that Swagger UI's "Try it out" requests resolve against the same
origin that served the spec — working equally for local development
(``http://localhost:5000``) and for a cloud deployment behind an Ingress
(``https://<fqdn>``).  Do not change it to an absolute ``http://localhost``
URL, or "Try it out" would target the user's *browser* host instead of the
server.
"""

import os

from flask import Blueprint, send_file

docs_bp = Blueprint("api_docs", __name__)


@docs_bp.route("/api/openapi.yaml")
def openapi_spec():
    """Return the OpenAPI 3.1 spec as YAML."""
    spec_path = os.path.join(os.path.dirname(__file__), "openapi.yaml")
    return send_file(spec_path, mimetype="application/yaml")
