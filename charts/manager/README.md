# HPC Pilot Manager — Helm Chart

This chart deploys the **HPC Pilot Manager** Flask application inside a
Kubernetes cluster, together with all the supporting resources it needs to
manage per-user [InterLink](https://interlink-project.dev) pod deployments.

---

## Overview

### What this chart creates

| Resource | Kind | Purpose |
|---|---|---|
| `hpc-pilot` | `Namespace` | Dedicated namespace for the manager |
| `hpc-pilot-manager` | `ServiceAccount` | In-cluster identity for the pod |
| `hpc-pilot-manager` | `ClusterRole` | RBAC rules: namespaces, deployments, services, ingresses, nodes |
| `hpc-pilot-manager` | `ClusterRoleBinding` | Binds the ClusterRole to the ServiceAccount |
| `<release>-manager` | `Secret` | Flask session secret key |
| `<release>-manager-site-config` | `ConfigMap` | `site_config.yaml` (cluster domain, wstunnel settings) |
| `<release>-manager-charts-config` | `ConfigMap` | `charts_config.yaml` (InterLink chart preset for every user) |
| `<release>-manager-data` | `PersistentVolumeClaim` | Durable store for per-user saved deployment configs |
| `<release>-manager` | `Deployment` | The Flask manager pod |
| `<release>-manager` | `Service` | ClusterIP service (port 80 → Flask port 5000) |
| `<release>-manager` | `Ingress` | nginx Ingress (exposes the manager UI/API externally) |

---

## Prerequisites

- Kubernetes ≥ 1.24
- Helm v3
- An **nginx Ingress controller** installed in the cluster
- A **wildcard DNS record** pointing `*.<clusterDomain>` at the Ingress
  controller's external IP (needed for per-user InterLink wstunnel endpoints)
- The manager container image built from `manager/Dockerfile` and pushed to
  an accessible registry

---

## Building the image

```bash
# From the repository root
docker build -t ghcr.io/<your-org>/hpc-pilot-manager:latest manager/

# Push to your registry
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
  --set siteConfig.clusterDomain=<your-cluster-domain> \
  --set ingress.host=manager.<your-cluster-domain>

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
| `image.pullPolicy` | `IfNotPresent` | Image pull policy |
| `replicaCount` | `1` | Number of manager pods |

### Flask

| Parameter | Default | Description |
|---|---|---|
| `flask.port` | `5000` | Port Flask listens on inside the container |
| `flask.debug` | `"0"` | Set to `"1"` for debug mode (never in production) |
| `flask.secretKey` | `""` | Flask session secret key — **always override in production** |
| `flask.existingSecret` | `""` | Use a pre-existing Secret instead of creating one |
| `flask.existingSecretKey` | `"flask-secret-key"` | Key inside the existing Secret |

### Site configuration

| Parameter | Default | Description |
|---|---|---|
| `siteConfig.clusterDomain` | `dev.local` | Wildcard base domain. Each user's InterLink wstunnel will be at `<namespace>.<clusterDomain>` |
| `siteConfig.wstunnel.port` | `80` | Port the wstunnel Ingress listens on |
| `siteConfig.wstunnel.localPort` | `4000` | Port the wstunnel client uses on the HPC edge-node |

### Default charts (InterLink preset)

`chartsConfig` is a multi-line YAML string written verbatim into
`charts_config.yaml`. It defines the Helm chart presets that are auto-seeded
into every user's saved-config store on first login.  The default value
installs the InterLink chart from the OCI registry.

Placeholder tokens resolved per-user:

| Token | Replaced with |
|---|---|
| `__NAMESPACE__` | User's Kubernetes namespace, e.g. `user-a3f1b2c4d5e6f7a8` |
| `__CLUSTER_DOMAIN__` | Value of `siteConfig.clusterDomain` |
| `__NAMESPACE_HASH__` | Hex-digest portion of the namespace (after `user-`) |

### Persistence

| Parameter | Default | Description |
|---|---|---|
| `persistence.enabled` | `true` | Mount a PVC for `/app/data` |
| `persistence.size` | `1Gi` | PVC storage size |
| `persistence.accessMode` | `ReadWriteOnce` | PVC access mode |
| `persistence.storageClass` | `""` | StorageClass (leave blank for cluster default) |
| `persistence.existingClaim` | `""` | Use a pre-existing PVC |

### Ingress

| Parameter | Default | Description |
|---|---|---|
| `ingress.enabled` | `true` | Create an Ingress resource |
| `ingress.className` | `nginx` | IngressClass name |
| `ingress.host` | `manager.dev.local` | Hostname for the manager UI |
| `ingress.path` | `/` | URL path prefix |
| `ingress.annotations` | see values.yaml | Extra annotations (timeouts are pre-configured) |
| `ingress.tls` | `[]` | TLS configuration |

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
  │   helm install interlink oci://ghcr.io/chbrandt/interlink \
  │     --namespace user-<hash> \
  │     --values -   (wstunnel.host = user-<hash>.<clusterDomain>)
  │
  ▼
InterLink pod created in user-<hash> namespace:
  ├── virtual-kubelet container  (interLink API server)
  ├── wstunnel server container  (port 8420 → ClusterIP Service)
  └── nginx Ingress:  user-<hash>.<clusterDomain>:80  →  wstunnel:8420
  │
  ▼
HPC edge-node runs wstunnel client:
  wstunnel client \
    --http-upgrade-path-prefix '<namespace-hash>' \
    -R 'tcp://4000:localhost:4000' \
    ws://user-<hash>.<clusterDomain>:80
```

> **Requirement:** A wildcard DNS record `*.<clusterDomain>` must point to
> the nginx Ingress controller's external IP so that each user's
> `user-<hash>.<clusterDomain>` hostname resolves correctly.

---

## Upgrading

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set siteConfig.clusterDomain=<new-domain>
```

## Uninstalling

```bash
helm uninstall manager
kubectl delete namespace hpc-pilot   # also removes all user namespaces if desired
```
