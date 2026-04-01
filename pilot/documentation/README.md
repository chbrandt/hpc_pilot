# HPC Pilot — Web Application

HPC Pilot is a minimalist Flask web application for deploying containerised
workloads to a Kubernetes cluster **from outside the cluster**. Authentication
is provided by [EGI Check-in](https://www.egi.eu/service/check-in/) access
tokens (JWT, JWKS-validated). Every authenticated user gets their own isolated
Kubernetes namespace.

---

## Features

| Feature | Description |
|---|---|
| **Single-container deploy** | Form-driven `kubectl`-style deployment: image, replicas, resources, env vars, ports, ingress |
| **Helm chart deploy** | Deploy any Helm chart (OCI, repo, tarball URL) with custom values override |
| **Unified workloads view** | Single page listing both container deployments and Helm releases, with type badges |
| **EGI Check-in auth** | JWKS-validated JWT tokens; user namespace derived deterministically from `sub` claim |
| **User isolation** | Each user can only see and delete workloads in their own namespace |
| **Token countdown** | Navbar expiry timer; auto-logout on expiry; one-click token refresh |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.9 | Tested with 3.11 |
| `helm` CLI (v3) | Must be on `$PATH`; used for chart operations |
| `kubectl` access | Via `KUBECONFIG` or `~/.kube/config` |
| EGI Check-in account | For production; dev mode can be adapted to skip token validation |

---

## Quick Start

### 1. Install Python dependencies

```bash
cd webapp/
pip install -r requirements.txt
```

### 2. Configure access to the cluster

```bash
export KUBECONFIG=/path/to/your/kubeconfig
```

Or place your kubeconfig at `~/.kube/config` (the default).

### 3. Apply the RBAC role (first time only)

The app needs permission to create/list/delete Deployments, Services, Ingresses,
and Namespaces in user namespaces:

```bash
kubectl apply -f manager_role.yaml
```

### 4. Set a secret key

```bash
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

### 5. Run

```bash
python app.py
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
webapp/
├── app.py               # Flask application, all routes
├── k8s_client.py        # Kubernetes API wrapper (deployments, services, ingress)
├── helm_client.py       # Helm CLI wrapper (install, list, uninstall)
├── token_auth.py        # EGI Check-in JWT validation, namespace derivation
├── manager_role.yaml    # Kubernetes RBAC ClusterRole + binding
├── requirements.txt     # Python dependencies
├── static/
│   └── style.css        # All CSS
├── templates/
│   ├── base.html        # Navbar, flash messages, token countdown JS
│   ├── login.html       # Token paste form
│   ├── index.html       # Deploy Container form
│   ├── helm.html        # Deploy Chart form
│   ├── deployments.html # Unified workloads table (containers + helm)
│   ├── status.html      # Post-deploy status / polling page
│   ├── helm_result.html # Post-helm-install result page
│   ├── releases.html    # (legacy) Helm-only releases list
│   └── pods.html        # (legacy) Raw pods view
└── documentation/       # ← you are here
    ├── README.md
    ├── architecture.md
    ├── authentication.md
    ├── api.md
    ├── kubernetes.md
    ├── helm.md
    └── configuration.md
```

---

## Further Reading

- [Architecture](architecture.md) — component diagram and request lifecycle
- [Authentication](authentication.md) — EGI Check-in token flow
- [API Reference](api.md) — all Flask routes
- [Kubernetes Integration](kubernetes.md) — resources created, RBAC
- [Helm Integration](helm.md) — chart deployment details
- [Configuration](configuration.md) — all environment variables
