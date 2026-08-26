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
| `lib.k8s_client` | Create / list / delete Kubernetes jobs (Deployments) pinned to InterLink nodes |
| `lib.helm_client` | Install / list / get-values / uninstall Helm chart releases |
| `lib.hpc_config` | Load per-HPC-node config files from `manager/hpc/<name>.yaml` |
| `lib.hpc_client` | Deploy and manage wstunnel + supervisord + plugin on a remote HPC node via `mccli` |
| `lib.token_auth` | Validate EGI Check-in JWT tokens; derive K8s namespaces; check group access |
| `lib.token_checkin` | CLI helper for the EGI Check-in OAuth 2.0 Device Authorization Grant |
| `lib.saved_deployments` | Per-user saved-config store; default-chart seeding |

---

(lib-k8s)=

## `lib.k8s_client` — Kubernetes Client

```{code-block} python
from lib.k8s_client import K8sClient
```

### `K8sClient`

Wraps the official `kubernetes` Python client.
Reads cluster credentials from a kubeconfig file (falls back to `$KUBECONFIG`,
then `~/.kube/config`).

```{code-block} python
k8s = K8sClient()                                # default kubeconfig
k8s = K8sClient(kubeconfig_path="/path/cfg.yaml")  # explicit path
```

---

#### Namespace helpers

```{code-block} python
namespaces = k8s.list_namespaces()          # -> list[str]
exists = k8s.namespace_exists("user-abc123")  # -> bool
result = k8s.create_namespace("user-abc123")
# -> {"success": True, "namespace": "user-abc123"}  (idempotent on 409)
```

---

#### InterLink node discovery

```{code-block} python
nodes = k8s.list_interlink_nodes()  # -> list[str]
```

Returns the sorted names of cluster nodes carrying the taint key
`virtual-node.interlink/no-schedule`.

---

#### Creating a job

A job is a `Deployment` pinned to an InterLink virtual-kubelet node via
`nodeSelector` + a toleration for `virtual-node.interlink/no-schedule`.
Replicas, resources, ports, Services and Ingresses are **not supported**
(InterLink maps one pod to one HPC batch job).

```{code-block} python
result = k8s.create_job(
    name="my-job",
    image="ubuntu:22.04",
    node_name="virtual-node-user-abc123",
    namespace="user-abc123",
    env_vars={"MY_ENV": "hello"},   # optional
    command="echo hello",           # optional, run as /bin/sh -c
)
```

**Return value** (success):

```{code-block} json
{
  "success": true,
  "job_name": "my-job",
  "namespace": "user-abc123",
  "image": "ubuntu:22.04"
}
```

---

#### Listing jobs

```{code-block} python
jobs  = k8s.list_jobs(namespace="user-abc123")
all_  = k8s.list_jobs()   # namespace=None or "__all__" lists across all ns
```

Each entry (label-filtered to `created-by=hpc-pilot-webapp`):

```{code-block} json
{
  "name": "my-job",
  "namespace": "user-abc123",
  "image": "ubuntu:22.04",
  "node_name": "virtual-node-user-abc123",
  "status": "available",
  "created": "2026-08-14 12:34:56"
}
```

---

#### Job spec, status, delete

```{code-block} python
spec   = k8s.get_job_spec(name="my-job", namespace="user-abc123")
status = k8s.get_job_status(name="my-job", namespace="user-abc123")
result = k8s.delete_job(name="my-job", namespace="user-abc123")
```

`get_job_spec` returns `{name, image, node_name, env_vars, command}`
(or `{"error": "..."}` on failure).

`get_job_status` returns:

```{code-block} json
{
  "name": "my-job",
  "namespace": "user-abc123",
  "replicas": 1,
  "ready_replicas": 1,
  "available_replicas": 1,
  "updated_replicas": 1,
  "replicas_status": "1/1",
  "status": "available",
  "image": "ubuntu:22.04",
  "created": "2026-08-14 12:34:56"
}
```

`status` is `available` / `progressing` / `unknown` (from the Deployment
conditions).

`delete_job` returns `{"job": {"success": bool, "name" | "error": ...}}`.

---

(lib-helm)=

## `lib.helm_client` — Helm Client

```{code-block} python
from lib.helm_client import helm_install, helm_list, helm_get_values, helm_uninstall
```

Thin wrappers around the `helm` CLI binary via `subprocess.run()`.
Requires Helm 3 on `$PATH`; inherits the process `KUBECONFIG`.

### `helm_install`

