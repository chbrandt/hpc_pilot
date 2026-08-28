# Helm Integration

The manager uses Helm to deploy the **InterLink virtual-kubelet pod** — the
only Helm release it manages. The `helm_client.py` module wraps the `helm` CLI
using `subprocess.run()`; no Helm Python SDK is used (subprocess calls are
preferred for reliability and version-independence).

The InterLink release is a **singleton**: a user may deploy at most one. Its
chart reference, version and default values are defined once in
[`charts_config.yaml`](../manager/charts_config.yaml) and installed via the
[`/api/interlink`](rest_api.md#api-helm) endpoint (no arbitrary chart is
accepted at runtime). See [configuration.md](configuration.md#charts-configyaml).

---

## Prerequisites

- `helm` v3 must be installed and available on `$PATH` (the manager Docker
  image installs it automatically).
- The manager's kubeconfig / ServiceAccount must grant permissions to the
  namespaces Helm installs into (see [kubernetes.md](kubernetes.md#rbac-requirements)).
- The `helm` binary inherits the same `KUBECONFIG` environment variable as
  the Flask process (or the in-cluster ServiceAccount token).

---

## Chart reference formats

The `chart` value in `charts_config.yaml` is passed **directly** to
`helm install`, so any reference format Helm understands is valid:

| Format | Example |
|---|---|
| OCI registry | `oci://ghcr.io/chbrandt/interlink` (the default) |
| Already-added repo | `bitnami/nginx` (requires prior `helm repo add`) |
| HTTPS tarball URL | `https://charts.bitnami.com/bitnami/nginx-18.2.3.tgz` |
| Local path | `/path/to/my-chart` (if the app has filesystem access) |

> **Note:** The manager does **not** call `helm repo add` automatically. If
> using a `repo/chart` reference, add the repository to the local Helm
> configuration before the app runs. For zero-configuration deployments prefer
> OCI or HTTPS URL references.

---

## Install behaviour

```python
helm_install(
    release_name="interlink",
    chart="oci://ghcr.io/chbrandt/interlink",
    namespace="user-abc123",
    values_yaml="...",   # resolved from charts_config.yaml
    version=None,        # optional — pin chart version
)
```

translates to:

```bash
helm install interlink oci://ghcr.io/chbrandt/interlink \
  --namespace user-abc123 \
  --wait \
  --timeout=5m0s \
  --values -      # values_yaml passed via stdin
```

| Flag | Effect |
|---|---|
| `--wait` | Block until all pods are Ready (or timeout) |
| `--timeout=5m0s` | Give up after 5 minutes |
| `--values -` | Read values YAML from stdin (avoids temp files) |

The HTTP request **blocks** for the duration of the install. The submit button
is disabled in the browser via JavaScript to prevent double-submission. A
"⏳ Installing…" message is shown inline.

### Per-user placeholder resolution

Before installing, the `/api/interlink` handler resolves placeholder tokens in
the default `values_yaml` against the user's namespace and `site_config.yaml`:

| Token | Replaced with |
|---|---|
| `__NAMESPACE__` | User's Kubernetes namespace, e.g. `user-a3f1b2c4d5e6f7a8` |
| `__HOSTNAME__` | `site_config.hostname` (the single shared hostname) |

The resolved values configure the InterLink wstunnel server so that the HPC
edge-node's wstunnel client can reach it via path-prefix routing on that
hostname (`--http-upgrade-path-prefix <namespace>`).


---

## Default values (InterLink)

The InterLink chart's default `values_yaml` (from `charts_config.yaml`) looks
like this (with placeholders that are resolved per-user at install time):

```yaml
nodeName: virtual-node-__NAMESPACE__

interlink:
  enabled: true
  address: http://0.0.0.0
  port: 3000
  disableProjectedVolumes: true  # no projected service-account token volumes on the VK pod

plugin:
  enabled: false            # deployed on the HPC edge-node, not in the pod
  address: http://0.0.0.0
  port: 4000

wstunnel:
  enabled: true
  port: 8420                # server port inside the pod
  ingress:
    host: __HOSTNAME__       # shared cluster hostname (path-prefix routing)
  logLevel: debug
  secret: "__NAMESPACE__"    # path-prefix secret = the user's namespace
```

Operators can override any of these keys via the `chartsConfig` block of the
manager Helm chart (see [`charts/manager/README.md`](../charts/manager/README.md)).


---

## Get values

```python
helm_get_values(release_name="interlink", namespace="user-abc123")
# → {"success": True, "values_yaml": "nodeName: ...\n...", "error": None}
```

Runs `helm get values interlink --namespace <ns> --output yaml`. Used by the
GUI's "save release" feature.

---

## List releases

```python
helm_list(namespace="user-abc123")
# → [{"name": "interlink", "namespace": "user-abc123",
#     "revision": "1", "status": "deployed", "chart": "...", ...}]
```

Runs `helm list --namespace <ns> --output json`. (The REST API exposes only the
managed `interlink` release; `helm_list` itself lists all releases in the
namespace and is primarily a library-level helper.)

---

## Uninstall

```python
helm_uninstall(release_name="interlink", namespace="user-abc123")
# → {"success": True, "output": "release \"interlink\" uninstalled\n", "error": None}
```

Runs `helm uninstall interlink --namespace user-abc123`.

---

## Status Badges

Helm release statuses are mapped to CSS badge classes in `deployments.html`:

| Helm status | Badge class | Colour |
|---|---|---|
| `deployed` | `.badge-deployed` | 🟢 green |
| `pending-install` | `.badge-pending-install` | 🟡 yellow |
| `pending-upgrade` | `.badge-pending-upgrade` | 🟡 yellow |
| `pending-rollback` | `.badge-pending-rollback` | 🟡 yellow |
| `uninstalling` | `.badge-uninstalling` | 🟡 yellow |
| `failed` | `.badge-failed` | 🔴 red |
| `superseded` | `.badge-superseded` | ⚪ grey |

---

## Error handling

All four functions (`helm_install`, `helm_list`, `helm_get_values`,
`helm_uninstall`) catch:

- `FileNotFoundError` — `helm` binary not found on `$PATH`
- `subprocess.TimeoutExpired` — install exceeded the 5-minute timeout
- Non-zero exit code — error output from `helm` itself (e.g. invalid chart,
  resource conflict, cluster unreachable)

Errors are surfaced as a `{"success": False, "error": "..."}` return value
(`helm_list` raises `RuntimeError`), which the API/GUI layers translate into
flash messages or error banners.

---

## `helm_client.py` Public API

| Function | Signature | Description |
|---|---|---|
| `helm_install` | `(release_name, chart, namespace, values_yaml=None, version=None, timeout="5m0s") → dict` | Install chart; block until ready; return `{success, output, error}` |
| `helm_list` | `(namespace) → list[dict]` | List releases; return normalised list; raise `RuntimeError` on failure |
| `helm_get_values` | `(release_name, namespace) → dict` | Return user-supplied values: `{success, values_yaml, error}` |
| `helm_uninstall` | `(release_name, namespace) → dict` | Uninstall release; return `{success, output, error}` |
