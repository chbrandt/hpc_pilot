# HPC Pilot Manager — Helm Chart

This chart deploys the **HPC Pilot Manager** Flask application inside a
Kubernetes cluster, together with all the supporting resources it needs to
manage per-user [InterLink](https://interlink-project.dev) pod deployments.

- **Chart name:** `egi-hpc-pilot` (see [`Chart.yaml`](./Chart.yaml))
- **Version:** `0.1.0` / appVersion `0.1.0`

---

## Overview

### What this chart creates

| Resource | Kind | Purpose |
|---|---|---|
| `hpc-pilot` | `Namespace` | Dedicated namespace for the manager |
| `hpc-pilot-manager` | `ServiceAccount` | In-cluster identity for the pod |
| `egi-hpc-pilot` | `ClusterRole` | RBAC superset of the InterLink virtual-kubelet role: namespaces, jobs, services, ingresses, nodes (+status), pods (+status), configmaps, secrets, events, deployments, replicasets, serviceaccounts (+token), certificatesigningrequests (+approval), leases, and cluster/role RBAC |
| `egi-hpc-pilot` | `ClusterRoleBinding` | Binds the ClusterRole to the ServiceAccount |
| `<release>-manager` | `Secret` | Flask session secret key |
| `<release>-manager-site-config` | `ConfigMap` | `site_config.yaml` (hostname, wstunnel ports, allowed_groups) |
| `<release>-manager-charts-config` | `ConfigMap` | `charts_config.yaml` (InterLink chart preset per user) |
| `<release>-manager-data` | `PersistentVolumeClaim` | Durable store for per-user saved deployment configs |
| `<release>-manager` | `Deployment` | The Flask manager pod |
| `<release>-manager` | `Service` | ClusterIP service (port 80 → Flask port 5000) |
| `<release>-manager` | `Ingress` | nginx Ingress on `siteConfig.hostname` (exposes the manager UI/API) |

---

## Prerequisites

- Kubernetes ≥ 1.24
- Helm v3
- An **nginx Ingress controller** installed in the cluster
- A **DNS record** for the single `siteConfig.hostname` pointing at the Ingress
  controller's external IP (no wildcard record required — routing is
  path-prefix based)
- The manager container image built from the root `Dockerfile` and pushed to
  an accessible registry

---

## Building the image

```bash
# From the repository root
docker build -t ghcr.io/<your-org>/hpc-pilot-manager:latest .

docker push ghcr.io/<your-org>/hpc-pilot-manager:latest
```

---

## Quick start

```bash
# 1. Generate a strong Flask secret key
SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

# 2. Install the chart
helm install manager ./charts/manager \
  --set image.repository=ghcr.io/<your-org>/hpc-pilot-manager \
  --set image.tag=latest \
  --set flask.secretKey="$SECRET" \
  --set siteConfig.hostname=manager.example.com \
  --set interlinkConfig.chart=oci://ghcr.io/chbrandt/interlink

# 3. Verify
kubectl get all -n hpc-pilot
kubectl get ingress -n hpc-pilot
```

---

## Configuration reference

### Image

| Parameter | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/chbrandt/hpc-pilot-manager` | Container image repository |
| `image.tag` | `latest` | Image tag |
| `image.pullPolicy` | `Always` | Image pull policy |
| `image.pullSecrets` | `[]` | Optional registry pull secrets |
| `replicaCount` | `1` | Number of manager pods |

### Flask

| Parameter | Default | Description |
|---|---|---|
| `flask.port` | `5000` | Port Flask listens on inside the container |
| `flask.debug` | `"0"` | Set to `"1"` for debug mode (never in production) |
| `flask.secretKey` | `"ok-for-dev-only-…"` | Flask session secret key — **always override in production** |
| `flask.existingSecret` | `""` | Use a pre-existing Secret instead of creating one |
| `flask.existingSecretKey` | `flask-secret-key` | Key inside the existing Secret |

### Site configuration (`site_config.yaml`)

| Parameter | Default | Description |
|---|---|---|
| `siteConfig.hostname` | `app.hpc-pilot.test.fedcloud.eu` | Single fixed hostname for the manager and every user's InterLink wstunnel endpoint (path-prefix routing — no wildcard DNS) |
| `siteConfig.wstunnel.port` | `80` | Port the wstunnel ingress listens on |
| `siteConfig.wstunnel.localPort` | `4000` | Local port on the HPC edge-node wstunnel forwards to |
| `siteConfig.allowedGroups` | `[]` | Optional list of EGI VO entitlement substrings restricting access (empty = open) |

> The `site_config.yaml` ConfigMap is rendered by
> [`templates/configmap-site.yaml`](./templates/configmap-site.yaml). Without
> `siteConfig.wstunnel.*` the HPC endpoints would raise a `KeyError`, so
> always supply them.

### InterLink chart defaults (`charts_config.yaml`)

| Parameter | Default | Description |
|---|---|---|
| `interlinkConfig.chart` | `oci://ghcr.io/chbrandt/interlink` | InterLink chart reference (OCI / repo / URL) |
| `interlinkConfig.version` | `null` | Pin a chart version, or `null` for latest |
| `interlinkConfig.interlink.address` | `http://0.0.0.0` | InterLink API server bind address |
| `interlinkConfig.interlink.port` | `3000` | InterLink API server port |
| `interlinkConfig.interlink.disableProjectedVolumes` | `true` | Disable projected service-account token volumes on the virtual-kubelet pod |
| `interlinkConfig.plugin.address` | `http://0.0.0.0` | Plugin bind address (HPC side) |
| `interlinkConfig.plugin.port` | `4000` | Plugin port |
| `interlinkConfig.wstunnel.port` | `8080` | wstunnel server port inside the pod |
| `interlinkConfig.wstunnel.logLevel` | `debug` | wstunnel log level |

