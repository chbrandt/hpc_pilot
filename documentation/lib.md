(lib)=

# `lib` — Python Programming Interface

The `lib` package is the **pure-Python layer** of HPC Pilot.
It has no dependency on Flask or any web framework, so you can import it
directly from a Python interpreter, a Jupyter notebook, or any script without
starting a server.

```{contents} Contents
:local:
:depth: 2
```

---

## Overview

| Module | Purpose |
|---|---|
| `lib.k8s_client` | Create / list / delete Kubernetes Deployments |
| `lib.helm_client` | Install / list / uninstall Helm chart releases |
| `lib.hpc_client` | Deploy and manage wstunnel on a remote HPC node via `mccli` |
| `lib.token_auth` | Validate EGI Check-in JWT tokens; derive K8s namespace names |
| `lib.saved_deployments` | Persist and reload deployment configurations |

---

(lib-k8s)=

## `lib.k8s_client` — Kubernetes Client

```{code-block} python
from lib.k8s_client import K8sClient
```

### `K8sClient`

Wraps the official `kubernetes` Python client.  
Reads cluster credentials from a kubeconfig file.

```{code-block} python
# Default kubeconfig (~/.kube/config or $KUBECONFIG)
k8s = K8sClient()

# Explicit path
k8s = K8sClient(kubeconfig_path="/path/to/my-cluster.yaml")
```

---

#### Namespace helpers

```{code-block} python
# List all namespace names
namespaces = k8s.list_namespaces()          # -> list[str]

# Create a namespace (idempotent — returns success if it already exists)
result = k8s.create_namespace("user-abc123")
# -> {"success": True, "namespace": "user-abc123"}

# Test whether a namespace exists
exists = k8s.namespace_exists("user-abc123")  # -> bool
```

---

#### Creating a Deployment

```{code-block} python
result = k8s.create_deployment(
    name="my-nginx",
    image="nginx:latest",
    namespace="user-abc123",
    replicas=2,
    cpu_request="100m",
    cpu_limit="500m",
    mem_request="64Mi",
    mem_limit="256Mi",
    env_vars={"MY_ENV": "hello"},
    ports=[{"number": 80, "name": "http", "protocol": "TCP"}],
    command=None,          # optional shell override
    ingress={              # optional — creates a K8s Ingress resource
        "host": "my-nginx.example.com",
        "path": "/",
        "port": 80,
        "class": "nginx",
    },
)
```

**Return value** (success):

```{code-block} json
{
  "success": true,
  "deployment_name": "my-nginx",
  "namespace": "user-abc123",
  "image": "nginx:latest",
  "replicas": 2,
  "status": "progressing",
  "service": {
    "success": true,
    "service_name": "my-nginx-svc",
    "node_ip": "10.0.0.1",
    "ports": [{"name": "http", "port": 80, "node_port": 31234,
                "external_url": "http://10.0.0.1:31234"}]
  },
  "ingress": {
    "success": true,
    "ingress_name": "my-nginx-ingress",
    "host": "my-nginx.example.com",
    "path": "/",
    "url": "http://my-nginx.example.com/"
  }
}
```

---

#### Listing Deployments

```{code-block} python
# All deployments created by HPC Pilot in a specific namespace
deployments = k8s.list_deployments(namespace="user-abc123")

# Across all namespaces
all_deps = k8s.list_deployments()   # namespace=None or "__all__"
```

Each entry in the returned list:

```{code-block} json
{
  "name": "my-nginx",
  "namespace": "user-abc123",
  "image": "nginx:latest",
  "replicas": 2,
  "ready_replicas": 2,
  "replicas_status": "2/2",
  "status": "available",
  "created": "2025-01-15 12:34:56",
  "service_ports": [...],
  "ingress_url": "http://my-nginx.example.com/"
}
```

---

#### Deployment Status and Spec

```{code-block} python
# Detailed status for a single deployment
status = k8s.get_deployment_status(name="my-nginx", namespace="user-abc123")
# -> {"name": ..., "status": "available", "replicas": 2, "ready_replicas": 2, ...}

# Read back the spec (useful for saving as a template)
spec = k8s.get_deployment_spec(name="my-nginx", namespace="user-abc123")
# -> {"name": ..., "image": ..., "replicas": ..., "ports": [...], ...}
```

---

#### Deleting a Deployment

```{code-block} python
result = k8s.delete_deployment(name="my-nginx", namespace="user-abc123")
# Also removes the associated Service and Ingress (if any).
# -> {"deployment": {"success": True, ...}, "service": ..., "ingress": ...}
```

---

(lib-helm)=

## `lib.helm_client` — Helm Client

```{code-block} python
from lib.helm_client import helm_install, helm_list, helm_get_values, helm_uninstall
```

Thin wrappers around the `helm` CLI binary.  
Requires Helm 3 to be installed and available on `$PATH`.

---

### `helm_install`

```{code-block} python
result = helm_install(
    release_name="my-release",
    chart="bitnami/nginx",
    namespace="user-abc123",
    values_yaml="replicaCount: 2\n",  # optional raw YAML string
    version="15.0.0",                  # optional — pin chart version
    timeout="5m0s",                    # default
)
# -> {"success": bool, "output": str, "error": str | None}
```

```{note}
`helm_install` runs `helm install --wait`, so it blocks until the release
is fully deployed or the timeout is reached.
```

Common chart reference formats accepted by the `chart` argument:

- `bitnami/nginx` — from an added Helm repo
- `oci://registry-1.docker.io/bitnamicharts/nginx` — OCI registry
- `https://example.com/charts/nginx-1.0.0.tgz` — direct tarball URL

