# Deployment Guide

This guide covers deploying the full HPC Pilot stack inside a Kubernetes
cluster: the **Manager app** and the per-user **InterLink pods**, and how the
two are interlinked.

---

## Architecture overview (in-cluster)

```
                  ┌─────────────────────────────────────────────────┐
                  │            Kubernetes Cluster                   │
                  │                                                 │
                  │  ┌──────────────────────────────────────────┐   │
                  │  │  Namespace: hpc-pilot                    │   │
                  │  │                                          │   │
  Browser / API ──┼──►  Deployment: manager          ◄─ Helm   │   │
  (HTTPS)         │  │  Service:    ClusterIP         ◄─ RBAC   │   │
                  │  │  Ingress:    manager.<domain>            │   │
                  │  │  PVC:        /app/data                   │   │
                  │  └──────────────┬───────────────────────────┘   │
                  │                 │ helm install interlink         │
                  │                 │ (per user, on demand)          │
                  │                 ▼                                │
                  │  ┌──────────────────────────────────────────┐   │
                  │  │  Namespace: user-<hash>                  │   │
                  │  │                                          │   │
                  │  │  Pod: vk-node                            │   │
                  │  │    ├── interLink API server (port 3000)  │   │
                  │  │    └── wstunnel server    (port 8420)    │   │
                  │  │  Ingress: user-<hash>.<domain>:80        │   │
                  │  └──────────────────────────────────────────┘   │
                  │                                                 │
                  └─────────────────────────────────────────────────┘
                                         ▲
                         WebSocket tunnel │ (wstunnel)
                                         │
                  ┌──────────────────────┴──────────────────────────┐
                  │  HPC Edge-node                                  │
                  │   wstunnel client  →  interLink plugin          │
                  └─────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Kubernetes ≥ 1.24 | Any distribution (minikube, k3s, EKS, GKE, …) |
| Helm v3 | `brew install helm` or `snap install helm --classic` |
| nginx Ingress controller | `helm install ingress-nginx ingress-nginx/ingress-nginx` |
| Wildcard DNS record | `*.<clusterDomain>` → Ingress controller external IP |
| Container registry | For the manager image (GitHub Container Registry, Docker Hub, …) |

---

## Step 1 — Build and push the Manager image

```bash
# From the repository root
docker build -t ghcr.io/<your-org>/hpc-pilot-manager:latest manager/
docker push ghcr.io/<your-org>/hpc-pilot-manager:latest
```

The `manager/Dockerfile`:

- Base image: `python:3.11-slim`
- Installs Helm v3 CLI (required by `helm_client.py`)
- Installs Python dependencies from `requirements.txt`
- Exposes port 5000

---

## Step 2 — Configure DNS and TLS

Every user's InterLink wstunnel server is exposed at:

```
user-<16-hex-chars>.<clusterDomain>
```

You must configure a **wildcard DNS** entry so all sub-domains resolve to your
nginx Ingress controller:

```
*.dev.local   →  <ingress-controller-external-ip>
```

For production, provision a wildcard TLS certificate (cert-manager + Let's
Encrypt or a pre-existing wildcard cert) and add it to `values.yaml`:

```yaml
ingress:
  tls:
    - secretName: wildcard-tls
      hosts:
        - "*.prod.example.com"
```

---

## Step 3 — Install the Manager Helm chart

```bash
# Generate a strong Flask session secret
SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

helm install manager ./charts/manager \
  --namespace hpc-pilot \
  --create-namespace \
  --set image.repository=ghcr.io/<your-org>/hpc-pilot-manager \
  --set image.tag=latest \
  --set flask.secretKey="${SECRET}" \
  --set siteConfig.clusterDomain=<your-cluster-domain> \
  --set ingress.host=manager.<your-cluster-domain>
