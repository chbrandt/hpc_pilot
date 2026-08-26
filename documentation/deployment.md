# Deployment Guide

This guide covers deploying the full HPC Pilot stack: the **Manager app**
inside a Kubernetes cluster, the per-user **InterLink pods**, and the **HPC
edge-node** wstunnel client that connects them.

---

## Architecture overview (in-cluster)

```
                  ┌─────────────────────────────────────────────────┐
                  │            Kubernetes Cluster                   │
                  │                                                 │
                  │  ┌──────────────────────────────────────────┐   │
                  │  │  Namespace: hpc-pilot                    │   │
   Browser / API ─┼──►  Deployment: manager          ◄─ Helm   │   │
   (HTTPS)         │  │  Service:    ClusterIP         ◄─ RBAC   │   │
                  │  │  Ingress:    <hostname>                  │   │
                  │  │  PVC:        /app/data                   │   │
                  │  └──────────────┬───────────────────────────┘   │
                  │                 │ helm install interlink         │
                  │                 │ (per user, on demand)          │
                  │                 ▼                                │
                  │  ┌──────────────────────────────────────────┐   │
                  │  │  Namespace: user-<hash>                  │   │
                  │  │  Pod: virtual-node-<namespace>            │   │
                  │  │    ├── interLink API server (port 3000)   │   │
                  │  │    └── wstunnel server    (port 8080)     │   │
                  │  │  Ingress: <hostname>/<namespace> (path)    │   │
                  │  └──────────────────────────────────────────┘   │
                  │                                                 │
                  └─────────────────────────────────────────────────┘
                                          ▲
                          WebSocket tunnel │ (wstunnel, path-prefix)
                                          │
                   ┌──────────────────────┴──────────────────────────┐
                   │  HPC Edge-node                                  │
                   │   wstunnel client  →  interLink plugin          │
                   └─────────────────────────────────────────────────┘
```

