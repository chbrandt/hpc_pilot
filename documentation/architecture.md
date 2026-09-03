# Architecture

## Overview

HPC Pilot is a three-layer web application. A single Flask process (the
**manager**) runs the whole stack; the layers communicate as follows:

- `lib/` — pure-Python business logic (Kubernetes SDK, `helm` CLI subprocess,
  `mccli`/SSH). No Flask dependency.
- `api/` — JSON REST API under `/api`, protected by Bearer-token auth. Calls
  `lib/` directly.
- `app/` — HTML web GUI under `/`. A thin client that talks to the `api/`
  layer over HTTP via `app/api_client.py` (loopback by default; split
  deployment via `API_BASE_URL`).

The manager can run **outside** the cluster (local dev / VM, external
kubeconfig) or **inside** the cluster as a Deployment managed by the
[`charts/manager/`](../charts/manager/) Helm chart (in-cluster ServiceAccount
token). In both cases it speaks to the Kubernetes API and the `helm` CLI.

```
┌─────────────────────────────────────────────────────────────────┐
│                        User's Browser                           │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTP (port 5000)
┌────────────────────────────▼────────────────────────────────────┐
│              Flask Application (manager/main.py)                │
│                                                                 │
│  ┌─────────────────┐   HTTP    ┌──────────────────┐             │
│  │   app/ (GUI)    │──────────►│   api/ (REST)    │             │
│  │  HTML + templates│  loopback│  /api/* JSON     │             │
│  │  via api_client │           │                  │             │
│  └─────────────────┘           └────────┬─────────┘             │
│                                         │                       │
│                                         │ calls                 │
│                                         ▼                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                        lib/                               │   │
│  │  ┌────────────┐  ┌─────────────┐  ┌──────────────────┐   │   │
│  │  │ k8s_client │  │ helm_client │  │   hpc_client     │   │   │
│  │  │ K8s SDK    │  │ helm CLI    │  │  mccli / SSH     │   │   │
│  │  └─────┬──────┘  └──────┬──────┘  └────────┬─────────┘   │   │
│  └────────┼─────────────────┼───────────────────┼─────────────┘   │
└───────────┼─────────────────┼───────────────────┼─────────────────┘
            │                 │                   │
            │ K8s API          │ K8s API           │ SSH (mccli)
            │ (kubeconfig      │ (kubeconfig       │ + EGI token
            │  or in-cluster)  │  via helm)        │
            ▼                  ▼                   ▼
   ┌──────────────────┐  ┌─────────────────┐  ┌────────────────┐
   │  EGI Check-in    │  │ Kubernetes      │  │  HPC edge-node │
   │  JWKS / UserInfo │  │ user-<hash> ns  │  │  wstunnel+plugin│
   └──────────────────┘  │  + Helm releases│  └────────────────┘
                         └─────────────────┘
```

---

## Module Responsibilities

### `main.py`
Flask application factory (`create_app`). Registers all blueprints (from
`app/` and `api/`), mounts the Swagger UI at `/api/docs`, and injects
`current_user` into every template via a context processor.

### `lib/k8s_client.py`
Wraps the official `kubernetes` Python SDK. Manages namespaces and job
(`Deployment`) objects targeting InterLink virtual-kubelet nodes. See
[kubernetes.md](kubernetes.md).

### `lib/helm_client.py`
Wraps the `helm` CLI via `subprocess.run()`. Functions: `helm_install`,
`helm_list`, `helm_get_values`, `helm_uninstall`. See [helm.md](helm.md).

### `lib/hpc_client.py`
Wraps `mccli` (motley-cue SSH client) to install and manage the wstunnel client
+ supervisord + InterLink plugin on a remote HPC node. See [lib.md](lib.md).

### `lib/hpc_config.py`
Loads per-node HPC config files from `manager/hpc/<name>.yaml`.

### `lib/token_auth.py`
EGI Check-in JWT/JWKS validation, namespace derivation, UserInfo-based group
access checks. See [authentication.md](authentication.md).

### `lib/saved_deployments.py`
Per-user saved-configuration store (JSON files under `manager/data/`), plus
the default-chart seeding logic driven by `charts_config.yaml`.

### `api/` layer
Thin JSON wrappers over `lib/`. Each module is a Flask blueprint with the
`/api` prefix. Auth is enforced by `require_token` (`api/auth.py`).

### `app/` layer
HTML routes. Each module is a Flask blueprint. Backend calls go through
`app/api_client.py` (`api_get`/`api_post`/`api_delete`) so the GUI has no
direct `lib/` dependency and could run as a separate process.

---

## Request Lifecycle — Job Submission

```
Browser            app/ (k8s_bp)        api_client          api/ (k8s_bp)        lib/k8s_client      K8s API
   │  POST /submit    │                    │                   │                    │                  │
   │─────────────────►│ validate form       │                   │                    │                  │
   │                  │ api_post /api/jobs  │                   │                    │                  │
   │                  │───────────────────► │ POST /api/jobs/preset│                    │                  │
   │                  │                    │──────────────────►│ require_token      │                  │
   │                  │                    │                   │ derive namespace   │                  │
   │                  │                    │                   │ create_job(...)   │                  │
   │                  │                    │                   │──────────────────► │ POST Deployment  │
   │                  │                    │                   │                    │ (nodeSelector +  │
   │                  │                    │                   │                    │  toleration)     │
   │                  │                    │                   │◄────────────────── │ 201              │
   │                  │                    │◄──────────────────│                    │                  │
   │ render status    │◄────────────────── │                   │                    │                  │
   │◄─────────────────│                    │                   │                    │                  │
   │  (status.html polls GET /jobs/<name>/status until "succeeded"/"failed")            │                  │
```