The wstunnel **ingress host**, **external port**, **internal port** and
**secret** are deliberately not configurable here — they are derived from
`siteConfig.hostname`, `siteConfig.wstunnel.port`,
`siteConfig.wstunnel.localPort` and the per-user namespace, so every user's
tunnel stays consistent with the site-level routing configuration.

Placeholder tokens resolved per-user at seed time (only these two are used):

| Token | Replaced with |
|---|---|
| `__NAMESPACE__` | User's Kubernetes namespace, e.g. `user-a3f1b2c4d5e6f7a8` |
| `__HOSTNAME__` | `siteConfig.hostname` (the single shared hostname) |

### Persistence

| Parameter | Default | Description |
|---|---|---|
| `persistence.enabled` | `false` | Mount a PVC for `/app/data` |
| `persistence.size` | `1Gi` | PVC storage size |
| `persistence.accessMode` | `ReadWriteOnce` | PVC access mode |
| `persistence.storageClass` | `""` | StorageClass (leave blank for cluster default) |
| `persistence.existingClaim` | `""` | Use a pre-existing PVC |

### Ingress

| Parameter | Default | Description |
|---|---|---|
| `ingress.enabled` | `true` | Create an Ingress resource |
| `ingress.className` | `nginx` | IngressClass name |
| `ingress.path` | `/` | URL path prefix |
| `ingress.pathType` | `Prefix` | Path type |
| `ingress.port` | `80` | Backend service port |
| `ingress.annotations` | see values | Extra annotations (timeouts are pre-configured) |
| `ingress.tls.enabled` | `true` | Enable TLS for `siteConfig.hostname` |
| `ingress.tls.secretName` | `manager-tls` | Secret holding the TLS certificate |

> The Ingress host is `siteConfig.hostname` (not a separate `ingress.host`
> value). The chart does **not** require a wildcard record — a single-host
> TLS certificate is sufficient.

### Resources

| Parameter | Default |
|---|---|
| `resources.requests.cpu` | `100m` |
| `resources.requests.memory` | `128Mi` |
| `resources.limits.cpu` | `500m` |
| `resources.limits.memory` | `512Mi` |

---


## How the manager connects to InterLink deployments

```
User logs in (EGI Check-in token)
  │
  ▼
Manager derives namespace: user-<hash>   ←── from token "sub" claim
  │
  ▼
POST /api/interlink
  │   helm install interlink-<hpc_name> oci://ghcr.io/chbrandt/interlink \
  │     --namespace user-<hash> \
  │     --values -   (wstunnel.ingress.host = siteConfig.hostname, secret = __NAMESPACE__)
  │
  ▼
InterLink pod created in user-<hash> namespace:
  ├── virtual-kubelet node: vk-node-<hash>-<hpc_name>
  ├── interLink API server container  (port 3000)
  ├── wstunnel server container       (port 8080)
  └── nginx Ingress:  <hostname>/<namespace>  →  wstunnel:8080   (path-prefix)
  │
  ▼
HPC edge-node runs wstunnel client:
  wstunnel client \
    --http-upgrade-path-prefix 'user-<hash>' \
    -R 'tcp://4000:localhost:4000' \
    ws://<hostname>:80
```

> **No wildcard DNS required.** A single DNS record for `siteConfig.hostname`
> pointing at the nginx Ingress controller's external IP is enough — each
> user's wstunnel is disambiguated by the path prefix.

---

## Upgrading

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set image.tag=<new-tag>
```

## Uninstalling

```bash
helm uninstall manager
kubectl delete namespace hpc-pilot   # also removes user namespaces if desired
```