> Routing is **path-prefix** on a single `hostname`: each user's wstunnel is
> reached at `https://<hostname>/<namespace>` via the
> `--http-upgrade-path-prefix <namespace>` option. **No wildcard DNS or
> wildcard TLS certificate is required** — one hostname and one certificate
> suffice.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Kubernetes ≥ 1.24 | Any distribution (minikube, k3s, EKS, GKE, …) |
| Helm v3 | `brew install helm` or `snap install helm --classic` |
| nginx Ingress controller | `helm install ingress-nginx ingress-nginx/ingress-nginx` |
| A DNS record | `<hostname>` → Ingress controller external IP (single host, no wildcard) |
| A TLS certificate | for `<hostname>` (cert-manager + Let's Encrypt, or a pre-existing cert) |
| Container registry | For the manager image (GitHub Container Registry, Docker Hub, …) |
| `mccli` + `flaat-userinfo` | Bundled in the manager image (installed from `requirements.txt`); required for HPC edge-node operations |

---

## Step 1 — Build and push the Manager image

```bash
# From the repository root (Dockerfile is at the repo root)
docker build -t ghcr.io/<your-org>/hpc-pilot-manager:latest .
docker push ghcr.io/<your-org>/hpc-pilot-manager:latest
```

The root `Dockerfile`:

- Base image: `python:3.11-slim`
- Installs Helm v3 CLI (required by `helm_client.py`)
- Copies `manager/` and installs Python dependencies from `requirements.txt`
  (this includes `mccli` + `flaat`, required by `hpc_client.py` for HPC edge-node SSH)
- Installs the `openssh-client` system package (used by `mccli`/SSH)
- Runs `python main.py`, exposes port 5000

---

## Step 2 — Configure DNS and TLS

Pick a single hostname (e.g. `hpc-pilot.example.com`) and point it at the nginx
Ingress controller's external IP:

```
hpc-pilot.example.com   →  <ingress-controller-external-ip>
```

Provision a TLS certificate for that single hostname (cert-manager + Let's
Encrypt, or a pre-existing cert) and add it to `values.yaml`:

```yaml
ingress:
  tls:
    - secretName: manager-tls
      hosts:
        - hpc-pilot.example.com
```

No wildcard record or wildcard certificate is needed.

---

## Step 3 — Install the Manager Helm chart

```bash
SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

helm install manager ./charts/manager \
  --namespace hpc-pilot --create-namespace \
  --set image.repository=ghcr.io/<your-org>/hpc-pilot-manager \
  --set image.tag=latest \
  --set flask.secretKey="${SECRET}" \
  --set siteConfig.hostname=hpc-pilot.example.com \
  --set interlinkConfig.chart=oci://ghcr.io/chbrandt/interlink
```

### What this creates

| Resource | Name | Purpose |
|---|---|---|
| `Namespace` | `hpc-pilot` | Dedicated manager namespace |
| `ServiceAccount` | `hpc-pilot-manager` | In-cluster identity |
| `ClusterRole` | `egi-hpc-pilot` | RBAC: namespaces, deployments, services, ingresses, nodes, pods, secrets/configmaps, events, replicasets |
| `ClusterRoleBinding` | `egi-hpc-pilot` | Binds the ClusterRole to the ServiceAccount |
| `Secret` | `manager` | `FLASK_SECRET_KEY` |
| `ConfigMap` | `manager-site-config` | `site_config.yaml` (hostname, wstunnel ports, allowed_groups) |
| `ConfigMap` | `manager-charts-config` | `charts_config.yaml` (InterLink chart preset per user) |
| `PersistentVolumeClaim` | `manager-data` | `/app/data` — durable saved-deployment configs (optional) |
| `Deployment` | `manager` | Flask manager pod |
| `Service` | `manager` | ClusterIP → Flask port 5000 |
| `Ingress` | `manager` | `<hostname>` → Service |

### Verify

```bash
kubectl get all -n hpc-pilot
kubectl get ingress -n hpc-pilot
# Expected: manager pod Running, Ingress with ADDRESS populated
```

---

## Step 4 — First login

Navigate to `https://<hostname>` and log in with your **EGI Check-in** token.

On first login the manager:

1. Validates the Bearer token via EGI Check-in JWKS.
2. If `allowed_groups` is set, fetches entitlements from the UserInfo endpoint
   and checks group membership.
3. Derives a deterministic namespace: `user-<sha256(sub)[:16]>`.
4. Creates the namespace in the cluster (`POST /api/namespaces/ensure`).
5. Seeds the default chart presets (`POST /api/saved/seed`), including the
   InterLink chart, into `/app/data/<namespace>.json`.

---

## Step 5 — Deploy InterLink for a user

### Via the web UI

1. Go to the **Helm** tab.
2. Click **Deploy** (the InterLink preset is pre-seeded).
3. The manager runs:

   ```bash
   helm install interlink oci://ghcr.io/chbrandt/interlink \
     --namespace user-<hash> \
     --wait --timeout=5m0s \
     --values -   # values injected from charts_config.yaml with per-user tokens resolved
   ```

4. The InterLink chart creates:
   - A `Deployment` (`virtual-node-<namespace>`) containing the **interLink API
     server** and **wstunnel server** containers.
   - A `Service` exposing the wstunnel server.
   - An `Ingress` routing WebSocket traffic on `<hostname>/<namespace>` to the
     wstunnel server (path-prefix routing).

### Via the REST API

```bash
export TOKEN=<egi-check-in-access-token>

curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  https://<hostname>/api/interlink | jq .
```

---

## Step 6 — Connect the HPC edge-node

Once the InterLink pod is running, connect the HPC side. The manager automates
this with `POST /api/hpc/deploy` (which installs wstunnel + supervisord + the
InterLink plugin on the HPC node via `mccli`/SSH), but the underlying
`wstunnel client` command is:

```bash
# On the HPC edge-node
wstunnel client \
  --http-upgrade-path-prefix 'user-<hash>' \
  -R 'tcp://4000:localhost:4000' \
  ws://<hostname>:80
```

Where:

- `user-<hash>` = the user's full namespace (used as the wstunnel secret / path prefix)
- `<hostname>` = `site_config.hostname` (the shared cluster hostname)
- `4000` = the port the InterLink plugin listens on (`wstunnel.local_port`)

> The `wstunnel_protocol` (`ws` vs `wss`) is chosen automatically from the
> configured port (`wss` on 443, `ws` otherwise).

---

## Step 7 — Verify end-to-end connectivity

```bash
# On the Kubernetes side — check the InterLink pod is running
kubectl get pods -n user-<hash>

# On the HPC side — check the wstunnel client tunnel is active
supervisorctl status wstunnel   # → RUNNING

# From the manager UI — the virtual-kubelet node should be listed
kubectl get nodes
```

---

## Upgrading the Manager

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set image.tag=<new-tag>
```

To change the site hostname (requires re-seeding user configs):

```bash
helm upgrade manager ./charts/manager \
  --reuse-values \
  --set siteConfig.hostname=new.example.com
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
- [ ] Use HTTPS — configure `ingress.tls` with a valid certificate for the
      single `siteConfig.hostname`
- [ ] Point one DNS record at the Ingress controller (no wildcard needed)
- [ ] Enable `persistence.enabled` so per-user saved configs survive restarts
- [ ] Use a named `storageClass` for `persistence.storageClass` if the cluster
      default is not reliable
- [ ] Size Gunicorn workers for concurrent Helm installs
      (each `helm install --wait` blocks a worker for up to 5 minutes)
- [ ] Configure your OCI registry credentials if the InterLink chart or
      manager image is in a private registry
- [ ] Provide `mccli` + `flaat-userinfo` on the manager pod for HPC operations
- [ ] Configure `allowed_groups` to restrict access to the intended EGI VO(s)
- [ ] Monitor `/app/data` PVC usage as the user base grows