---

### `helm_list`

```{code-block} python
releases = helm_list(namespace="user-abc123")
```

Returns a list of dicts, one per installed release:

```{code-block} json
[
  {
    "name": "my-release",
    "namespace": "user-abc123",
    "revision": "1",
    "updated": "2025-01-15 12:34:56",
    "status": "deployed",
    "chart": "nginx-15.0.0",
    "app_version": "1.27.0"
  }
]
```

Raises `RuntimeError` if the `helm` binary exits non-zero.

---

### `helm_get_values`

```{code-block} python
result = helm_get_values(release_name="my-release", namespace="user-abc123")
# -> {"success": bool, "values_yaml": str | None, "error": str | None}
```

Returns the user-supplied values as a raw YAML string.  
`values_yaml` is `None` when no custom values were provided at install time.

---

### `helm_uninstall`

```{code-block} python
result = helm_uninstall(release_name="my-release", namespace="user-abc123")
# -> {"success": bool, "output": str, "error": str | None}
```

---

(lib-hpc)=

## `lib.hpc_client` — HPC Node Client

```{code-block} python
from lib import hpc_client
```

Uses `mccli` (the motley-cue SSH client) to authenticate with an HPC login
node using an EGI Check-in access token and run remote commands.

**Prerequisites on the manager host:**

- `mccli` — `pip install mccli`
- `flaat-userinfo` — used by mccli to decode the token

---

### `check_connection`

```{code-block} python
result = hpc_client.check_connection(
    token="<egi-access-token>",
    hpc_host="hpc-login.example.org",
    ssh_port=22,           # default
)
# -> {"success": bool, "username": str | None, "output": str, "error": str | None}
```

Runs `whoami` on the remote node. Use this to verify that the token grants
SSH access before attempting a full deployment.

---

### `check_installed`

```{code-block} python
result = hpc_client.check_installed(
    token="<egi-access-token>",
    hpc_host="hpc-login.example.org",
)
# -> {"success": bool, "installed": bool, "output": str, "error": str | None}
```

Checks whether the `~/.hpc-pilot` directory already exists on the remote node.

---

### `deploy`

```{code-block} python
result = hpc_client.deploy(
    token="<egi-access-token>",
    hpc_host="hpc-login.example.org",
    ssh_port=22,
    wstunnel_server="user-abc123.k8s.example.org",
    wstunnel_port=8420,
    wstunnel_secret="my-shared-secret",
    wstunnel_local_port=8420,   # optional, defaults to wstunnel_port
)
# -> {"success": bool, "output": str, "error": str | None}
```

Uploads and executes `manager/hpc/setup.sh` on the remote node.
The script installs `wstunnel` and `supervisord`, writes a supervisord
configuration, and starts the tunnel process.

---

### `get_status` / `start_services` / `stop_services`

```{code-block} python
status  = hpc_client.get_status(token, hpc_host)
started = hpc_client.start_services(token, hpc_host)
stopped = hpc_client.stop_services(token, hpc_host)
# All return: {"success": bool, "output": str, "error": str | None}
```

These call `supervisorctl status/start all/stop all` on the remote node.

---

(lib-token)=

## `lib.token_auth` — Token Validation

```{code-block} python
from lib.token_auth import validate_token, derive_namespace
```

Pure-Python helpers for EGI Check-in JWT access tokens.

---

### `validate_token`

```{code-block} python
try:
    claims = validate_token("<raw-jwt-string>")
except ValueError as exc:
    print(f"Invalid token: {exc}")
```

Performs a full end-to-end validation:

1. Decodes the JWT header to extract `kid` and `alg`.
2. Extracts the `iss` (issuer) claim without verification.
3. Checks `iss` against the trusted issuers list.
4. Fetches (or uses a 1-hour-cached) JWKS key set from the issuer.
5. Verifies the RSA signature, expiry (`exp`), and issuer.
6. Returns the full verified claims dict.

**Trusted issuers:**

- `https://aai.egi.eu/auth/realms/egi` (production)
- `https://aai-dev.egi.eu/auth/realms/egi` (development)
- `https://aai-demo.egi.eu/auth/realms/egi` (demo)

Raises `ValueError` with a human-readable message on any failure.

---

### `derive_namespace`

```{code-block} python
ns = derive_namespace(claims["sub"])
# e.g. "user-a3f1b2c4d5e6f7a8"
```

Produces a stable, Kubernetes-safe namespace name from a user's `sub` claim.
The algorithm is:

```
"user-" + sha256(sub).hexdigest()[:16]
```

The result is always 21 characters (lowercase alphanumeric + hyphens, ≤ 63 chars).

---

(lib-saved)=

## `lib.saved_deployments` — Configuration Store

```{code-block} python
from lib.saved_deployments import (
    save_config, list_configs, get_config, delete_config,
    load_app_config, load_default_charts,
)
```

Persists deployment configurations as JSON files under `manager/data/`.
Configurations can be re-used as templates when launching new deployments.

```{code-block} python
# Save a deployment spec under an ID
save_config(config_id="my-nginx-template", data={"name": "nginx", "image": "nginx:latest", ...})

# List all saved configurations
configs = list_configs()   # -> list[dict]

# Load a single config by ID
cfg = get_config("my-nginx-template")   # -> dict | None

# Delete a config
delete_config("my-nginx-template")

# Load the charts_config.yaml defaults
app_cfg = load_app_config()
charts   = load_default_charts()
```
