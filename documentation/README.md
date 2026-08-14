# HPC Pilot — Web Application

HPC Pilot is a Flask web application for deploying **HPC jobs** (container
workloads forwarded to HPC batch systems via
[interLink](https://interlink-project.dev)) and managing per-user InterLink
pods in a Kubernetes cluster. It authenticates via
[EGI Check-in](https://www.egi.eu/service/check-in/) access tokens, and every
authenticated user gets their own isolated Kubernetes namespace.

The manager can run **outside** the cluster (local dev, VM) or **inside** the
cluster as a Kubernetes Deployment managed by the
[`charts/manager/`](../charts/manager/) Helm chart.

---

## Features

| Feature | Description |
|---|---|
| **Job submission** | Form-driven submission of container jobs pinned to InterLink virtual-kubelet nodes (InterLink forwards them to HPC batch jobs) |
| **InterLink deployment** | Deploy the singleton InterLink chart (wstunnel server + virtual-kubelet) per user, with per-user placeholder resolution from `charts_config.yaml` |
| **HPC node management** | Install/start/stop/status/uninstall the wstunnel client + supervisord + InterLink plugin on a remote HPC edge-node via `mccli` |
| **Unified workloads view** | Single page listing container jobs and the InterLink Helm release |
| **EGI Check-in auth** | Token-based authentication; user namespace derived deterministically from the `sub` claim |
| **User isolation** | Each user can only see and delete workloads in their own namespace |
| **Token expiry handling** | Navbar expiry countdown; auto-logout on expiry; one-click token refresh |
| **Saved configs** | Per-user store of reusable job / release templates, seeded with defaults on first login |

---

## Architecture

HPC Pilot is built in three layers under `manager/`:

```
manager/
├── lib/       # Pure Python business logic (importable from CLI)
├── api/       # JSON REST API under /api   (cURL / HTTP clients)
├── app/       # HTML web GUI under /       (browser)
├── main.py    # Flask application entry point (wires lib+api+app into a single WSGI app)
├── site_config.yaml  # Operator-level site settings
└── charts_config.yaml # Default chart catalogue seeded per user
```

See [Architecture](architecture.md) for the full component diagram and request
lifecycle.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.9 | Tested with 3.11 / 3.12 |
| `helm` CLI (v3) | Must be on `$PATH`; used for chart operations |
| `kubectl` access | Via `KUBECONFIG` or `~/.kube/config` (or in-cluster ServiceAccount) |
| EGI Check-in access token | An access token obtained from EGI Check-in (e.g. via the `lib/token_checkin.py` helper) |
| `site_config.yaml` | Operator-level settings (hostname, wstunnel ports, allowed_groups) |
| `charts_config.yaml` | Default charts seeded to each user on first login |
| `mccli` + `flaat-userinfo` | **Only for HPC operations** — `mccli` wraps SSH with OIDC token auth; install with `pip install mccli` |

---

## Quick Start

### 1. Install Python dependencies

```bash
cd manager/
pip install -r requirements.txt
```

### 2. Configure the site

Edit `manager/site_config.yaml` to set the manager's public hostname and
wstunnel ports. No wildcard DNS or wildcard TLS certificate is required —
every user's wstunnel endpoint is reached through a path prefix
(`<hostname>/<user-namespace>`) on this single hostname, not a per-user
subdomain:

```yaml
# manager/site_config.yaml
hostname: your-cluster.example.com
wstunnel:
  port: 80
  local_port: 4000
```

### 3. Configure default charts (optional)

Edit `manager/charts_config.yaml` to define which Helm charts are pre-seeded
into every user's saved-deployments store on first login. The file includes
placeholder tokens (`__NAMESPACE__`, `__HOSTNAME__`) that are resolved
per-user.

### 4. Configure HPC nodes (optional)

Add one file per HPC site under `manager/hpc/<name>.yaml`
(`hostname`, `ssh_port`, `plugin`). See
[configuration.md](configuration.md#per-hpc-node-config-managerhpcnameyaml).

### 5. Configure access to the cluster

```bash
export KUBECONFIG=/path/to/your/kubeconfig
```

Or place your kubeconfig at `~/.kube/config` (the default). When running
in-cluster (via the Helm chart) the pod uses its ServiceAccount token instead.

### 6. Set a secret key

```bash
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

### 7. Run

```bash
python main.py
```

The app listens on `http://0.0.0.0:5000` by default. A browseable Swagger UI
for the REST API is served at **`/api/docs`**.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig file |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | Session encryption key — **change in production** |
| `FLASK_PORT` | `5000` | TCP port to listen on |
| `FLASK_DEBUG` | `0` | Set to `1` to enable Flask debug mode |
| `API_BASE_URL` | `http://localhost:5000` | Base URL of the REST API as seen from the GUI layer (set to split GUI/API processes) |

---


## File Structure

```
manager/
├── main.py               # Flask application entry point (wires lib+api+app)
├── site_config.yaml       # Operator-level site settings
├── charts_config.yaml     # Default chart catalogue seeded per user
├── requirements.txt       # Python dependencies
├── api/                   # JSON REST API under /api (blueprints)
│   ├── auth.py            # Bearer-token validation (require_token decorator)
│   ├── k8s.py             # Job REST API
│   ├── helm.py            # InterLink chart REST API
│   ├── hpc.py             # HPC node REST API
│   ├── saved.py           # Saved configs REST API
│   ├── docs.py            # Swagger / OpenAPI spec endpoint
│   ├── site_config.py     # Site configuration loader
│   └── openapi.yaml       # OpenAPI 3.1 specification
├── app/                   # HTML web GUI under / (blueprints + templates)
│   ├── auth.py            # Login/logout route handlers
│   ├── k8s.py             # Job route handlers
│   ├── helm.py            # InterLink chart route handlers
│   ├── hpc.py             # HPC node route handlers
│   ├── saved.py           # Saved config route handlers
│   ├── api_client.py      # Internal HTTP client for calling the REST API
│   ├── static/
│   │   └── style.css      # All CSS
│   └── templates/
│       ├── base.html      # Navbar, flash messages, token countdown JS
│       ├── login.html     # Log-in page
│       ├── index.html     # Submit Job form
│       ├── helm.html      # Deploy InterLink form
│       ├── hpc.html       # HPC node management
│       ├── deployments.html # Unified workloads table
│       ├── status.html    # Post-submit status / polling page
│       ├── helm_result.html # Post-helm-install result page
│       ├── hpc_result.html  # HPC action result page
│       └── releases.html    # InterLink release list
├── lib/                   # Pure Python business logic (importable from CLI)
│   ├── k8s_client.py      # Kubernetes API wrapper (jobs + namespaces)
│   ├── helm_client.py     # Helm CLI wrapper
│   ├── hpc_client.py      # mccli/SSH HPC deployment client
│   ├── hpc_config.py      # Per-HPC-node config loader
│   ├── token_auth.py      # EGI Check-in JWT/JWKS validation, namespace derivation
│   ├── token_checkin.py   # EGI Check-in device-flow CLI helper
│   └── saved_deployments.py # Saved deployment configurations + seeding
├── hpc/                   # HPC edge-node configuration and setup assets
│   ├── pilot/
│   │   ├── setup.sh                    # (deprecated) legacy setup script
│   │   ├── supervisord.conf.jinja     # supervisord config template
│   │   ├── supervisord-wstunnel.conf.jinja
│   │   └── supervisord-interlink.conf.jinja
│   ├── plugins/
│   │   ├── echo/InterLinkConfig.yaml
│   │   ├── docker/InterLinkConfig.yaml
│   │   └── slurm/InterLinkConfig.yaml
│   └── <name>.yaml        # One file per HPC node (hostname, ssh_port, plugin)
├── k8s/                   # Kubernetes-side configuration
│   └── pilot/wstunnel.conf
└── data/                  # Runtime data (per-user saved configs, JSON)
```

> `templates/pods.html` and `templates/container.html` are legacy templates
> kept in the tree but not referenced by any current route.

---

## Further Reading

- [**Deployment Guide**](deployment.md) — build the image, install the Helm chart, connect InterLink and the HPC edge-node end-to-end
- [Architecture](architecture.md) — component diagram and request lifecycle
- [Authentication](authentication.md) — EGI Check-in OIDC token flow
- [Web UI Routes](api.md) — all Flask GUI routes
- [REST API Reference](rest_api.md) — REST API endpoints (also at `/api/docs`)
- [Python Library](lib.md) — pure-Python package `lib`
- [Kubernetes Integration](kubernetes.md) — resources created, RBAC
- [Helm Integration](helm.md) — the InterLink chart and `helm_client`
- [Configuration](configuration.md) — all environment variables, kubeconfig, RBAC, production setup

