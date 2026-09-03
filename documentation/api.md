# Web UI Routes

The **web GUI layer** (`manager/app/`) renders HTML pages in the browser. It is a
thin client over the [REST API](rest_api.md): every backend operation goes
through `app/api_client.py`, which forwards the session's Bearer token to the
`/api` endpoints over HTTP (loopback by default).

All routes (except `/login` and `/logout`) are protected by the
`require_login` decorator. Unauthenticated HTML requests are redirected to
`/login`; AJAX/JSON requests get HTTP 401 with a JSON body.

```{contents} Contents
:local:
:depth: 2
```

---

## Authentication Routes (`app/auth.py`)

### `GET /login`

Render the login page (token paste form).

**Query parameters:**

| Parameter | Description |
|---|---|
| `reason=expired` | Show "session expired" warning banner |
| `refresh=1` | Show "update your token" banner instead of the normal title |
| `next=<url>` | URL to redirect to after successful login |

**Response:** `login.html`

---

### `POST /login`

Validate the submitted EGI Check-in access token and start a session.

On success the manager also:

1. Derives the user's namespace from the `sub` claim.
2. Calls `POST /api/userspace/` to create it (if missing).
3. Calls `POST /api/saved/seed` to seed the default chart presets.

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `token` | ✓ | Raw EGI Check-in JWT access token |
| `next` | | URL to redirect to after login |

**Success:** Redirect to `next` (or `/`)
**Failure:** Flash error message, redirect to `GET /login`

---

### `GET /logout`

Clear the session and redirect to the login page.

**Query parameters:**

| Parameter | Description |
|---|---|
| `reason=expired` | Show "token expired" flash message |

**Response:** Redirect to `/login`

---

## Job Routes (`app/k8s.py`)

### `GET /`

Render the "Submit a Job" form.

The form's InterLink-node dropdown is populated by calling
`GET /api/nodes/interlink`. Saved container configs for the user are listed for
one-click reuse.

**Auth:** Required
**Response:** `index.html`

---

### `POST /submit`

Submit a new job (forwards to `POST /api/jobs/preset`).

**Auth:** Required

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `name` | ✓ | Job name (RFC 1123 label, max 63 chars) |
| `image` | ✓ | Container image (e.g. `ubuntu:22.04`) |
| `node_name` | ✓ | InterLink virtual-kubelet node name (from the dropdown) |
| `command` | | Shell command override; runs as `/bin/sh -c "<command>"` |
| `env_key[]` | | Environment variable key (repeatable) |
| `env_value[]` | | Environment variable value (repeatable, paired with `env_key`) |

**Success:** Render `status.html` with the job result
**Failure:** Flash error, redirect to `GET /`

---

### `GET /jobs`

List all workloads in the user's namespace: container **jobs** (from
`GET /api/jobs`) and the InterLink Helm release(s) — one per configured HPC
node, from `GET /api/interlink?hpc_name=<name>` — merged into a single
unified table.

**Auth:** Required
**Response:** `deployments.html` with `workloads` list

Each workload entry:

```python
{
    "kind":         "container" | "helm",
    "name":         str,
    "namespace":    str,
    "detail":       str,   # image (container) or chart name (helm)
    "node_name":    str,    # container only
    "status":       str,   # CSS badge class key
    "created":      str,   # ISO timestamp
}
```

---

### `POST /jobs/<namespace>/<name>/delete`

Delete a container job (forwards to `DELETE /api/jobs/<name>`).

**Auth:** Required
**Security:** `namespace` must equal `session["namespace"]`
**Response:** Redirect to `/jobs`

---

### `GET /jobs/<namespace>/<name>/status`

Get current job status as JSON (used by the `status.html` polling page after a
submit).

**Auth:** Required
**Security:** `namespace` must equal `session["namespace"]`
**Response:** JSON

```json
{
  "status": "succeeded | failed | suspended | running | unknown",
  "ready": 0,
  "active": 0,
  "succeeded": 1,
  "failed": 0
}
```

