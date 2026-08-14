# Charts

This directory contains Helm charts for HPC Pilot components.

## Production charts

### Manager app — `manager/`

Deploys the HPC Pilot Manager Flask application **inside** the Kubernetes
cluster, along with all supporting resources (RBAC, ConfigMaps, PVC, Ingress).

- Chart: [`manager/`](./manager/)
- Full instructions: [`manager/README.md`](./manager/README.md)

Quick start:

```bash
SECRET=$(python -c 'import secrets; print(secrets.token_hex(32))')

helm install manager ./charts/manager \
  --set image.repository=ghcr.io/<your-org>/hpc-pilot-manager \
  --set image.tag=latest \
  --set flask.secretKey="$SECRET" \
  --set siteConfig.hostname=manager.example.com \
  --set interlinkConfig.chart=oci://ghcr.io/chbrandt/interlink
```

### InterLink Pod — `interlink/`

Defines the default values for the InterLink Helm chart that the manager
deploys automatically into each user's namespace via `POST /api/interlink`.
This chart is **not** installed directly by the operator — it is managed by
the manager app on behalf of users.

- Values reference: [`interlink/values.yaml`](./interlink/values.yaml)
- Details: [`interlink/README.md`](./interlink/README.md)

---

## Development / example charts

| Directory | Purpose |
|---|---|
| `test_pods/` | Example pod/test manifests for validating the InterLink + wstunnel deployment without the manager |

---

## Deployment order

```
1. helm install manager ./charts/manager   # deploy the manager app
2. (users log in)                          # manager auto-creates user namespaces
3. POST /api/interlink                     # manager deploys InterLink per user
4. (HPC setup)                             # wstunnel client connects from edge-node
```

See [`documentation/deployment.md`](../documentation/deployment.md) for the
complete step-by-step guide.
