(api)=

# `api` — REST API

The `api` layer exposes every manager capability as a JSON HTTP endpoint.
It is designed for scripted access (shell, Python, CI pipelines) using
**EGI Check-in Bearer tokens** for authentication.

All responses use `Content-Type: application/json`.
All endpoints are mounted under the `/api` path prefix.

```{contents} Contents
:local:
:depth: 2
```

---

## Authentication

Every endpoint is protected. Attach a valid EGI Check-in access token as a
[Bearer token](https://datatracker.ietf.org/doc/html/rfc6750):

```{code-block} bash
curl -H "Authorization: Bearer $TOKEN" https://manager.example.org/api/jobs
```

**Token acquisition**

The token is the same EGI Check-in access token used to log into the web GUI.
You can obtain one with the Device Authorization Grant helper shipped in this
repository:

```{code-block} bash
python manager/lib/token_checkin.py new
# Follow the printed URL, authenticate in your browser, then read the
# access token from the printed tokens file.
export TOKEN=$(python -c "import json;print(json.load(open('tokens_egi.json'))['access_token'])")
```

See [authentication.md](authentication.md) for the full token flow.

**Namespace isolation**

Each token's `sub` (subject) claim is hashed to produce a deterministic,
isolated Kubernetes namespace (`user-<16-hex-chars>`).
All resources created through the API are placed in that namespace — you never
see or manage other users' resources.

**Error responses**

| HTTP code | Meaning |
|---|---|
| `401` | Missing, malformed, expired, or untrusted-issuer token |
| `403` | Token valid but the user is not a member of a configured `allowed_groups` VO |
| `503` | Token valid but the EGI UserInfo endpoint could not be reached to verify group membership |
| `400` | Missing required field in request body |
| `404` | Named resource not found |
| `500` | Server-side error (Kubernetes API unreachable, Helm failed, mccli/SSH failed, …) |

Error body:

```{code-block} json
{"error": "human-readable message"}
```

---

(api-health)=

## Health - `/health`

### `GET /health` - Service liveness probe

Public endpoint (no authentication) that returns a simple JSON status.
Useful for Kubernetes liveness/readiness probes and load-balancer checks.

``` bash
curl https://manager.example.org/health
```

**Response:**

``` json
{"status": "Service alive"}
```

---

(api-k8s)=

## Kubernetes Jobs — `/api/namespaces`, `/api/nodes`, `/api/jobs`

Jobs are Kubernetes `Deployment` objects pinned to an InterLink virtual-kubelet
node via `nodeSelector` + `tolerations`. InterLink forwards the pod to an HPC
batch job on the connected HPC site, so **replica counts, CPU/memory requests
and limits, container ports, Services and Ingresses are not supported** and are
not part of the API (see [kubernetes.md](kubernetes.md)).

### `POST /api/userspace/` — Ensure the user namespace exists

Idempotently create the caller's personal namespace (derived from the token
`sub` claim). Safe to call on every login — returns `"created": false` when the
namespace already exists.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/userspace/ | jq .
```

**Response `200`** (already exists):

```{code-block} json
{"namespace": "user-a3f1b2c4d5e6f7a8", "created": false}
```

**Response `201`** (newly created):

```{code-block} json
{"namespace": "user-a3f1b2c4d5e6f7a8", "created": true}
```

---

### `DELETE /api/userspace/` — Delete the user namespace

Delete the caller's personal namespace and every resource inside it (jobs,
InterLink releases). The namespace is derived from the token, so users can
only ever delete their own.

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/userspace/ | jq .
```

**Response `200`**:

```{code-block} json
{"namespace": "user-a3f1b2c4d5e6f7a8", "deleted": true}
```


---

### `GET /api/nodes/interlink` — List InterLink virtual-kubelet nodes

Return the names of cluster nodes registered as InterLink virtual-kubelet nodes.
A node is considered an InterLink node when it carries the taint key
`virtual-node.interlink/no-schedule`.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/nodes/interlink | jq .
```

**Response `200`:**

```{code-block} json
{"nodes": ["virtual-node-user-a3f1b2c4d5e6f7a8"]}
```

---

### `GET /api/jobs` — List jobs

Returns all HPC-Pilot-managed jobs (Deployments labelled
`created-by=hpc-pilot-webapp`) in the caller's namespace.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/jobs/preset | jq .
```

**Response `200`:**