The submitted `Deployment` is pinned to an InterLink virtual-kubelet node;
InterLink then forwards the pod to an HPC batch job on the connected site
(once the HPC edge-node side is wired up via wstunnel).

---

## Request Lifecycle — InterLink Install

```
Browser            app/ (helm_bp)       api_client          api/ (helm_bp)       lib/helm_client     helm CLI      K8s API
   │  POST /helm/install │                 │                   │                    │                  │             │
   │────────────────────►│ api_post        │                   │                    │                  │             │
   │                     │ /api/interlink  │                   │                    │                  │             │
   │                     │────────────────►│ POST /api/interlink│                   │                  │             │
   │                     │                 │──────────────────►│ require_token      │                  │             │
   │                     │                 │                   │ resolve placeholders│                 │             │
   │                     │                 │                   │ helm_install(...)  │                  │             │
   │                     │                 │                   │──────────────────►│ helm install     │             │
   │                     │                 │                   │                    │ --wait (≤5min)   │ apply        │
   │                     │                 │                   │                    │─────────────────►│────────────►│
   │                     │                 │                   │◄──────────────────│                  │             │
   │ render helm_result  │◄────────────────│◄─────────────────│                    │                  │             │
   │◄────────────────────│                 │                   │                    │                  │             │
```

`POST /api/interlink` reads the InterLink chart reference, version and default
values from `charts_config.yaml`, resolves `__NAMESPACE__`/`__HOSTNAME__`, and
runs `helm install interlink` into the user's namespace.

---


## Request Lifecycle — HPC Deploy

```
Browser      app/ (hpc_bp)    api_client      api/ (hpc_bp)         lib/hpc_client        mccli/SSH         HPC node
   │ POST /hpc/deploy │            │                │                     │                   │                │
   │─────────────────►│ api_post   │                │                     │                   │                │
   │                  │ /api/hpc/deploy             │                     │                   │                │
   │                  │───────────►│ POST /api/hpc/deploy                │                   │                │
   │                  │            │────────────────►│ require_token      │                   │                │
   │                  │            │                │ resolve hpc_name →  │                   │                │
   │                  │            │                │ load_hpc_config     │                   │                │
   │                  │            │                │ compute wstunnel    │                   │                │
   │                  │            │                │ hpc_client.deploy() │                   │                │
   │                  │            │                │────────────────────►│ step functions    │                │
   │                  │            │                │                     │ (mkdir, venv,     │                │
   │                  │            │                │                     │  pip supervisor,  │                │
   │                  │            │                │                     │  wstunnel dl,     │                │
   │                  │            │                │                     │  plugin install,  │                │
   │                  │            │                │                     │  start supervisord)               │
   │                  │            │                │                     │──────────────────►│──────────────►│
   │                  │            │                │◄────────────────────│                   │                │
   │ render hpc_result│◄───────────│◄────────────────│                   │                   │                │
   │◄─────────────────│            │                │                     │                   │                │
```

The wstunnel client connects back to the in-cluster InterLink wstunnel server
using path-prefix routing on the shared `site_config.hostname`
(`--http-upgrade-path-prefix <namespace>`).

---

## Template Map

```
base.html  (navbar, flash messages, token-countdown JS)
  ├── login.html        → POST /login
  ├── index.html        → POST /submit             → status.html
  ├── nodes.html         → POST /hpc/{deploy,status,start,stop}     → hpc_result.html
  │                       POST /hpc/nodes/interlink/{deploy,delete}
  ├── deployments.html  → (unified workloads view: jobs + interlink)
  │                       GET  /jobs/<ns>/<name>/output → output.html
  │                       POST /jobs/<ns>/<name>/delete
  │                       POST /hpc/nodes/interlink/delete
  └── output.html       → (job stdout/stderr)     → GET /jobs/<ns>/<name>/output
```

> `pods.html` and `container.html` are legacy templates kept in the tree but
> not referenced by any current route.

---

## Session Data

The Flask session (encrypted client-side cookie, signed with
`FLASK_SECRET_KEY`) stores:

| Key | Type | Description |
|---|---|---|
| `token` | `str` | Raw JWT access token (forwarded as Bearer token to the REST API) |
| `claims` | `dict` | Decoded JWT claims (`sub`, `exp`, `iss`, …) |
| `namespace` | `str` | User's Kubernetes namespace (derived from `sub`) |

---

## Security Notes

- The app never stores credentials server-side; state lives in the encrypted
  client cookie.
- Namespace isolation is enforced server-side: GUI delete/save routes check that
  the requested namespace matches `session["namespace"]`; the REST API derives
  the namespace from the token and never trusts a client-supplied namespace.
- JWKS keys are fetched over HTTPS and cached (1-hour TTL); key rotation is
  handled by retrying on a cache miss.
- All Helm operations are scoped to the user's namespace via `--namespace`.
- Optional `allowed_groups` in `site_config.yaml` restricts access to members
  of specific EGI VOs (verified via the UserInfo endpoint — see
  [authentication.md](authentication.md)).

