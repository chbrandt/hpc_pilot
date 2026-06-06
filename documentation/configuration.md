# Configuration

---

## `site_config.yaml`

Operator-level settings that describe the deployment environment. This file
lives at `manager/site_config.yaml` and is read at startup by the api/app
layer. The `lib/` layer never reads it directly — site config is always passed
explicitly as an argument to library functions.

### Top-level keys

| Key | Type | Default | Description |
|---|---|---|---|
| `cluster_domain` | string | `dev.local` | Wildcard base domain of the Kubernetes cluster. Each user's InterLink deployment is reachable at `<namespace>.<cluster_domain>`. For a wildcard certificate `*.prod.example.com` set this to `prod.example.com`. |
| `oidc` | mapping | *(see below)* | OIDC / OAuth 2.0 settings for EGI Check-in authentication. |

### `oidc` sub-keys

| Key | Default | Description |
|---|---|---|
| `oidc.issuer` | `https://aai.egi.eu/auth/realms/egi` | OIDC realm base URL. OIDC Discovery is performed at `<issuer>/.well-known/openid-configuration`. Use `https://aai-dev.egi.eu/auth/realms/egi` for the dev environment. |
| `oidc.client_id` | `oidc-agent` | OIDC public client identifier registered with EGI Check-in. |
| `oidc.scope` | `openid offline_access profile email` | Space-separated OIDC scopes to request. |
| `oidc.redirect_uri` | `http://localhost:5000/api/auth/checkin/callback` | Callback URL registered with the OIDC provider for the Authorization Code (browser) flow. Must match the exact URI registered in the Check-in client configuration. |

All `oidc` keys are optional — any missing key falls back to its default. Environment variables (see below) take precedence over values set in this file.

**Example — switch to the dev environment:**

```yaml
# site_config.yaml
cluster_domain: prod.example.com

oidc:
  issuer: "https://aai-dev.egi.eu/auth/realms/egi"
  client_id: "my-hpc-pilot-client"
  redirect_uri: "https://hpc-pilot.example.com/api/auth/checkin/callback"
```

> **Note:** `cluster_domain` was previously located inside `charts_config.yaml`
> under the `app_config:` key.  That section has been removed; update any
> existing deployments by migrating the value to `site_config.yaml`.

---

## `charts_config.yaml`

Chart-catalogue settings that control which Helm charts are pre-seeded into
every user's saved-config store on first login. This file lives at
`manager/charts_config.yaml`.

See the inline comments in that file for a full description of the
`default_charts` fields and the supported placeholder tokens
(`__NAMESPACE__`, `__CLUSTER_DOMAIN__`, `__NAMESPACE_HASH__`).

---

## Environment Variables

Environment variables always take precedence over values in `site_config.yaml`.

### Flask / Kubernetes

| Variable | Default | Required | Description |
|---|---|---|---|
| `KUBECONFIG` | `~/.kube/config` | No | Path to kubeconfig file. If unset, the Kubernetes client falls back to `~/.kube/config`. |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | **Yes (production)** | Key used to sign and encrypt the session cookie. Generate with: `python -c 'import secrets; print(secrets.token_hex(32))'` |
| `FLASK_PORT` | `5000` | No | TCP port for the Flask development server. |
| `FLASK_DEBUG` | `0` | No | Set to `1` to enable Flask debug mode (auto-reload, detailed error pages). **Never use in production.** |
| `SITE_CONFIG` | `manager/site_config.yaml` | No | Absolute (or relative-to-CWD) path to the site configuration file. Use this to point the app at a custom config without editing the default file — useful in CI, Docker, or multi-environment deployments. **Example:** `SITE_CONFIG=/etc/hpc-pilot/site_config.yaml python main.py` |

### OIDC / EGI Check-in overrides

These override the corresponding `oidc.*` keys in `site_config.yaml`.

| Variable | Overrides | Description |
|---|---|---|
| `CHECKIN_ISSUER` | `oidc.issuer` | OIDC realm base URL. |
| `CHECKIN_CLIENT_ID` | `oidc.client_id` | OIDC client identifier. |
| `CHECKIN_SCOPE` | `oidc.scope` | Space-separated OIDC scopes. |
| `CHECKIN_REDIRECT_URI` | `oidc.redirect_uri` | Authorization Code callback URL. |

---

## Kubeconfig

The app does **not** run inside the cluster — it uses an external kubeconfig
to authenticate with the Kubernetes API.

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

After creating the service account and binding the `manager_role.yaml` ClusterRole,
extract the token with:

```bash
kubectl create token hpc-pilot-sa --duration=8760h
```

---

## RBAC Setup

```bash
# Apply the ClusterRole and ClusterRoleBinding
kubectl apply -f webapp/manager_role.yaml

# Verify
kubectl auth can-i create deployments --as system:serviceaccount:default:hpc-pilot-sa
```

---

## Running in Production

### Recommended: Gunicorn behind Nginx

```bash
pip install gunicorn
gunicorn -w 4 -b 127.0.0.1:5000 app:app
```

> **Important:** With 4 workers, up to 4 concurrent Helm installs (blocking
> requests) can be handled simultaneously. Each `helm install --wait` can hold
> a worker for up to 5 minutes. Size `--workers` accordingly.

Nginx config snippet:

```nginx
server {
    listen 443 ssl;
    server_name hpc-pilot.example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Allow long-running helm install requests
        proxy_read_timeout 360s;
    }
}
```

### Docker example

```dockerfile
FROM python:3.11-slim

# Install helm
RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

WORKDIR /app
COPY webapp/ .
RUN pip install -r requirements.txt

ENV FLASK_PORT=5000
EXPOSE 5000
CMD ["python", "app.py"]
```

Run with:

```bash
docker run -d \
  -p 5000:5000 \
  -e FLASK_SECRET_KEY="<strong-random-key>" \
  -v /path/to/kubeconfig:/root/.kube/config:ro \
  hpc-pilot
```

---

## Helm CLI Configuration

The `helm` binary must be on `$PATH`. The app inherits the process environment,
so any Helm configuration that works in the shell where you start the app will
also work inside the app:

```bash
# Add a repo before starting the app (if using repo/chart references)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Then start the app
python app.py
```

For OCI references (`oci://…`) no prior `helm repo add` is needed.

---

## Trusted EGI Check-in Issuers

To add or remove trusted issuers, edit the `TRUSTED_ISSUERS` list in
`token_auth.py`:

```python
TRUSTED_ISSUERS = [
    "https://aai.egi.eu/auth/realms/egi",       # production
    "https://aai-dev.egi.eu/auth/realms/egi",    # development
    "https://aai-demo.egi.eu/auth/realms/egi",   # demo
]
```

---

## Production Checklist

- [ ] Set a strong, random `FLASK_SECRET_KEY`
- [ ] Keep `FLASK_DEBUG=0`
- [ ] Use HTTPS (via Nginx/Caddy or cloud load balancer)
- [ ] Apply `manager_role.yaml` with a dedicated service account
- [ ] Restrict kubeconfig permissions to only what `manager_role.yaml` grants
- [ ] Configure a `proxy_read_timeout` of at least 360 seconds on the reverse proxy
- [ ] Add OCI chart repos (or `helm repo add`) before startup if using repo references
- [ ] Monitor worker count vs. expected concurrent users deploying charts