```{code-block} json
[
  {
    "name": "my-job",
    "namespace": "user-a3f1b2c4d5e6f7a8",
    "image": "ubuntu:22.04",
    "node_name": "virtual-node-user-a3f1b2c4d5e6f7a8",
    "status": "running",
    "created": "2026-08-14 12:34:56"
  }
]
```

---
### `POST /api/jobs/preset` — Submit a job (preset)

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-job",
    "image": "ubuntu:22.04",
    "node_name": "virtual-node-user-a3f1b2c4d5e6f7a8",
    "command": "echo hello && sleep infinity",
    "env_vars": {"MY_VAR": "hello"}
  }' \
  https://manager.example.org/api/jobs | jq .
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✓ | Job name (RFC 1123 label: lowercase alphanumeric + hyphens, max 63 chars) |
| `image` | string | ✓ | Container image reference, e.g. `ubuntu:22.04` |
| `node_name` | string | ✓ | InterLink virtual-kubelet node name (sets `nodeSelector["kubernetes.io/hostname"]`) |
| `env_vars` | object | — | `{"KEY": "value"}` pairs |
| `command` | string | — | Shell command override (run as `/bin/sh -c "<command>"`) |
| `cpu` | string | — | CPU request/limit, e.g. `2`, `500m` (default `1`) |
| `memory` | string | — | Memory request/limit, e.g. `4Gi`, `512Mi` (default `1Gi`) |

The `node_name` is validated against the InterLink virtual-kubelet nodes deployed
in the cluster — invalid node names are rejected with `400`.

If the namespace does not yet exist it is created automatically before the job.

**Response `201`** (created):

```{code-block} json
{
  "success": true,
  "job_name": "my-job",
  "namespace": "user-a3f1b2c4d5e6f7a8",
  "image": "ubuntu:22.04"
}
```

---

### `POST /api/jobs/spec` — Submit a job from a Pod spec

Create a job from the `spec` field of a Pod manifest. The spec is used verbatim as
the job's pod-template spec, giving full control over containers,
resources, commands and node pinning; the InterLink toleration is injected
automatically when missing.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"my-job\",
    \"spec\": {
      \"containers\": [{\"name\": \"my-job\", \"image\": \"ubuntu:22.04\"}],
      \"nodeSelector\": {\"kubernetes.io/hostname\": \"vk-node-1\"}
    }
  }\"
  https://manager.example.org/api/jobs/spec | jq .
```

**Request body:** `name` (job name) and `spec` (a Pod-manifest `spec` dict
containing at least `containers`).

**Response `201`** (created): same shape as `/api/jobs/preset`.

---

### `GET /api/jobs/<name>` — Get job spec

Read back the full job spec — used by the GUI's "save configuration" feature to
store a reusable template.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/jobs/my-job | jq .
```

**Response `200`:**

```{code-block} json
{
  "name": "my-job",
  "image": "ubuntu:22.04",
  "node_name": "virtual-node-user-a3f1b2c4d5e6f7a8",
  "env_vars": {"MY_VAR": "hello"},
  "command": "echo hello && sleep infinity"
}
```

**Response `404`** (not found): `{"error": "..."}`

---

### `GET /api/jobs/<name>/status` — Get job status

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/jobs/my-job/status | jq .
```

**Response `200`:**

```{code-block} json
{
  "name": "my-job",
  "namespace": "user-a3f1b2c4d5e6f7a8",
  "ready": 0,
  "active": 0,
  "succeeded": 1,
  "failed": 0,
  "status": "succeeded",
  "image": "ubuntu:22.04",
  "created": "2026-08-14 12:34:56"
}
```

`status` is one of `"succeeded"`, `"failed"`, `"suspended"`, `"running"`, or
`"unknown"` (derived from the batch Job's `Complete` / `Failed` / `Suspended`
conditions and the `active` / `ready` counters). This endpoint is polled by the
`status.html` page after a submit until it reaches `succeeded` or `failed`.

---

### `GET /api/jobs/<name>/output` — Retrieve job output

Returns the job's stdout/stderr as captured through the pod log endpoint.
For InterLink-backed jobs this includes InterLink's own status lines (SLURM
submission, node assignment, timing) followed by the container runtime's
output — the same content as `kubectl logs <pod>`.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/jobs/my-job/output | jq .
```

**Response `200`:**

```{code-block} json
{
  "name": "my-job",
  "pod": "my-job-abc123",
  "content": "This pod my-job-abc123/... has been submitted to SLURM ...\n"
}
```

If no pods exist for the job, or the pod log endpoint is unreachable, the
endpoint returns a `404` with `{"error": "..."}`.

