"""
app — Web GUI layer for HPC Pilot.

Renders Jinja2 HTML templates for interactive use in a web browser.

Authentication uses an EGI Check-in token entered via a login form; the
validated token and user metadata are stored in the Flask session.

Blueprints
----------
auth_bp    /login, /logout      — session management
k8s_bp     /, /deploy, /deployments, /deployments/…
helm_bp    /helm, /releases     — deprecated redirects to /nodes
hpc_bp     /nodes, /hpc/…       — "Manage Nodes" page (HPC + InterLink)
saved_bp   /saved/…             — saved configuration management
"""
