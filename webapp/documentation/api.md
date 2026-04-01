# API Reference

All routes are served by `app.py`. Authentication is enforced by checking
`get_session_user()` at the start of each protected route — unauthenticated
requests are redirected to `/login`.

---

## Auth Routes

### `GET /login`

Render the login page.

**Query parameters:**

| Parameter | Description |
|---|---|
| `reason=expired` | Show "session expired" warning banner |
| `refresh=1` | Show "update your token" banner instead of normal title |
| `next=<url>` | URL to redirect to after successful login |

**Response:** `login.html`

---

### `POST /login`

Validate the submitted access token and start a session.

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

## Container Deployment Routes

### `GET /`

Render the "Deploy a Container" form.

**Auth:** Required  
**Response:** `index.html`

---

### `POST /deploy`

Create a Kubernetes Deployment (+ optional Service and Ingress) from form data.

**Auth:** Required

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `name` | ✓ | Deployment name (RFC 1123, max 63 chars) |
| `image` | ✓ | Container image (e.g. `nginx:latest`) |
| `replicas` | | Number of pod replicas (default: `1`) |
| `cpu_request` | | CPU request (e.g. `100m`) |
| `cpu_limit` | | CPU limit (e.g. `500m`) |
| `mem_request` | | Memory request (e.g. `64Mi`) |
| `mem_limit` | | Memory limit (e.g. `256Mi`) |
| `command` | | Shell command override; runs as `/bin/sh -c "<command>"` |
| `env_key[]` | | Environment variable key (repeatable) |
| `env_value[]` | | Environment variable value (repeatable, paired with `env_key`) |
| `port_number[]` | | Port number 1–65535 (repeatable) |
| `port_name[]` | | Port name (repeatable, optional) |
| `port_protocol[]` | | `TCP` or `UDP` (repeatable, default `TCP`) |
| `ingress_enabled` | | `1` to enable Ingress (requires at least one port) |
| `ingress_host` | | Ingress hostname (empty = match all) |
| `ingress_path` | | Ingress path (default `/`) |
| `ingress_port` | | Target port name or number |
| `ingress_class` | | Ingress class name (e.g. `nginx`) |

**Success:** Render `status.html` with deployment result  
**Failure:** Flash error, redirect to `GET /`

---

### `GET /deployments`

List all workloads in the user's namespace: container deployments **and** Helm
releases merged into a single unified table.

**Auth:** Required  
**Response:** `deployments.html` with `workloads` list

Each workload entry:

```python
{
    "kind":         "container" | "helm",
    "name":         str,
    "namespace":    str,
    "detail":       str,   # image (container) or chart name (helm)
    "status":       str,   # CSS badge class key
    "status_label": str,   # badge display text
    "created":      str,   # ISO timestamp
    "service_ports": [...] | None,  # container only
    "ingress_url":   str | None,    # container only
}
```

---

### `POST /deployments/<namespace>/<name>/delete`

Delete a container deployment (Deployment + Service + Ingress).

**Auth:** Required  
**Security:** `namespace` must equal `session["namespace"]`  
**Response:** Redirect to `/deployments`

---

### `GET /deployments/<namespace>/<name>/status`

Get current deployment status as JSON (used by the `status.html` polling page).

**Auth:** Required  
**Response:** JSON

```json
{
  "status": "available | progressing | unknown",
  "replicas_status": "2/2",
  "ready_replicas": 2,
  "replicas": 2
}
```

**Error responses:**
- `401` — `{"error": "Not authenticated"}`
- `500` — `{"error": "<message>"}`

---

## Helm Routes

### `GET /helm`

Render the "Deploy a Helm Chart" form.

**Auth:** Required  
**Response:** `helm.html`

---

### `POST /helm/install`

Install a Helm chart with `helm install --wait --timeout=5m0s`.

**Auth:** Required

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `release_name` | ✓ | Release name (RFC 1123, max 63 chars) |
| `chart` | ✓ | Chart reference (see [Helm Integration](helm.md)) |
| `version` | | Pin a specific chart version |
| `values_yaml` | | YAML string passed to `--values -` (stdin) |

**Behaviour:** Synchronous — the HTTP request blocks until `helm install` completes
(up to 5 minutes). The submit button is disabled via JavaScript to prevent
double-submission.

**Success:** Render `helm_result.html` with install output  
**Failure:** Flash error, redirect to `GET /helm`

---

### `GET /releases`

List Helm releases in the user's namespace (standalone page, not linked from
navbar — use `/deployments` for the merged view).

**Auth:** Required  
**Response:** `releases.html`

---

### `POST /releases/<name>/delete`

Uninstall a Helm release (`helm uninstall`).

**Auth:** Required  
**Response:** Redirect to `/deployments`