---

### `POST /jobs/<namespace>/<name>/save`

Read the full job spec (forwards to `GET /api/jobs/<name>`) and save it to the
user's saved-config store as a reusable container template.

**Auth:** Required
**Security:** `namespace` must equal `session["namespace"]`
**Response:** Redirect to `/jobs`

---

## Helm / InterLink Routes (`app/helm.py`)

### `GET /helm`

Render the "Deploy InterLink" form. The HPC-node dropdown is populated from
`manager/hpc/*.yaml` (via `lib.hpc_config.list_hpc_nodes`); all other chart
settings come from `charts_config.yaml`.

**Auth:** Required
**Response:** `helm.html`

---

### `POST /helm/install`

Submit the InterLink Helm install form (forwards to `POST /api/interlink`
with `{"hpc_name": ...}`), which installs the chart bound to the selected HPC
node using the defaults from `charts_config.yaml`.

**Auth:** Required
**Form fields:** `hpc_name` (required)
**Response:** `helm_result.html` with the install outcome

---

### `GET /releases`

List InterLink Helm releases in the user's namespace. One release may exist
per configured HPC node (`interlink-<hpc_name>`); each is checked
individually via `GET /api/interlink?hpc_name=<name>` (empty rows for HPC
nodes with no deployed release).

**Auth:** Required
**Response:** `releases.html`

---

### `POST /releases/<name>/delete`

Uninstall the InterLink Helm release identified by `name`
(`interlink-<hpc_name>`); forwards to `DELETE /api/interlink` with the
recovered `hpc_name`.

**Auth:** Required
**Response:** Redirect to `/jobs`

---

## HPC Routes (`app/hpc.py`)

All HPC routes are mounted under the `/hpc` prefix. They render forms or result
pages; the backend always proxies to the corresponding `/api/hpc/*` endpoint
using the user's session token. The wstunnel parameters are computed from the
session namespace and `site_config.yaml` — the user only picks an HPC node.

### `GET /hpc`

Render the HPC deployment form. The node dropdown is populated from
`manager/hpc/*.yaml` (via `lib.hpc_config.list_hpc_nodes`); the page also shows
the computed wstunnel server/port/secret for the user's namespace.

**Auth:** Required
**Response:** `hpc.html`

---

### `POST /hpc/deploy`

Deploy the HPC Pilot stack on the selected node (forwards to
`POST /api/hpc/deploy` with `{"hpc_name": ...}`).

**Auth:** Required
**Query parameters:** `hpc_name` (required)
**Response:** `hpc_result.html` with the deploy outcome

---

### `GET /hpc/status`

Query `supervisorctl status` on the selected node (forwards to
`GET /api/hpc/status` with `?hpc_name=`).

**Auth:** Required
**Form fields:** `hpc_name` (required)
**Response:** `hpc_result.html`

---

### `POST /hpc/start`

Start all managed services on the selected node (forwards to
`POST /api/hpc/start`).

**Auth:** Required
**Form fields:** `hpc_name` (required)
**Response:** `hpc_result.html`

---

### `POST /hpc/stop`

Stop all managed services on the selected node (forwards to
`POST /api/hpc/stop`).

**Auth:** Required
**Form fields:** `hpc_name` (required)
**Response:** `hpc_result.html`

---

## Saved Config Routes (`app/saved.py`)

### `POST /saved/<config_id>/delete`

Remove a saved configuration entry from the user's store. The redirect target
depends on the entry's `kind`: Helm configs go to `/releases`, container
configs to `/`.

**Auth:** Required
**Response:** Redirect to `/` or `/releases`

---

## Static Assets & API Docs

- All CSS lives in `app/static/style.css`.
- Templates live in `app/templates/` (all extend `base.html`).
- `base.html` embeds the token-expiry countdown JavaScript that redirects to
  `/logout?reason=expired` when the session token expires.
- The interactive Swagger UI for the REST API is served at **`/api/docs`**.