```{code-block} python
result = helm_install(
    release_name="interlink",
    chart="oci://ghcr.io/chbrandt/interlink",
    namespace="user-abc123",
    values_yaml="...",   # optional raw YAML string (passed via --values -)
    version=None,        # optional — pin chart version
    timeout="5m0s",     # default
)
# -> {"success": bool, "output": str, "error": str | None}
```

Runs `helm install <release> <chart> --namespace <ns> --wait --timeout=5m0s
[--version …] --values -` (values read from stdin).

### `helm_list`

```{code-block} python
releases = helm_list(namespace="user-abc123")  # -> list[dict]
```

Returns a normalised list (`name`, `namespace`, `revision`, `updated`,
`status`, `chart`, `app_version`). Raises `RuntimeError` on non-zero exit.

### `helm_get_values`

```{code-block} python
result = helm_get_values(release_name="interlink", namespace="user-abc123")
# -> {"success": bool, "values_yaml": str | None, "error": str | None}
```

Returns the user-supplied values as a raw YAML string (`values_yaml` is `None`
when no custom values were provided). Used by the GUI's "save release" feature.

### `helm_uninstall`

```{code-block} python
result = helm_uninstall(release_name="interlink", namespace="user-abc123")
# -> {"success": bool, "output": str, "error": str | None}
```

---

(lib-hpc-config)=

## `lib.hpc_config` — Per-HPC-Node Configuration

```{code-block} python
from lib.hpc_config import list_hpc_nodes, load_hpc_config
```

Loads per-HPC-node config files from `manager/hpc/<name>.yaml`. Each file has
the structure:

```yaml
hostname: 161.9.255.206   # HPC login node hostname or IP (required)
ssh_port: 3333            # SSH port (default 22)
plugin: echo              # InterLink plugin: echo | docker | slurm
```

The filename stem (`<name>`) is the HPC node's unique identifier used by the
API and web GUI.

```{code-block} python
nodes = list_hpc_nodes()
# -> [{"name": "test-echo", "hostname": ..., "ssh_port": ..., "plugin": ...}, ...]

cfg = load_hpc_config("test-echo")
# -> {"name": "test-echo", "hostname": ..., "ssh_port": ..., "plugin": ...}
# raises ValueError if the file is missing or invalid
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

- `mccli` — `pip install mccli` (wraps SSH with OIDC token auth)
- `flaat-userinfo` — used by mccli to decode the token

All public functions return a dict ``{success, output, error}`` (the two
boolean-returning probes below are the exception).

> **Remote file copies** (`copy_supervisord_conf`, `copy_plugin_conf`) first
> try the SFTP/scp channel (`mccli scp`); if that fails — common on
> motley-cue endpoints that only allow command execution — they fall back to
> piping the file over the SSH *exec* channel (`cat > <remote>`), which works
> wherever `mccli ssh <cmd>` works.  Success is decided by the subprocess
> return code (and stderr is surfaced on failure), not by "stdout is empty".

### `check_connection` / `check_installed`

```{code-block} python
ok = hpc_client.check_connection(token, hpc_host, ssh_port=22)   # -> bool (runs whoami)
ok = hpc_client.check_installed(token, hpc_host, ssh_port=22)     # -> bool (~/.pilot exists)
```

### `deploy`

```{code-block} python
result = hpc_client.deploy(
    token="<egi-access-token>",
    hpc_host="hpc-login.example.org",
    ssh_port=22,
    wstunnel_server="app.example.com",   # site_config.hostname
    wstunnel_port=80,                     # site_config.wstunnel.port
    wstunnel_secret="user-abc123",       # the user's namespace
    wstunnel_local_port=4000,            # site_config.wstunnel.local_port
    plugin="echo",                        # echo | docker | slurm
)
# -> {"success": bool, "output": str, "error": str}
```

Installs the HPC Pilot stack on the remote node by running these step
functions in order:

1. `setup_directories` — `mkdir -p ~/.pilot/{tmp,bin,log}`
2. `install_supervisord` — `python3.12 -m venv ~/.pilot && pip install supervisor`
3. `copy_supervisord_conf` — render & copy `supervisord.conf.jinja`
4. `install_wstunnel` — download wstunnel `v10.5.5` to `~/.pilot/bin`
5. `install_plugin` — install the named plugin (pip for `echo`, binary for
   `docker`/`slurm`) and symlink it to `~/.pilot/bin/plugin`
6. `copy_plugin_conf` — render & copy the plugin's `InterLinkConfig.yaml`
7. `start_supervisord` — start (or reload) the supervisord daemon
8. `check_status` — `supervisorctl status`

### `undeploy`

```{code-block} python
result = hpc_client.undeploy(token, hpc_host, ssh_port=22)
```

Stops services, shuts down supervisord, and removes the `~/.pilot` directory.

### `get_status` / `start_services` / `stop_services`

```{code-block} python
status  = hpc_client.get_status(token, hpc_host, ssh_port=22)
started = hpc_client.start_services(token, hpc_host, ssh_port=22)
stopped = hpc_client.stop_services(token, hpc_host, ssh_port=22)
# -> {"success": bool, "output": str, "error": str | None}
```

Map to `supervisorctl status` / `start all` / `stop all` on the remote node.

---


(lib-token)=

## `lib.token_auth` — Token Validation

```{code-block} python
from lib.token_auth import validate_token, derive_namespace, check_group_access, fetch_userinfo
```

### `validate_token`

```{code-block} python
claims = validate_token("<raw-jwt-string>")
# raises ValueError on any failure
```

Full validation:

1. Decode the JWT header to extract `kid` and `alg`.
2. Extract the `iss` claim without verification.
3. Check `iss` against the trusted-issuers list.
4. Fetch (or use a 1-hour-cached) JWKS key set from the issuer.
5. Verify the RSA signature, expiry (`exp`), and issuer.

**Trusted issuers** (`token_auth.py`):

- `https://aai.egi.eu/auth/realms/egi` (production)
- `https://aai-dev.egi.eu/auth/realms/egi` (development)
- `https://aai-demo.egi.eu/auth/realms/egi` (demo)

