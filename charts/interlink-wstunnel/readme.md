# Deploy interLink and wsTunnel

Step to deploy an interLink API server instance behind a wsTunnel server
for providing connectivity to the outside world (through a wsTunnel client
on the other end).

K8s deployment parts:
- interLink API
- wstunnel server
- ingress

On the other side (HPC, remotely):
- wstunnel client
- interLink plugin

## K8s deployment

Prerequisites:

- Kubernetes cluster (1.20+)
- `kubectl` configured to access your cluster
- NGINX Ingress Controller installed (for ingress support)

### namespace.yaml

Creates the `interlink` namespace for isolating all deployment resources.

```bash
kubectl apply -f namespace.yaml
```

### configmap.yaml

Contains the InterLink configuration with the following settings:

- **InterlinkAddress**: API server address (0.0.0.0)
- **InterlinkPort**: API server port (4000)
- **SidecarURL**: wsTunnel sidecar service URL
- **SidecarPort**: wsTunnel plugin port (3000)
- **DataRootFolder**: Storage location for interLink data (/data/interlink)
- **VerboseLogging**: Enable detailed logging

Edit this file to customize your deployment configuration.

```bash
kubectl apply -f configmap.yaml
```

### deploy-interlink.yaml

Deploys the interLink API server with:

- **Deployment**: Single replica of the interLink container
- **Service**: ClusterIP service exposing port 4000
- **Image**: `ghcr.io/interlink-hq/interlink/interlink:latest`

The API server mounts the configuration from the ConfigMap and listens on port 4000.

```bash
kubectl apply -f deploy-interlink.yaml
```

### deploy-wstunnel.yaml

Deploys the wsTunnel server with:

- **Deployment**: Single replica of the wsTunnel container
- **Service**: ClusterIP service with two ports:
  - Port 8080: WebSocket tunnel endpoint
  - Port 3000: Plugin communication endpoint
- **Image**: `ghcr.io/erebe/wstunnel:latest`
- **Security**: HTTP upgrade path restricted using a token (`h3GywpDrP6gJEdZ6xbJbZZVFmvFZDCa4KcRd`)

```bash
kubectl apply -f deploy-wstunnel.yaml
```

### ingress.yaml

Configures NGINX Ingress to expose wsTunnel externally:

- **Host**: `interlink.dev.local` (update as needed)
- **Path**: `/` (all traffic)
- **Backend**: wsTunnel service on port 8080
- **Timeout Settings**: 3600s for long-lived WebSocket connections
- **WebSocket Support**: Enabled via NGINX annotations

```bash
kubectl apply -f ingress.yaml
```

> Or deploy all at once:
>
> ```bash
>  kubectl apply -f .
>  ```

### Extra/Devel configs

If you are deploying this on a local machine -- using minikube, for example --
you need to set/run some things to be able to test/reach the services.

1. Expose services through minikube (MacOS). Run on another terminal window:

    ```bash
    % minikube tunnel
    ```

2. Associate `interlink.dev.local` to `localhost` in your `/etc/hosts`:
    ```bash
    % cat /etc/hosts | grep '127'
    127.0.0.1    localhost interlin.dev.local
    ```

## HPC deployment

### wstunnel client

### interLink plugin


## Maintenance notes

### Verify Deployment

Check that all resources are created:

```bash
kubectl get pods,deploy,svc,ingress -n interlink
```

Check pod logs:

```bash
kubectl logs -n interlink -l app=interlink-api-server
kubectl logs -n interlink -l app=wstunnel-server
```

### Configuration

Update the ConfigMap before deployment to customize:

```bash
kubectl edit configmap interlink-config -n interlink
```

After editing, restart the pods:

```bash
kubectl rollout restart deployment/interlink-api-server -n interlink
kubectl rollout restart deployment/wstunnel-server -n interlink
```

### Accessing the Services

- **Locally within cluster**: Use service names directly (e.g., `http://interlink-service.interlink.svc.cluster.local:4000`)
- **Via Ingress**: Access through the configured hostname (`interlink.dev.local`)
- **Port-forward for debugging**:
  ```bash
  kubectl port-forward svc/interlink-service 4000:4000 -n interlink
  kubectl port-forward svc/wstunnel-service 8080:8080 -n interlink
  ```

### Troubleshooting

TBD

### Remote Configuration

On the HPC side, configure the wsTunnel client to connect to the exposed service endpoint and deploy the interLink plugin to communicate through the tunnel.