```{note}
Fetching logs of a pod on an InterLink virtual node is proxied by the API
server through the virtual-kubelet's serving endpoint (``:10250``). The
manager automatically approves the virtual-kubelet's
`kubernetes.io/kubelet-serving` CSR right after install — and, as a
self-healing fallback, when a log request fails with a TLS handshake error
(`remote error: tls: internal error`). Without the approved certificate the
API server cannot fetch any logs from the virtual node.
```

---

### `DELETE /api/jobs/<name>` — Delete a job

Deletes the Deployment. No Service or Ingress is created for jobs, so nothing
else needs to be removed.

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/jobs/my-job | jq .
```

**Response `200`:**

```{code-block} json
{"job": {"success": true, "name": "my-job"}}
```

---

(api-helm)=

## InterLink Chart — `/api/interlink`

The manager manages exactly **one** Helm release per user — the InterLink
virtual-kubelet pod. The release is named `interlink`, is a singleton (a user
may not deploy more than one), and its chart reference, version and default
values are read from `charts_config.yaml` (see [helm.md](helm.md)).

### `POST /api/interlink` — Deploy InterLink

Install the InterLink chart into the user's namespace using the defaults from
`charts_config.yaml`. No request body is required; per-user placeholders
(`__NAMESPACE__`, `__HOSTNAME__`) in the default values are resolved
server-side from the token and `site_config.yaml`.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/interlink | jq .
```

```{note}
This call blocks for the duration of `helm install --wait` (timeout 5 minutes).

After a successful install the manager also approves the virtual-kubelet's
`kubernetes.io/kubelet-serving` certificate CSR (Kubernetes ships no
auto-approval for serving CSRs). This is best-effort and never fails the
install; it is retried transparently if a pod-log request later fails with
a TLS handshake error.
```

**Response `201`** (installed):

```{code-block} json
{"success": true, "output": "...helm output...", "error": null}
```

**Response `400`** (singleton already deployed / install failed):

```{code-block} json
{"error": "..."}
```

---

### `GET /api/interlink` — Get InterLink values

Return the user-supplied values for the deployed InterLink release.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/interlink | jq .
```

**Response `200`:**

```{code-block} json
{
  "success": true,
  "values_yaml": "nodeName: ...\ninterlink: ...\n",
  "error": null
}
```

**Response `404`** (not deployed): `{"error": "..."}`

---

### `DELETE /api/interlink` — Uninstall InterLink

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/interlink | jq .
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "release \"interlink\" uninstalled\n", "error": null}
```

---

(api-saved)=

## Saved Configurations — `/api/saved`

### `POST /api/saved/seed` — Seed default configs

Idempotently seed the default Helm chart configs (from `charts_config.yaml`)
into the authenticated user's saved-config store, applying per-user
placeholder resolution against `site_config.yaml`. Already-seeded entries
(identified by their stable IDs) are never duplicated.