### `fetch_userinfo`

```{code-block} python
userinfo = fetch_userinfo(token, issuer)
# -> {"eduperson_entitlement": [...], "entitlements": [...], ...}
```

Fetches the user's entitlements from the issuer's UserInfo endpoint. EGI
Check-in does not put entitlements in the JWT, so this is used by the
group-access check.

### `check_group_access`

```{code-block} python
check_group_access(claims, allowed_groups)   # raises ValueError on denial
```

Verifies that the user holds at least one of the `allowed_groups` substrings
in `eduperson_entitlement` or `entitlements` (union, substring match).
Raises `ValueError` on denial; is a no-op when `allowed_groups` is empty.

### `derive_namespace`

```{code-block} python
ns = derive_namespace(claims["sub"])   # "user-" + sha256(sub)[:16]
```

Stable, Kubernetes-safe namespace name (21 chars, RFC 1123 compliant).

---

## `lib.token_checkin` — CLI Token Helper

```{code-block} bash
python manager/lib/token_checkin.py new                  # full device flow
python manager/lib/token_checkin.py new --audience interlink
python manager/lib/token_checkin.py refresh --file tokens_egi.json
```

Implements the OAuth 2.0 Device Authorization Grant against EGI Check-in,
using the public client `oidc-agent`. Saves tokens to a JSON file (mode 0600)
and supports refresh/revoke. See [authentication.md](authentication.md).

---

(lib-saved)=

## `lib.saved_deployments` — Configuration Store

```{code-block} python
from lib.saved_deployments import (
    save_config, list_configs, get_config, delete_config,
    seed_defaults, def_chart_is_singleton, load_default_charts,
)
```

Persists per-user saved configurations as a JSON file per namespace under
`manager/data/<namespace>.json`.

```{code-block} python
# Save a deployment spec (kind: "container" | "helm")
entry = save_config(namespace="user-abc123", kind="container",
                    config={"name": "my-job", "image": "ubuntu:22.04", ...})
# entry includes a generated "id" and "saved_at"

# List (optionally filtered by kind)
configs = list_configs("user-abc123", kind="helm")

# Load / delete by id
cfg = get_config("user-abc123", "<id>")      # -> dict | None
delete_config("user-abc123", "<id>")         # -> bool
```

### Default-chart seeding

```{code-block} python
seed_defaults("user-abc123", site_config={"hostname": "..."})
```

Reads `charts_config.yaml` and inserts any missing default chart entries into
the user's store with stable IDs (`default-<release_name>`), resolving the
per-user placeholder tokens `__NAMESPACE__` and `__HOSTNAME__` against the
user's namespace and the supplied site config. Idempotent — safe to call on
every login.

### Helpers

```{code-block} python
charts = load_default_charts()                 # -> list[dict] from charts_config.yaml
is_singleton = def_chart_is_singleton("oci://ghcr.io/chbrandt/interlink")
```