```

### What this creates

| Resource | Name | Purpose |
|---|---|---|
| `Namespace` | `hpc-pilot` | Dedicated manager namespace |
| `ServiceAccount` | `hpc-pilot-manager` | In-cluster identity |
| `ClusterRole` | `manager` | RBAC: namespaces, deployments, services, ingresses, nodes, pods, secrets |
| `ClusterRoleBinding` | `manager` | Binds ClusterRole to the ServiceAccount |
| `Secret` | `manager` | `FLASK_SECRET_KEY` |
| `ConfigMap` | `manager-site-config` | `site_config.yaml` (cluster domain + wstunnel ports) |
| `ConfigMap` | `manager-charts-config` | `charts_config.yaml` (InterLink chart preset per user) |
| `PersistentVolumeClaim` | `manager-data` | `/app/data` — durable saved-deployment configs |
| `Deployment` | `manager` | Flask manager pod |
| `Service` | `manager` | ClusterIP → Flask port 5000 |
| `Ingress` | `manager` | `manager.<clusterDomain>` → Service |

### Verify

```bash
kubectl get all -n hpc-pilot
kubectl get ingress -n hpc-pilot
# Expected: manager pod Running, Ingress with ADDRESS populated
```

---

## Step 4 — First login

Navigate to `https://manager.<your-cluster-domain>` and log in with your
**EGI Check-in** token.

On first login the manager:

1. Validates the Bearer token via EGI Check-in JWKS.
2. Derives a deterministic namespace: `user-<sha256(sub)[:16]>`.
3. Creates the namespace in the cluster (if it does not exist).
4. Seeds the default chart presets (including the InterLink chart) into the
   user's saved-config store under `/app/data/<namespace>.json`.

---

## Step 5 — Deploy InterLink for a user

### Via the web UI

1. Go to the **Helm** tab.
2. Click **Deploy InterLink** (or use the pre-seeded saved config).
3. The manager runs:

   ```bash
   helm install interlink oci://ghcr.io/chbrandt/interlink \
     --namespace user-<hash> \
     --wait --timeout=5m0s \
     --values -     # values injected from charts_config.yaml with per-user tokens resolved
   ```

4. The InterLink Helm chart creates:
   - A `Deployment` (`vk-node`) containing the **interLink API server** and
     **wstunnel server** containers.
   - A `Service` exposing the wstunnel server.
   - An `Ingress` at `user-<hash>.<clusterDomain>` routing WebSocket traffic
     to the wstunnel server.

### Via the REST API

```bash
export TOKEN=<egi-check-in-access-token>

curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  https://manager.<your-cluster-domain>/api/interlink | jq .
```

---

## Step 6 — Connect the HPC edge-node

Once the InterLink pod is running, connect the HPC side using `wstunnel client`:

```bash
# On the HPC edge-node
wstunnel client \
  --http-upgrade-path-prefix '<namespace-hash>' \
  -R 'tcp://4000:localhost:4000' \
  ws://user-<hash>.<clusterDomain>:80
```

Where:

- `<namespace-hash>` = the hex portion of the namespace (after `user-`)
- `user-<hash>.<clusterDomain>` = the wstunnel server hostname
- `4000` = the port the interLink plugin listens on

The manager's `POST /api/hpc/deploy` endpoint automates this step (uploads
`setup.sh` to the HPC node via SSH and starts `supervisord`).

---

## Step 7 — Verify end-to-end connectivity

```bash
# On the Kubernetes side — check wstunnel and interLink are running
kubectl get pods -n user-<hash>

# On the HPC side — check the wstunnel client tunnel is active
supervisorctl status wstunnel   # → RUNNING

# From the manager UI — open the "Status" page for the InterLink deployment
# Expected: virtual-kubelet node appears in `kubectl get nodes`
kubectl get nodes
```

---

## Upgrading the Manager

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set image.tag=<new-tag>
```

To change the cluster domain (requires re-seeding user configs):

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set siteConfig.clusterDomain=new.domain.example.com
```

---

## Uninstalling

```bash
# Remove the manager
helm uninstall manager --namespace hpc-pilot

# Remove all user namespaces (InterLink deployments included)
kubectl get namespaces -o name | grep "namespace/user-" | xargs kubectl delete

# Remove the manager namespace itself
kubectl delete namespace hpc-pilot
```

---

## Production checklist

- [ ] Set a strong, random `flask.secretKey` (or use `flask.existingSecret`)
- [ ] Keep `flask.debug = "0"`
- [ ] Use HTTPS — configure `ingress.tls` with a valid certificate
- [ ] Set up a wildcard DNS record `*.<clusterDomain>` → Ingress IP
- [ ] Use a named `storageClass` for `persistence.storageClass` if the cluster
      default is not reliable
- [ ] Size Gunicorn workers for concurrent Helm installs
      (each `helm install --wait` blocks a worker for up to 5 minutes)
- [ ] Configure your OCI registry credentials if the InterLink chart or
      manager image is in a private registry
- [ ] Monitor `/app/data` PVC usage as user base grows
