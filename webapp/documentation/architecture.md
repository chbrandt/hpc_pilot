# Architecture

## Overview

HPC Pilot is a three-tier web application that sits **outside** the Kubernetes
cluster it manages. The Flask server is the only process; it talks to the
cluster via the official `kubernetes` Python client (for container workloads)
and via the `helm` CLI subprocess (for chart workloads).

```
┌─────────────────────────────────────────────────────────────────┐
│                        User's Browser                           │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTP (port 5000)
┌────────────────────────────▼────────────────────────────────────┐
│                    Flask Application (app.py)                   │
│                                                                 │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────────────┐     │
│  │ token_auth  │   │  k8s_client  │   │   helm_client     │     │
│  │  .py        │   │  .py         │   │   .py             │     │
│  │             │   │              │   │                   │     │
│  │ JWT/JWKS    │   │ kubernetes   │   │ subprocess →      │     │
│  │ validation  │   │ Python SDK   │   │ helm CLI          │     │
│  └──────┬──────┘   └──────┬───────┘   └────────┬──────────┘     │
│         │                 │                    │                │
└─────────┼─────────────────┼────────────────────┼─────────────-──┘
          │                 │                    │
          │ HTTPS           │ K8s API            │ K8s API
          │                 │ (kubeconfig)       │ (kubeconfig via helm)
          ▼                 ▼                    ▼
   ┌─────────────-┐  ┌────────────────────────────────┐
   │ EGI Check-in │  │     Kubernetes Cluster         │
   │ JWKS endpoint│  │                                │
   └─────────────-┘  │  user-<hash> namespace         │
                     │  ├── Deployments (containers)  │
                     │  ├── Services (NodePort)       │
                     │  ├── Ingresses                 │
                     │  └── Helm-managed resources    │
                     └────────────────────────────────┘
```

---

## Module Responsibilities

### `app.py`
The Flask application entrypoint. Contains all route handlers and orchestrates
the other modules. Imports are deferred inside route functions to keep startup
fast and to allow reloading without restart.

Sections:
- **Helpers** — `get_k8s_client()`, `validate_k8s_name()`
- **Context processor** — injects `current_user` into all templates
- **Auth routes** — `/login`, `/logout`
- **Container routes** — `/`, `/deploy`, `/deployments`, `/deployments/<ns>/<name>/delete`, `/deployments/<ns>/<name>/status`
- **Helm routes** — `/helm`, `/helm/install`, `/releases`, `/releases/<name>/delete`

### `k8s_client.py`
Wraps the official `kubernetes` Python SDK. All Kubernetes API calls for
container workloads go through this module. Creates and deletes:
`V1Deployment`, `V1Service` (NodePort), `V1Ingress`.

### `helm_client.py`
Wraps the `helm` CLI via `subprocess.run()`. Three public functions:
`helm_install`, `helm_list`, `helm_uninstall`. All output is returned as plain
dicts to keep `app.py` free of subprocess concerns.

### `token_auth.py`
Handles EGI Check-in JWT authentication:
- Downloads and caches JWKS keys per issuer (1-hour TTL)
- Validates token signature, expiry, and trusted issuer
- Derives a deterministic, valid Kubernetes namespace name from the `sub` claim
- Provides `get_session_user()` and `@require_token` decorator

---

## Request Lifecycle — Container Deployment

```
Browser                Flask (app.py)         k8s_client.py        Kubernetes API
   │                        │                        │                    │
   │  POST /deploy          │                        │                    │
   │───────────────────────►│                        │                    │
   │                        │ validate session        │                    │
   │                        │ parse form fields       │                    │
   │                        │ validate k8s name       │                    │
   │                        │──────────────────────► │                    │
   │                        │  namespace_exists?      │ GET namespace      │
   │                        │                        │───────────────────►│
   │                        │                        │◄───────────────────│
   │                        │  create_deployment()    │                    │
   │                        │──────────────────────► │ POST Deployment    │
   │                        │                        │───────────────────►│
   │                        │                        │ POST Service       │
   │                        │                        │───────────────────►│
   │                        │                        │ POST Ingress (opt) │
   │                        │                        │───────────────────►│
   │                        │◄──────────────────────│                    │
   │  render status.html     │                        │                    │
   │◄───────────────────────│                        │                    │
```

---

## Request Lifecycle — Helm Install

```
Browser                Flask (app.py)         helm_client.py      helm CLI        K8s API
   │                        │                        │               │               │
   │  POST /helm/install     │                        │               │               │
   │───────────────────────►│                        │               │               │
   │                        │ validate session        │               │               │
   │                        │ parse form fields       │               │               │
   │                        │──────────────────────► │               │               │
   │                        │  helm_install(...)      │ helm install  │               │
   │                        │                        │──────────────►│               │
   │                        │                        │               │ apply resources│
   │                        │                        │               │──────────────►│
   │                        │                        │  (blocks for  │               │
   │                        │                        │   up to 5min) │               │
   │                        │◄──────────────────────│               │               │
   │  render helm_result.html│                        │               │               │
   │◄───────────────────────│                        │               │               │
```

---

## Template Map

```
base.html  (navbar, flash messages, token countdown JS)
  ├── login.html       → POST /login
  ├── index.html       → POST /deploy         → status.html
  ├── helm.html        → POST /helm/install   → helm_result.html
  └── deployments.html → (unified workloads view)
                          POST /deployments/<ns>/<name>/delete
                          POST /releases/<name>/delete
```

---

## Session Data

The Flask session (encrypted cookie) stores:

| Key | Type | Description |
|---|---|---|
| `token` | `str` | Raw JWT access token |
| `claims` | `dict` | Decoded JWT claims (`sub`, `exp`, `iss`, etc.) |
| `namespace` | `str` | User's Kubernetes namespace (derived from `sub`) |

---

## Security Notes

- The app never stores credentials server-side; state lives in the client
  cookie (encrypted with `FLASK_SECRET_KEY`)
- Namespace isolation is enforced server-side: delete routes check that the
  requested namespace matches `session["namespace"]`
- JWKS keys are fetched over HTTPS and cached; key rotation is handled by
  retrying on a cache miss
- All Helm operations are scoped to the user's namespace via `--namespace`
