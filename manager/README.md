# HPC Pilot — Manager App

The Flask application that powers HPC Pilot: it submits HPC jobs (container
workloads forwarded to HPC batch systems via [interLink](https://interlink-project.dev)),
deploys per-user InterLink pods, manages the HPC edge-node wstunnel client,
and authenticates via [EGI Check-in](https://www.egi.eu/service/check-in/).

It can run **outside** the cluster (local dev / VM, external kubeconfig) or
**inside** the cluster as a Deployment managed by the
[`charts/manager/`](../charts/manager/) Helm chart.

> For the full documentation see [`../documentation/README.md`](../documentation/README.md).

---

## Three-layer architecture

```
manager/
├── lib/       # Pure Python business logic (importable from CLI)
├── api/       # JSON REST API under /api   (cURL / HTTP clients)
├── app/       # HTML web GUI under /       (browser)
├── main.py    # Flask application entry point (wires lib+api+app)
├── site_config.yaml  # Operator-level site settings
└── charts_config.yaml # Default chart catalogue seeded per user
```

- `lib/` — Kubernetes SDK, `helm` CLI, `mccli`/SSH, JWT validation. No Flask.
- `api/` — thin JSON wrappers over `lib/`, protected by Bearer-token auth.
- `app/` — HTML routes; a thin client that calls the `api/` layer over HTTP
  via `app/api_client.py` (loopback by default; split with `API_BASE_URL`).

See [`../documentation/architecture.md`](../documentation/architecture.md).

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure the site (single hostname — no wildcard DNS needed)
#    edit site_config.yaml: hostname, wstunnel.port, wstunnel.local_port

# 3. Set kubeconfig (optional — defaults to ~/.kube/config)
export KUBECONFIG=/path/to/your/kubeconfig

# 4. Set a secret key
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"

# 5. Run
python main.py
# → http://localhost:5000  (Swagger UI at /api/docs)
```

---

## Configuration

| Source | Purpose |
|---|---|
| `site_config.yaml` | `hostname`, `wstunnel.port`, `wstunnel.local_port`, `allowed_groups` |
| `charts_config.yaml` | Default InterLink chart preset seeded per user |
| `hpc/<name>.yaml` | One file per HPC node (`hostname`, `ssh_port`, `plugin`) |

| Environment variable | Default | Description |
|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig (or in-cluster ServiceAccount) |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | Session cookie key — **change in production** |
| `FLASK_PORT` | `5000` | TCP port |
| `FLASK_DEBUG` | `0` | `1` for debug mode |
| `API_BASE_URL` | `http://localhost:5000` | REST API base URL as seen from the GUI layer |

See [`../documentation/configuration.md`](../documentation/configuration.md).

---

## Further reading

- [Deployment Guide](../documentation/deployment.md)
- [REST API Reference](../documentation/rest_api.md) (also at `/api/docs`)
- [Web UI Routes](../documentation/api.md)
- [Python Library](../documentation/lib.md)
- [Authentication](../documentation/authentication.md)
