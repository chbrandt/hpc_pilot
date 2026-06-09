# HPC Pilot — Web Application

HPC Pilot is a Flask web application for deploying containerised workloads
and HPC jobs to a Kubernetes cluster **from outside the cluster**. It manages
[interLink](https://interlink-project.dev) pods per user and authenticates
via [EGI Check-in](https://www.egi.eu/service/check-in/) access tokens.
Every authenticated user gets their own isolated Kubernetes namespace.

---

## Features

| Feature | Description |
|---|---|
| **Container deployment** | Form-driven deployment: image, replicas, resources, env vars, ports, ingress |
| **Helm chart deploy** | Deploy any Helm chart (OCI, repo, tarball URL) with custom values override |
| **HPC job management** | Submit and monitor HPC jobs via interLink plugin |
| **Unified workloads view** | Single page listing container deployments, Helm releases, and HPC jobs |
| **EGI Check-in auth** | Token-based authentication via EGI Check-in; user namespace derived deterministically from `sub` claim |
| **User isolation** | Each user can only see and delete workloads in their own namespace |
| **Token expiry handling** | Navbar expiry countdown; auto-logout on expiry; one-click token refresh |

---

## Architecture

HPC Pilot is built in three layers under `manager/`:

```
manager/
├── lib/       # Pure Python business logic (importable from CLI)
├── api/       # JSON REST API under /api   (cURL / HTTP clients)
├── app/       # HTML web GUI under /       (browser)
├── main.py    # Flask application entry point (Wires lib+api+app into a single WSGI app)
└── site_config.yaml  # Operator-level site settings
└── charts_config.yaml # Default chart catalogue seeded per user
```

See [Architecture](architecture.md) for the full component diagram and request lifecycle.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.9 | Tested with 3.11 |
| `helm` CLI (v3) | Must be on `$PATH`; used for chart operations |
| `kubectl` access | Via `KUBECONFIG` or `~/.kube/config` |
| EGI Check-in access token | An access token obtained from EGI Check-in (e.g. via `oidc-agent`) |
| `site_config.yaml` | Operator-level settings (cluster domain) |
| `charts_config.yaml` | Default charts seeded to each user on first login |

---

## Quick Start

### 1. Install Python dependencies

```bash
cd manager/
pip install -r requirements.txt
```

### 2. Configure the site

Edit `manager/site_config.yaml` to set your cluster domain:

```yaml
# manager/site_config.yaml
cluster_domain: your-cluster.example.com
```

### 3. Configure default charts (optional)

Edit `manager/charts_config.yaml` to define which Helm charts are pre-seeded
into every user's saved-deployments store on first login. The file includes
placeholder tokens (`__NAMESPACE__`, `__CLUSTER_DOMAIN__`, `__NAMESPACE_HASH__`)
that are resolved per-user.

### 4. Configure access to the cluster

```bash
export KUBECONFIG=/path/to/your/kubeconfig
```

Or place your kubeconfig at `~/.kube/config` (the default). The app does **not**
run inside the cluster — it uses an external kubeconfig for all Kubernetes API
operations.

### 5. Set up RBAC (first time only)

The app needs permission to create/list/delete Deployments, Services, Ingresses,
Namespaces, and Helm releases in user namespaces. See the
[RBAC Setup section in Configuration](configuration.md#rbac-setup) for the
`ClusterRole` and `ClusterRoleBinding` definitions.

### 6. Set a secret key

```bash
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

### 7. Run

```bash
python main.py
```

The app listens on `http://0.0.0.0:5000` by default.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig file |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | Session encryption key — **change in production** |
| `FLASK_PORT` | `5000` | TCP port to listen on |
| `FLASK_DEBUG` | `0` | Set to `1` to enable Flask debug mode |

---

## File Structure

```
manager/
├── main.py               # Flask application entry point (Wires lib+api+app into a single WSGI app)
├── site_config.yaml       # Operator-level site settings (cluster domain)
├── charts_config.yaml     # Default chart catalogue seeded per user on first login
├── requirements.txt       # Python dependencies
├── api/                   # JSON REST API under /api (blueprints)
│   ├── auth.py            # OIDC token validation and session management
│   ├── k8s.py             # Container deployment REST API
│   ├── helm.py            # Helm chart REST API
│   ├── hpc.py             # HPC job submission REST API
│   ├── saved.py           # Saved configurations REST API
│   ├── docs.py            # Swagger / OpenAPI spec endpoints
│   ├── site_config.py     # Site configuration loader
│   └── openapi.yaml       # OpenAPI specification
├── app/                   # HTML web GUI under / (blueprints + templates)
│   ├── auth.py            # Auth route handlers
│   ├── k8s.py             # Container deployment route handlers
│   ├── helm.py            # Helm chart route handlers
│   ├── hpc.py             # HPC job route handlers
│   ├── saved.py           # Saved configurations route handlers
│   ├── api_client.py      # Internal HTTP client for calling REST API layer
│   ├── static/
│   │   └── style.css      # All CSS
│   └── templates/
│       ├── base.html      # Navbar, flash messages, token countdown JS
│       ├── login.html     # Log-in page
│       ├── index.html     # Deploy Container form
│       ├── helm.html      # Deploy Chart form
│       ├── hpc.html       # HPC job submission
│       ├── deployments.html # Unified workloads table
│       ├── status.html    # Post-deploy status / polling page
│       ├── helm_result.html # Post-helm-install result page
│       ├── hpc_result.html  # HPC job result page
│       └── container.html   # Container detail view
├── lib/                   # Pure Python business logic (importable from CLI)
│   ├── k8s_client.py      # Kubernetes API wrapper
│   ├── helm_client.py     # Helm CLI wrapper
│   ├── hpc_client.py      # HPC job client
│   ├── token_auth.py      # EGI Check-in JWT/JWKS validation, namespace derivation
│   ├── token_checkin.py   # EGI Check-in device flow and token helpers
│   └── saved_deployments.py # Saved deployment configurations
├── hpc/                   # HPC edge-node configuration and setup scripts
│   └── pilot/
│       ├── setup.sh
│       └── supervisord.conf.jinja
├── k8s/                   # Kubernetes-side configuration
│   └── pilot/
│       └── wstunnel.conf
└── data/                  # Runtime data (user saved configs, etc.)
```

---

## Further Reading

- [Architecture](architecture.md) — component diagram and request lifecycle
- [Authentication](authentication.md) — EGI Check-in OIDC token flow
- [API Reference](api.md) — all Flask routes
- [REST API Reference](rest_api.md) — REST API endpoints
- [Python Library](lib.md) — pure-Python package `lib`
- [Kubernetes Integration](kubernetes.md) — resources created, RBAC
- [Helm Integration](helm.md) — chart deployment details
- [Configuration](configuration.md) — all environment variables, kubeconfig, RBAC, production setup
