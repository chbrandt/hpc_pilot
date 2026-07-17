(api)=

# `api` — REST API

The `api` layer exposes every `lib` capability as a JSON HTTP endpoint.
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

Every endpoint is protected.
Attach a valid EGI Check-in access token as a
[Bearer token](https://datatracker.ietf.org/doc/html/rfc6750):

```{code-block} bash
curl -H "Authorization: Bearer $TOKEN" https://manager.example.org/api/deployments
```

**Token acquisition**

The token is the same EGI Check-in access token used to log into the web GUI.
You can obtain one with the `checkin_token_device.py` helper in
`duckduck/utils/`:

```{code-block} bash
cd duckduck/utils
python checkin_token_device.py
# Follow the browser prompt — the token is printed to stdout.
export TOKEN=$(python checkin_token_device.py)
```

**Namespace isolation**

Each token's `sub` (subject) claim is hashed to produce a deterministic,
isolated Kubernetes namespace (`user-<16-hex-chars>`).
All resources created through the API are placed in that namespace
— you never see or manage other users' resources.

**Error responses**

| HTTP code | Meaning |
|---|---|
| `401` | Missing, malformed, expired, or untrusted-issuer token |
| `400` | Missing required field in request body |
| `404` | Named resource not found |
| `500` | Server-side error (Kubernetes API unreachable, Helm failed, etc.) |

Error body:

```{code-block} json
{"error": "human-readable message"}
```

---

(api-k8s)=

## Kubernetes Deployments — `/api/deployments`

### `GET /api/deployments` — List deployments

Returns all HPC-Pilot-managed Deployments in the caller's namespace.

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/deployments | jq .
```

**Response `200`:**

```{code-block} json
[
  {
    "name": "my-nginx",
    "namespace": "user-a3f1b2c4d5e6f7a8",
    "image": "nginx:latest",
    "replicas": 1,
    "ready_replicas": 1,
    "replicas_status": "1/1",
    "status": "available",
    "created": "2025-01-15 12:34:56",
    "service_ports": [
      {
        "name": "http",
        "port": 80,
        "node_port": 31234,
        "external_url": "http://10.0.0.1:31234"
      }
    ],
    "ingress_url": null
  }
]
```

---

### `POST /api/deployments` — Create a deployment

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-nginx",
    "image": "nginx:latest",
    "replicas": 1,
    "ports": [{"number": 80, "name": "http"}]
  }' \
  https://manager.example.org/api/deployments | jq .
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✓ | Deployment name (Kubernetes-valid) |
| `image` | string | ✓ | Container image, e.g. `nginx:latest` |
| `replicas` | int | — | Number of replicas (default `1`) |
| `cpu_request` | string | — | CPU request, e.g. `"100m"` |
| `cpu_limit` | string | — | CPU limit, e.g. `"500m"` |
| `mem_request` | string | — | Memory request, e.g. `"64Mi"` |
| `mem_limit` | string | — | Memory limit, e.g. `"256Mi"` |
| `env_vars` | object | — | `{"KEY": "value"}` pairs |
| `ports` | array | — | See [Port objects](#port-objects) |
| `command` | string | — | Override container command (shell string) |
| `ingress` | object | — | See [Ingress config](#ingress-config) |

**Port objects:**

```{code-block} json
{"number": 80, "name": "http", "protocol": "TCP"}
```

`name` and `protocol` are optional (`"TCP"` is the default protocol).

**Ingress config:**

```{code-block} json
{
  "host": "my-nginx.example.com",
  "path": "/",
  "port": 80,
  "class": "nginx"
}
```

All fields optional. When `host` is omitted the Ingress matches all hosts.

**Response `201`** (created):

```{code-block} json
{
  "success": true,
  "deployment_name": "my-nginx",
  "namespace": "user-a3f1b2c4d5e6f7a8",
  "image": "nginx:latest",
  "replicas": 1,
  "status": "progressing",
  "service": {
    "success": true,
    "service_name": "my-nginx-svc",
    "node_ip": "10.0.0.1",
    "ports": [{"name": "http", "port": 80, "node_port": 31234,
               "external_url": "http://10.0.0.1:31234"}]
  }
}
```

---

### `GET /api/deployments/<name>/status` — Deployment status

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/deployments/my-nginx/status | jq .
```

**Response `200`:**

```{code-block} json
{
  "name": "my-nginx",
  "namespace": "user-a3f1b2c4d5e6f7a8",
  "replicas": 1,
  "ready_replicas": 1,
  "available_replicas": 1,
  "updated_replicas": 1,
  "replicas_status": "1/1",
  "status": "available",
  "image": "nginx:latest",
  "created": "2025-01-15 12:34:56"
}
```

`status` is one of `"available"`, `"progressing"`, or `"unknown"`.

---

### `DELETE /api/deployments/<name>` — Delete a deployment

Also removes the associated NodePort Service and Ingress, if they exist.

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/deployments/my-nginx | jq .
```

**Response `200`:**

```{code-block} json
{
  "deployment": {"success": true, "name": "my-nginx"},
  "service":    {"success": true, "name": "my-nginx-svc"},
  "ingress":    null
}
```

---

(api-helm)=

## Helm Releases — `/api/releases` and `/api/helm`

### `GET /api/releases` — List Helm releases

```{code-block} bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/releases | jq .
```

**Response `200`:**

```{code-block} json
[
  {
    "name": "my-release",
    "namespace": "user-a3f1b2c4d5e6f7a8",
    "revision": "1",
    "updated": "2025-01-15 12:34:56",
    "status": "deployed",
    "chart": "nginx-15.0.0",
    "app_version": "1.27.0"
  }
]
```

---

### `POST /api/helm/install` — Install a Helm chart

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "release_name": "my-release",
    "chart": "bitnami/nginx",
    "version": "15.0.0",
    "values_yaml": "replicaCount: 2\n"
  }' \
  https://manager.example.org/api/helm/install | jq .
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `release_name` | string | ✓ | Kubernetes-valid release name |
| `chart` | string | ✓ | Chart reference (repo/chart, OCI URI, or HTTPS tarball URL) |
| `version` | string | — | Pin a specific chart version |
| `values_yaml` | string | — | Raw YAML overrides (passed via `--values -`) |

The chart reference accepts any format the `helm install` command accepts:

```
bitnami/nginx
oci://registry-1.docker.io/bitnamicharts/nginx
https://example.com/charts/nginx-1.0.0.tgz
```

```{note}
The install call blocks (up to 5 minutes) until Helm reports that the
release is fully ready.  For long-running installs you may need to
increase your HTTP client's timeout accordingly.
```

**Response `201`** (installed):

```{code-block} json
{"success": true, "output": "NAME: my-release\n...", "error": null}
```

**Response `400`** (Helm failed):

```{code-block} json
{"success": false, "output": "", "error": "Error: INSTALLATION FAILED: ..."}
```

---

### `DELETE /api/releases/<name>` — Uninstall a Helm release

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.example.org/api/releases/my-release | jq .
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "release \"my-release\" uninstalled\n", "error": null}
```

---

(api-hpc)=

## HPC Node Operations — `/api/hpc`

All HPC endpoints accept a JSON body identifying the target HPC node by
its **name** — a short identifier that maps to a config file in
`manager/hpc/<name>.yaml` containing the hostname, SSH port, and plugin.
The raw Bearer token is forwarded to `mccli` for SSH authentication.

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

Installs wstunnel and supervisord on the remote node, then starts the
tunnel pointing at the Kubernetes-side server.  The HPC node's hostname,
SSH port, and plugin are read from the config file.  The wstunnel
parameters (server, port, secret, local port) are computed internally
from the authenticated user's namespace and the site config — they are
**not** supplied by the caller.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/deploy | jq .
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `hpc_name` | string | ✓ | HPC node name (matches `manager/hpc/<name>.yaml`) |

```{note}
This call may take up to **5 minutes** while the setup script runs on the
remote node (downloads wstunnel binary, configures supervisord, etc.).
```

**Response `200`:**

```{code-block} json
{"success": true, "output": "wstunnel installed...\nsupervisord started\n", "error": null}
```

---

### `DELETE /api/hpc/deploy` — Uninstall HPC deployment

Stop all supervisord-managed services, shut down supervisord, and remove
the ``~/.pilot`` installation directory from the remote node.
This is the inverse of `POST /api/hpc/deploy`.

```{code-block} bash
curl -s -X DELETE \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
  https://manager.example.org/api/hpc/deploy | jq .
```

**Request body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `hpc_name` | string | ✓ | HPC node name |

**Response `200`:**

```{code-block} json
{"success": true, "output": "[stop_services] ...\n[remove_installation] Installation removed.", "error": ""}
```

---

### `POST /api/hpc/status` — Query service status

Calls `supervisorctl status` on the remote node.

```{code-block} bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hpc_name": "test-echo"}' \
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

(api-python-client)=

## Using the API from Python

If you prefer Python over curl, you can call the API with the `requests`
library using the same token:

```{code-block} python
import os
import requests

BASE = "https://manager.example.org"
TOKEN = os.environ["TOKEN"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# List deployments
resp = requests.get(f"{BASE}/api/deployments", headers=HEADERS)
resp.raise_for_status()
for dep in resp.json():
    print(dep["name"], dep["status"])

# Create a deployment
resp = requests.post(
    f"{BASE}/api/deployments",
    headers=HEADERS,
    json={
        "name": "my-nginx",
        "image": "nginx:latest",
        "ports": [{"number": 80, "name": "http"}],
    },
)
resp.raise_for_status()
print(resp.json())

# Delete a deployment
resp = requests.delete(f"{BASE}/api/deployments/my-nginx", headers=HEADERS)
resp.raise_for_status()
print(resp.json())
```

---

## Endpoint Summary

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/deployments` | List K8s deployments |
| `POST` | `/api/deployments` | Create a K8s deployment |
| `GET` | `/api/deployments/<name>/status` | Deployment status |
| `DELETE` | `/api/deployments/<name>` | Delete a deployment |
| `GET` | `/api/releases` | List Helm releases |
| `POST` | `/api/helm/install` | Install a Helm chart |
| `DELETE` | `/api/releases/<name>` | Uninstall a Helm release |
| `GET` | `/api/hpc/nodes` | List available HPC nodes |
| `POST` | `/api/hpc/deploy` | Deploy wstunnel on HPC node |
| `DELETE` | `/api/hpc/deploy` | Stop & uninstall HPC deployment |
| `POST` | `/api/hpc/status` | Query HPC service status |
| `POST` | `/api/hpc/start` | Start HPC services |
| `POST` | `/api/hpc/stop` | Stop HPC services |
