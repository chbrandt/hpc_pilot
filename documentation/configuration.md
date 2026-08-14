# Configuration

HPC Pilot is configured by a few YAML files and environment variables. The
manager can run **outside** the cluster (external kubeconfig) or **inside** it
as a Deployment managed by the [`charts/manager/`](../charts/manager/) Helm
chart (in-cluster ServiceAccount).

---

## `site_config.yaml`

Operator-level settings describing the deployment environment. Lives at
`manager/site_config.yaml` and is read at startup by the `api`/`app` layer; the
`lib/` layer never reads it (callers pass values in explicitly). In-cluster,
this file is generated from the chart's `siteConfig` values (see
[`charts/manager/templates/configmap-site.yaml`](../charts/manager/templates/configmap-site.yaml)).

| Key | Default | Description |
|---|---|---|
| `hostname` | `dev.local` | The single, fixed hostname the manager and every user's InterLink wstunnel endpoint are exposed on. No wildcard DNS or wildcard TLS certificate is required: each user's InterLink deployment is reachable at `<hostname>/<namespace>` (a path-prefixed route on this one hostname), not a per-user subdomain. |
| `wstunnel.port` | `80` | Port the wstunnel ingress listens on (the port the HPC-side wstunnel client connects to). |
| `wstunnel.local_port` | `4000` | Local TCP port on the HPC edge-node that wstunnel forwards to the InterLink plugin. |
| `allowed_groups` | `[]` | Optional list of entitlement substrings. When non-empty, only users whose UserInfo contains at least one of these substrings in `eduperson_entitlement` or `entitlements` are granted access. An empty list disables the check (open access) and skips the UserInfo call. |

**Example:**

```yaml
# site_config.yaml
hostname: prod.example.com

wstunnel:
  port: 80
  local_port: 4000

# Restrict to members of a specific VO (substring match against entitlements)
allowed_groups:
  - "vo.access.egi.eu"
```

> **Note:** `hostname` replaces the previous `cluster_domain` key (which
> implied a wildcard base domain with per-user subdomains). With prefix-based
> routing, `hostname` is a single fixed value — no wildcard DNS record or
> wildcard TLS certificate is required anymore.

---

(charts-configyaml)=

## `charts_config.yaml`

Chart-catalogue settings controlling which Helm chart is pre-seeded into every
user's saved-config store on first login. Lives at
`manager/charts_config.yaml`; in-cluster it is generated from the chart's
`interlinkConfig` values
(see [`charts/manager/templates/configmap-charts.yaml`](../charts/manager/templates/configmap-charts.yaml)).

See the inline comments in that file for the full `default_charts` field list
and the supported placeholder tokens (`__NAMESPACE__`, `__HOSTNAME__`).

---

## Per-HPC-node config — `manager/hpc/<name>.yaml`

Each connectable HPC site is described by **one file per node** in
`manager/hpc/`. The filename stem (`<name>`) is the HPC node's unique
identifier used by the API and web GUI; it is loaded by
`lib.hpc_config` (`list_hpc_nodes` / `load_hpc_config`).

```yaml
# manager/hpc/test-echo.yaml
hostname: 161.9.255.206   # HPC login node hostname or IP (required)
ssh_port: 3333            # SSH port (default 22)
plugin: echo             # InterLink plugin: echo | docker | slurm
```

| Field | Type | Default | Description |
|---|---|---|---|
| `hostname` | string | — | HPC login node hostname or IP (required) |
| `ssh_port` | int | `22` | SSH port on the HPC node |
| `plugin` | string | `echo` | InterLink plugin to install (`echo`, `docker`, or `slurm`) |

**Plugin values:**

| Value | Description |
|---|---|
| `echo` | Minimal echo plugin — echoes job requests back. Useful for testing the tunnel without a real workload manager. |
| `docker` | Docker-based job execution plugin. |
| `slurm` | SLURM workload manager integration plugin. |

> The wstunnel parameters (server, port, secret, local port) are **not**
> stored here — they are derived from the K8s namespace and
> `site_config.yaml` at deploy time. HPC node configs do **not** support
> placeholder tokens.

---

## Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | No | Path to kubeconfig file. If unset, the Kubernetes client falls back to `~/.kube/config` (or the in-cluster ServiceAccount token). |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | **Yes (production)** | Key used to sign/encrypt the session cookie. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_PORT` | `5000` | No | TCP port for the Flask server. |
| `FLASK_DEBUG` | `0` | No | Set to `1` for debug mode (auto-reload, detailed error pages). **Never use in production.** |
| `API_BASE_URL` | `http://localhost:5000` | No | Base URL of the REST API as seen from the `app/` GUI layer. Defaults to loopback (single-process mode); set it to split the GUI and API into separate processes. |

