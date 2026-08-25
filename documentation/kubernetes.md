# Kubernetes Integration

Container workloads are submitted as **jobs** managed by `k8s_client.py`,
which wraps the official `kubernetes` Python SDK.

Jobs are forwarded by **InterLink** to HPC batch jobs running on the connected
HPC system.  Consequently, the following attributes are **not supported** and
are intentionally absent from the job API:

* Replica counts (always 1 — one job = one HPC batch job)
* CPU / memory resource requests and limits
* Container port definitions
* NodePort Services
* Ingress resources

---

## Resource Created

When a job is submitted, a single Kubernetes resource is created:

### `Deployment` (`apps/v1`)

```
Name:       <name>
Namespace:  <user-namespace>
Labels:     app=<name>, created-by=hpc-pilot-webapp
```

Spec:

* `replicas` — always **1** (InterLink maps one pod to one HPC batch job)
* `selector.matchLabels` — `app=<name>`
* Pod template:
  * Container name: `<name>`
  * Image: from form
  * Env vars: from form (if provided)
  * Command: `["/bin/sh", "-c", "<command>"]` (if provided)
  * **`nodeSelector`** — `kubernetes.io/hostname: <node_name>` (always set)
  * **`tolerations`** — `key: virtual-node.interlink/no-schedule, operator: Exists` (always set)

`nodeSelector` and `tolerations` are mandatory: they pin the pod to the chosen
InterLink virtual-kubelet node and allow it to be scheduled there despite the
node's taint.

---

## Resource Deleted

`delete_job(name, namespace)` deletes only the `Deployment` named `<name>`.
No Service or Ingress is created, so none needs to be removed.

---

## Namespace Management

User namespaces are created automatically:

* **At login** — if the namespace doesn't exist, it is created immediately.
* **At submit time** — a second check ensures the namespace exists before
  creating resources.

Namespace names follow the pattern `user-<16-char-hex>` (see
[authentication.md](authentication.md)).

```python
k8s.namespace_exists(namespace)   # → bool
k8s.create_namespace(namespace)   # → {"success": bool, "error": str | None}
```

---

## Job Status

`get_job_status(name, namespace)` reads the `batch/v1` Job object's
`.status` counters (`active`, `ready`, `succeeded`, `failed`) and
`.status.conditions` list, mapping the Job conditions to display states:

| Kubernetes condition | `status` value | Badge colour |
|---|---|---|
| `Complete=True` | `succeeded` | 🟢 green |
| `Failed=True` | `failed` | 🔴 red |
| `Suspended=True` | `suspended` | 🟡 yellow |
| active/ready pod, no terminal condition | `running` | 🟢 green |
| (none / unknown) | `unknown` | ⚪ grey |

This endpoint is polled by `status.html` every 3 seconds until the Job
reaches a terminal state (`succeeded` or `failed`).

---

## RBAC Requirements

The manager's service account (in-cluster) or kubeconfig user (external) needs
to manage namespaces, jobs, and — because the `helm` CLI runs under the same
identity — the Helm release storage (Secrets/ConfigMaps) and supporting
resources. The authoritative rule set is the chart's
[`clusterrole.yaml`](../charts/manager/templates/clusterrole.yaml); in summary:

```yaml
rules:
  - apiGroups: [""]
    resources: ["namespaces", "nodes", "pods"]
    verbs: ["get", "list", "create"]          # pods: read for status polling

  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "create", "delete"]

  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "list", "create", "delete"]

  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "create", "delete"]

  # Helm stores release state as Secrets in the target namespace:
  - apiGroups: [""]
    resources: ["secrets", "configmaps", "events"]
    verbs: ["get", "list", "create", "update", "delete"]
```

> Jobs themselves do not create Services or Ingresses, but the RBAC grants
> them so the same identity can run arbitrary `helm install` charts (e.g. the
> InterLink chart, which does create a Service and an Ingress).

---

## K8sClient Public API

| Method | Description |
|---|---|
| `__init__(kubeconfig_path=None)` | Load kubeconfig; initialise CoreV1 and BatchV1 API clients |
| `list_namespaces()` | Return sorted list of namespace names |
| `namespace_exists(namespace)` | Return `True` if namespace exists |
| `create_namespace(namespace)` | Create namespace; return `{success, error}` (idempotent on 409) |
| `list_interlink_nodes()` | Return sorted list of node names that carry the `virtual-node.interlink/no-schedule` taint |
| `create_job(name, image, node_name, namespace, env_vars, command)` | Create a batch Job (with `nodeSelector` + `tolerations`); return `{success, job_name, ...}` |
| `list_jobs(namespace=None)` | List jobs; return list of dicts with `name`, `image`, `node_name`, `status`, `created` |
| `get_job_spec(name, namespace)` | Return job spec: `name`, `image`, `node_name`, `env_vars`, `command` |
| `get_job_status(name, namespace)` | Return `{status, active, ready, succeeded, failed, ...}` |
| `delete_job(name, namespace)` | Delete the job; return `{"job": {success, name}}` |

---

## REST API Endpoints (Kubernetes)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/namespaces/ensure` | Idempotently create the user's personal namespace |
| `GET` | `/api/nodes/interlink` | List InterLink virtual-kubelet node names (`{"nodes": [...]}`) |
| `GET` | `/api/jobs` | List jobs in the user's namespace |
| `POST` | `/api/jobs` | Submit a job (`name`, `image`, `node_name` required; `env_vars`, `command` optional) |
| `GET` | `/api/jobs/<name>` | Return full spec of a job (`name`, `image`, `node_name`, `env_vars`, `command`) |
| `GET` | `/api/jobs/<name>/status` | Get job status |
| `DELETE` | `/api/jobs/<name>` | Delete the job |
