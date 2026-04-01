# Helm Integration

Multi-container workloads and complex applications are deployed via Helm charts.
The `helm_client.py` module wraps the `helm` CLI using `subprocess.run()`.

No Helm Python SDK is used — subprocess calls are preferred for reliability,
version-independence, and to avoid SDK maintenance overhead.

---

## Prerequisites

- `helm` v3 must be installed and available on `$PATH`
- The app's kubeconfig must grant permissions to the namespaces Helm installs into
- The `helm` binary inherits the same `KUBECONFIG` environment variable as
  the Flask process

---

## Chart Reference Formats

The `chart` field in the form is passed **directly** to `helm install` — any
reference format that Helm understands is valid:

| Format | Example |
|---|---|
| OCI registry | `oci://registry-1.docker.io/bitnamicharts/nginx` |
| Already-added repo | `bitnami/nginx` (requires prior `helm repo add`) |
| HTTPS tarball URL | `https://charts.bitnami.com/bitnami/nginx-18.2.3.tgz` |
| Local path | `/path/to/my-chart` (if the app has filesystem access) |

> **Note:** The app does **not** call `helm repo add` automatically. If using
> a `repo/chart` reference, the repository must have been added to the local
> Helm configuration before the app runs.
> For zero-configuration deployments, prefer OCI or HTTPS URL references.

---

## Install Behaviour

```python
helm_install(
    release_name="my-release",
    chart="oci://registry-1.docker.io/bitnamicharts/nginx",
    namespace="user-abc123",
    values_yaml="replicaCount: 1\nservice:\n  type: NodePort",
    version="18.2.3",   # optional
)
```

This translates to:

```bash
helm install my-release oci://registry-1.docker.io/bitnamicharts/nginx \
  --namespace user-abc123 \
  --wait \
  --timeout=5m0s \
  --version 18.2.3 \
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

---

## Values Override

The values YAML textarea accepts any valid YAML that would go in a `values.yaml`
file. Only the keys you specify override chart defaults — all other values use
the chart's built-in defaults.

Example for Bitnami NGINX with Ingress:

```yaml
replicaCount: 1

service:
  type: ClusterIP

ingress:
  enabled: true
  ingressClassName: nginx
  hostname: myapp.example.com
  path: /
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 128Mi
```

---

## List Releases

```python
helm_list(namespace="user-abc123")
# → [
#     {
#       "name": "my-release",
#       "namespace": "user-abc123",
#       "revision": "1",
#       "updated": "2026-03-31 20:00:00.000000000 +0000 UTC",
#       "status": "deployed",
#       "chart": "nginx-18.2.3",
#       "app_version": "1.27.0",
#     },
#     ...
# ]
```

Runs: `helm list --namespace <ns> --output json`

---

## Uninstall

```python
helm_uninstall(release_name="my-release", namespace="user-abc123")
# → {"success": True, "output": "release \"my-release\" uninstalled\n", "error": None}
```

Runs: `helm uninstall my-release --namespace user-abc123`

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

## Error Handling

All three functions (`helm_install`, `helm_list`, `helm_uninstall`) catch:

- `FileNotFoundError` — `helm` binary not found on `$PATH`
- `subprocess.TimeoutExpired` — install exceeded 5-minute (360-second) timeout
- Non-zero exit code — error output from `helm` itself (e.g. invalid chart,
  resource conflict, cluster unreachable)

Errors are surfaced as:
- Flash messages + redirect (for install/uninstall)
- Error banner on the workloads page (for list)

---

## `helm_client.py` Public API

| Function | Signature | Description |
|---|---|---|
| `helm_install` | `(release_name, chart, namespace, values_yaml=None, version=None, timeout="5m0s") → dict` | Install chart; block until ready; return `{success, output, error}` |
| `helm_list` | `(namespace) → list[dict]` | List releases; return normalised list; raise `RuntimeError` on failure |
| `helm_uninstall` | `(release_name, namespace) → dict` | Uninstall release; return `{success, output, error}` |