---

## Kubeconfig (external mode)

When running outside the cluster, the manager authenticates to the Kubernetes
API with an external kubeconfig.

### Minimal kubeconfig for a service account

```yaml
apiVersion: v1
kind: Config
clusters:
  - cluster:
      server: https://<cluster-api-endpoint>:6443
      certificate-authority-data: <base64-ca-cert>
    name: my-cluster
contexts:
  - context:
      cluster: my-cluster
      user: hpc-pilot
    name: hpc-pilot@my-cluster
current-context: hpc-pilot@my-cluster
users:
  - name: hpc-pilot
    user:
      token: <service-account-token>
```

After creating the service account and binding the chart's `ClusterRole`,
extract the token with:

```bash
kubectl create token hpc-pilot-sa --duration=8760h
```

---

## RBAC

The authoritative RBAC is the chart's
[`clusterrole.yaml`](../charts/manager/templates/clusterrole.yaml); see
[kubernetes.md](kubernetes.md#rbac-requirements) for the rule summary. When
running outside the cluster, apply the same rules to the kubeconfig user.

---

## In-cluster deployment (recommended for production)

Install the manager with the Helm chart (see
[`charts/manager/README.md`](../charts/manager/README.md) for the full
reference):

```bash
SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

helm install manager ./charts/manager \
  --namespace hpc-pilot --create-namespace \
  --set flask.secretKey="$SECRET" \
  --set siteConfig.hostname=manager.example.com \
  --set interlinkConfig.chart=oci://ghcr.io/chbrandt/interlink
```

The chart creates: a `Namespace`, `ServiceAccount`, `ClusterRole` +
`ClusterRoleBinding`, a `Secret` for `FLASK_SECRET_KEY`, two `ConfigMap`s
(`site_config.yaml` + `charts_config.yaml`), an optional `PVC` for
`/app/data`, a `Deployment`, a `Service`, and an `Ingress`.

---

## Docker

The image is built from the root [`Dockerfile`](../Dockerfile) (base
`python:3.11-slim`, installs Helm v3, copies `manager/`, runs
`python main.py`):

```bash
docker build -t ghcr.io/<your-org>/hpc-pilot-manager:latest .
docker run -d -p 5000:5000 \
  -e FLASK_SECRET_KEY="<strong-random-key>" \
  -v /path/to/kubeconfig:/root/.kube/config:ro \
  -v $(pwd)/manager/site_config.yaml:/app/site_config.yaml:ro \
  -v $(pwd)/manager/charts_config.yaml:/app/charts_config.yaml:ro \
  ghcr.io/<your-org>/hpc-pilot-manager:latest
```

> For HPC operations the container also needs `mccli` and `flaat-userinfo`
> on `$PATH` (not bundled in the default image). When running in-cluster,
> provide them via a custom image or an init container.

---

## Helm CLI configuration

The `helm` binary must be on `$PATH`. The manager inherits the process
environment, so any Helm configuration that works in the shell where you start
the manager also works inside it:

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami   # only for repo/chart refs
helm repo update
python manager/main.py
```

For OCI references (`oci://…`) no prior `helm repo add` is needed. The default
InterLink chart uses an OCI reference.

---

## Trusted EGI Check-in issuers

To add or remove trusted issuers, edit the `TRUSTED_ISSUERS` list in
[`manager/lib/token_auth.py`](../manager/lib/token_auth.py):

```python
TRUSTED_ISSUERS = [
    "https://aai.egi.eu/auth/realms/egi",       # production
    "https://aai-dev.egi.eu/auth/realms/egi",   # development
    "https://aai-demo.egi.eu/auth/realms/egi",  # demo
]
```

---

## Production checklist

- [ ] Set a strong, random `FLASK_SECRET_KEY` (or use `flask.existingSecret`)
- [ ] Keep `flask.debug = "0"`
- [ ] Use HTTPS — configure `ingress.tls` with a valid certificate for the
      single `siteConfig.hostname`
- [ ] Apply the chart's `ClusterRole` to a dedicated service account
- [ ] Restrict kubeconfig permissions to only what the `ClusterRole` grants
- [ ] Configure a `proxy_read_timeout` of at least 360 seconds on the reverse
      proxy (long-running `helm install --wait`)
- [ ] Enable `persistence.enabled` so per-user saved configs survive restarts
- [ ] Provide `mccli` + `flaat-userinfo` if HPC edge-node operations are needed
- [ ] Configure `allowed_groups` to restrict access to the intended EGI VO(s)

