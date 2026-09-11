"""
HPC Pilot — Flask application entry point.

Wires the three-layer architecture together into a single WSGI app:

    lib/   — pure Python business logic (importable from CLI)
    api/   — JSON REST API under /api  (cURL / HTTP clients)
    app/   — HTML web GUI under /      (browser)

Usage
-----
    # Run the server
    export KUBECONFIG=/path/to/kubeconfig   # optional
    python main.py

    # With a custom configuration file (overrides PILOT_CONFIG_PATH and the
    # default manager/pilot_config.yaml):
    python main.py --config /path/to/pilot_config.yaml

    # Or with Flask's CLI (from the manager/ directory):
    flask --app main run

Environment variables
---------------------
KUBECONFIG        Path to kubeconfig file (default: ~/.kube/config)
FLASK_PORT        Port to listen on (default: 5000)
FLASK_DEBUG       Set to "1" to enable debug mode
FLASK_SECRET_KEY  Flask session secret (change in production!)
PILOT_CONFIG_PATH Path to the unified pilot_config.yaml (overridden by
                  --config; default: manager/pilot_config.yaml)
"""

import argparse
import logging
import os
import sys

from flask import Flask, jsonify

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def _apply_config_flag() -> None:
    """
    Parse ``--config <path>`` from ``sys.argv`` (if present) and apply it via
    ``lib.config.set_config_path`` before any request is handled.

    Uses ``parse_known_args`` so this is safe to call even when the process
    is launched by a WSGI server (e.g. gunicorn) that may pass its own
    unrelated arguments.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", dest="config", default=None)
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    if args.config:
        from lib.config import set_config_path

        set_config_path(args.config)
        logger.info("Using configuration file: %s", args.config)


_apply_config_flag()


def create_app() -> Flask:
    """
    Application factory.

    Creates and configures the Flask application, registers all blueprints,
    and sets up the template context processor.
    """
    # Templates and static files live in app/templates and app/static
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "app", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "app", "static"),
    )
    app.secret_key = os.environ.get(
        "FLASK_SECRET_KEY", "dev-secret-change-in-production"
    )

    # ── Layer 3: Web GUI blueprints ───────────────────────────────────
    from app.auth import auth_bp
    from app.k8s import k8s_bp
    from app.helm import helm_bp
    from app.hpc import hpc_bp as app_hpc_bp
    from app.saved import saved_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(k8s_bp)
    app.register_blueprint(helm_bp)
    app.register_blueprint(app_hpc_bp)
    app.register_blueprint(saved_bp)

    # ── Layer 2: REST API blueprints ──────────────────────────────────
    from api.k8s import k8s_bp as api_k8s_bp
    from api.helm import helm_bp as api_helm_bp
    from api.hpc import hpc_bp as api_hpc_bp
    from api.saved import saved_bp as api_saved_bp
    from api.docs import docs_bp

    app.register_blueprint(api_k8s_bp)
    app.register_blueprint(api_helm_bp)
    app.register_blueprint(api_hpc_bp)
    app.register_blueprint(api_saved_bp)
    app.register_blueprint(docs_bp)

    # ── Swagger UI ────────────────────────────────────────────────────
    from flask_swagger_ui import get_swaggerui_blueprint

    swaggerui_bp = get_swaggerui_blueprint(
        "/api/docs",          # Swagger UI will be served at this URL
        "/api/openapi.yaml",  # URL of the OpenAPI spec (served by docs_bp)
        config={"app_name": "HPC Pilot API"},
    )
    app.register_blueprint(swaggerui_bp, url_prefix="/api/docs")

    # ── Public health endpoint (liveness probe, no auth) ──────
    @app.route("/health", methods=["GET"])
    def health():
        """Liveness probe -- public, returns a simple JSON status."""
        return jsonify({"status": "Service alive"})

    # ── Context processor — injects current_user into every template ──
    @app.context_processor
    def inject_user():
        from app.auth import get_session_user
        return {"current_user": get_session_user()}

    return app


# ── Entry point ───────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=debug)
