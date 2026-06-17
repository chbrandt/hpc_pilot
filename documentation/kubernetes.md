# Kubernetes Integration

Container workloads are managed by `k8s_client.py`, which wraps the official
`kubernetes` Python SDK. Helm chart workloads are managed separately — see
[helm.md](helm.md).

---

## Resources Created

When a container deployment is submitted via the form, the following Kubernetes
resources are created:

### 1. `Deployment` (`apps/v1`)

```
Name:       <name>
Namespace:  <user-namespace>
Labels:     app=<name>, created-by=hpc-pilot-webapp
```

Spec:

- `replicas` — from form (default 1)
- `selector.matchLabels` — `app=<name>`
- Pod template:
  - Container name: `<name>`
  - Image: from form
  - Resources: CPU/memory request & limit (if provided)
  - Env vars: from form (if provided)
  - Command: `["/bin/sh", "-c", "<command>"]` (if provided)
  - Ports: container ports (if provided)
  - **`nodeSelector`** — `kubernetes.io/hostname: <node_name>` (always set)
  - **`tolerations`** — `key: virtual-node.interlink/no-schedule, operator: Exists` (always set)

These two fields are mandatory and ensure every pod is scheduled exclusively
on the InterLink virtual-kubelet node chosen by the user.  Without them,
Kubernetes would not schedule a pod onto a tainted virtual-kubelet node.

### 2. `Service` (`v1`) — created when ports are defined

```
Name:       <name>-svc
Namespace:  <user-namespace>
Labels:     app=<name>, created-by=hpc-pilot-webapp
Type:       NodePort
```

One `ServicePort` entry per port in the form. The `nodePort` is allocated
automatically by Kubernetes.

### 3. `Ingress` (`networking.k8s.io/v1`) — optional, created when Ingress is enabled

```
Name:       <name>-ingress
Namespace:  <user-namespace>
Labels:     app=<name>, created-by=hpc-pilot-webapp
```

Spec:

- `ingressClassName` — from form (if provided)
- Rule: host (optional) → path → `serviceName: <name>-svc`, `servicePort: <port>`
- Annotation `nginx.ingress.kubernetes.io/rewrite-target: /` — added
  automatically when `ingressClassName == "nginx"`

---

## Resources Deleted

`delete_deployment(name, namespace)` deletes all three resources in order:

1. `Deployment` named `<name>`
2. `Service` named `<name>-svc`
3. `Ingress` named `<name>-ingress`

Each deletion is attempted independently; a "not found" response is treated as
success (idempotent). The result dict reports individual success/failure for each
resource.

---

## Namespace Management

User namespaces are created automatically:

- **At login** — if the namespace doesn't exist, it is created immediately
- **At deploy time** — a second check ensures the namespace exists before
  creating resources (guards against edge cases where the login-time creation
  failed silently)

Namespace names follow the pattern `user-<16-char-hex>` (see
[authentication.md](authentication.md)).

```python
# Check
k8s.namespace_exists(namespace)  # → bool

# Create
k8s.create_namespace(namespace)  # → {"success": bool, "error": str | None}
```

---

## Deployment Status

`get_deployment_status(name, namespace)` reads the `Deployment` object's
`.status.conditions` list and maps them to display states:

| Kubernetes condition | `status` value | Badge colour |
|---|---|---|
| `Available=True` | `available` | 🟢 green |
| `Progressing=True` + not Available | `progressing` | 🟡 yellow |
| (none / unknown) | `unknown` | ⚪ grey |

The replicas status string (`"2/2"`, `"1/3"`, etc.) is formed from
`ready_replicas / replicas`.

This endpoint is polled by `status.html` every 3 seconds until status reaches
`available`.

---

## RBAC Requirements

The service account used by the app (or the user in the kubeconfig) needs the
following permissions. The `manager_role.yaml` file in `webapp/` provides a
ready-made `ClusterRole` and `ClusterRoleBinding`:

```yaml
rules:
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "create"]

  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "create", "delete"]

  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "list", "create", "delete"]

  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "create", "delete"]
```

Apply with:

```bash
kubectl apply -f webapp/manager_role.yaml
```

---

## K8sClient Public API

| Method | Description |
|---|---|
| `__init__(kubeconfig_path=None)` | Load kubeconfig; initialise CoreV1, AppsV1, NetworkingV1 API clients |
| `namespace_exists(namespace)` | Return `True` if namespace exists |
| `create_namespace(namespace)` | Create namespace; return `{success, error}` |
| `list_interlink_nodes()` | Return sorted list of node names that carry the `virtual-node.interlink/no-schedule` taint |
| `create_deployment(name, image, node_name, namespace, ...)` | Create Deployment (with `nodeSelector` + `tolerations`) + Service + Ingress; return result dict |
| `list_deployments(namespace=None)` | List deployments (all namespaces if `None`); return list of dicts |
| `get_deployment_spec(name, namespace)` | Return full deployment spec including `node_name` (for saving/re-deploying) |
| `get_deployment_status(name, namespace)` | Return `{status, replicas_status, ready_replicas, replicas}` |
| `delete_deployment(name, namespace)` | Delete Deployment + Service + Ingress; return `{deployment, service, ingress}` result dict |

---

## REST API Endpoints (Kubernetes)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/namespaces/ensure` | Idempotently create the user's personal namespace |
| `GET` | `/api/nodes/interlink` | List InterLink virtual-kubelet node names (`{"nodes": [...]}`) |
| `GET` | `/api/deployments` | List container deployments in the user's namespace |
| `POST` | `/api/deployments` | Create a deployment (`name`, `image`, `node_name` required) |
| `GET` | `/api/deployments/<name>` | Return full spec of a deployment (incl. `node_name`) |
| `GET` | `/api/deployments/<name>/status` | Get deployment status |
| `DELETE` | `/api/deployments/<name>` | Delete deployment + service + ingress |