This is called automatically at login, but is safe to call on demand.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/saved/seed | jq .
```

**Response `200`:**

```{code-block} json
{"seeded": true, "namespace": "user-a3f1b2c4d5e6f7a8"}
```

---


(api-hpc)=

## HPC Node Operations — `/api/hpc`

All HPC endpoints accept a JSON body identifying the target HPC node by its
**name** — a short identifier that maps to a config file in
`manager/hpc/<name>.yaml` containing the `hostname`, `ssh_port`, and `plugin`.
The raw Bearer token is forwarded to `mccli` for SSH authentication.

The wstunnel parameters (server hostname, port, secret, local port) are
computed internally from the authenticated user's namespace and
`site_config.yaml` — they are **not** supplied by the caller.

**Common body fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `hpc_name` | string | ✓ | HPC node name (matches `manager/hpc/<name>.yaml`) |

---

### `GET /api/hpc/nodes` — List available HPC nodes

Returns all HPC nodes defined by config files in `manager/hpc/*.yaml`.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/hpc/nodes | jq .
```

**Response `200`:**

```{code-block} json
{
  "nodes": [
    {
      "name": "test-echo",
      "hostname": "161.9.255.206",
      "ssh_port": 3333,
      "plugin": "echo"
    },
    {
      "name": "test-docker",
      "hostname": "161.9.255.233",
      "ssh_port": 22,
      "plugin": "docker"
    }
  ]
}
```

---

### `POST /api/hpc/deploy` — Deploy wstunnel on HPC node

Install the HPC Pilot stack (wstunnel client + supervisord + InterLink plugin)
on the remote node and start it. The node's hostname, SSH port and plugin are
read from the config file; the plugin is installed from its published source
(see [lib.md](lib.md#lib-hpc)).

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/deploy | jq .
```

```{note}
This call may take up to **5 minutes** while the setup steps run on the remote
node (create venv, `pip install supervisor`, download wstunnel, install plugin,
start supervisord).
```

**Response `200`:**

```{code-block} json
{
  "success": true,
  "output": "[setup_directories] ...\n[install_supervisord] ...\n[check_status] ...",
  "error": ""
}
```

On failure `success` is `false`, the partial output is returned, and the HTTP
status is `500`.

---

### `DELETE /api/hpc/deploy` — Uninstall HPC deployment

Stop all supervisord-managed services, shut down supervisord, and remove the
`~/.pilot` installation directory from the remote node. This is the inverse of
`POST /api/hpc/deploy`.

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/deploy | jq .
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "[stop_services] ...\n[remove_installation] Installation removed.", "error": ""}
```

---


### `GET /api/hpc/status` — Query service status

Calls `supervisorctl status` on the remote node.

```{code-block} bash
curl -s -G \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "hpc_name=test-echo" \
  https://manager.example.org/api/hpc/status | jq .
```

**Response `200`:**

```{code-block} json
{
  "success": true,
  "output": "wstunnel   RUNNING   pid 12345, uptime 0:05:32\n",
  "error": null
}
```

---

### `POST /api/hpc/start` — Start services

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/start | jq .
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "wstunnel: started\n", "error": null}
```

---

### `POST /api/hpc/stop` — Stop services

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/stop | jq .
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "wstunnel: stopped\n", "error": null}
```

---

## OpenAPI / Swagger UI

- The raw OpenAPI 3.1 spec is served at [`/api/openapi.yaml`](../manager/api/openapi.yaml).
- A browseable Swagger UI is mounted at **`/api/docs`**.

---

(api-python-client)=

## Using the API from Python

```{code-block} python
import os
import requests

BASE = "https://manager.example.org"
TOKEN = os.environ["TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# List jobs
resp = requests.get(f"{BASE}/api/jobs", headers=HEADERS)
resp.raise_for_status()
for job in resp.json():
    print(job["name"], job["status"])

# Submit a job
resp = requests.post(
    f"{BASE}/api/jobs",
    headers=HEADERS,
    json={
        "name": "my-job",
        "image": "ubuntu:22.04",
        "node_name": "virtual-node-user-a3f1b2c4d5e6f7a8",
        "command": "echo hello",
    },
)
resp.raise_for_status()
print(resp.json())

# Delete a job
resp = requests.delete(f"{BASE}/api/jobs/my-job", headers=HEADERS)
resp.raise_for_status()
print(resp.json())
```

---

## Endpoint Summary

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/userspace/` | Idempotently create the user's personal namespace |
| `DELETE` | `/api/userspace/` | Delete the user's namespace and all its resources |
| `GET` | `/api/nodes/interlink` | List InterLink virtual-kubelet node names |
| `GET` | `/api/jobs` | List jobs in the user's namespace |
| `POST` | `/api/jobs/preset` | Submit a job from a preset (validates `node_name`) |
| `POST` | `/api/jobs/spec` | Submit a job from a Pod-manifest `spec` |
| `GET` | `/api/jobs/<name>` | Return full job spec |
| `GET` | `/api/jobs/<name>/status` | Get job status |
| `GET` | `/api/jobs/<name>/output` | Retrieve job output (stdout/stderr) |
| `DELETE` | `/api/jobs/<name>` | Delete a job |
| `POST` | `/api/interlink` | Install the InterLink singleton chart |
| `GET` | `/api/interlink` | Get InterLink release values |
| `DELETE` | `/api/interlink` | Uninstall the InterLink release |
| `POST` | `/api/saved/seed` | Seed default chart configs for the user |
| `GET` | `/api/hpc/nodes` | List available HPC nodes |
| `POST` | `/api/hpc/deploy` | Deploy wstunnel on an HPC node |
| `DELETE` | `/api/hpc/deploy` | Stop & uninstall the HPC deployment |
| `GET` | `/api/hpc/status` | Query HPC service status |
| `POST` | `/api/hpc/start` | Start HPC services |
| `POST` | `/api/hpc/stop` | Stop HPC services |
| `GET` | `/api/openapi.yaml` | Raw OpenAPI 3.1 spec |
| — | `/api/docs` | Swagger UI |

