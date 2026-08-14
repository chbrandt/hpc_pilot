# InterLink Pod

[helm-chart]: https://github.com/chbrandt/interlink-helm-chart/tree/pilot

The interLink pod is provide by [interlink-helm-chart][helm-chart],
which deploys the interLink API server and the virtual-kubelet (node), and
possibly other components.
In our case, we also deploy the wstunnel server container to provide secure
connection between interLink plugin (HPC) and interLink API server (Cloud).

## Installation

To deploy interLink (pod), [Helm](https://helm.sh/) is required.
The provided `[interlink/values.yaml](./interlink/values.yaml)` file contains
the default configuration for our interLink deployment.

Here's an example configuration for the interLink pod (this mirrors the
defaults the manager injects per user from `charts_config.yaml`):

```yaml
nodeName: virtual-node-user-<hash>  # unique per namespace

interlink:
  enabled: true             # deploys interLink API server container
  address: http://0.0.0.0   # address for interLink API server to listen on
  port: 3000                # port for interLink API server to listen on

plugin:
  # Disable deployment of interLink plugin container. 
  enabled: false 
  # It will be deployed separately on HPC edge-node, and communication
  # will be established through wstunnel on "address" (localhost/0.0.0.0) 
  # and specified "port" (e.g., 4000).
  address: http://0.0.0.0   
  port: 4000 

wstunnel:
  # Enable deployment of wstunnel server container in the interLink pod.
  enabled: true
  # Port the wstunnel server listens on inside the pod.
  port: 8080
  # Ingress exposing the wstunnel server (path-prefix routing on the shared
  # hostname; no wildcard DNS required).
  ingress:
    host: app.example.com   # __HOSTNAME__ placeholder, resolved per user
  # Secret value for wstunnel authentication, must match the secret used 
  # by wstunnel client (--http-upgrade-path-prefix "<secret>").
  # In HPC Pilot this is the user's namespace (the __NAMESPACE__ placeholder).
  secret: "user-<hash>"
  logLevel: debug
```

Deploy it with Helm:

```bash
helm install interlink \
    oci://ghcr.io/chbrandt/interlink \
    -n user-<hash> --create-namespace \
    -f values.yaml
```

This will create the interLink pod in the `user-<hash>` namespace.
You can check the status of the deployment with:

```bash
kubectl get all -n interlink
```

Should see something like:

```
NAME                               READY   STATUS    RESTARTS   AGE
pod/vk-node-node-98fbc58d4-94ksp   4/4     Running   0          19m

NAME               TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/wstunnel   ClusterIP   10.104.239.69   <none>        8420/TCP   19m

NAME                           READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/vk-node-node   1/1     1            1           19m

NAME                                     DESIRED   CURRENT   READY   AGE
replicaset.apps/vk-node-node-98fbc58d4   1         1         1       19m
```

And an ingress resource exposing wstunnel server
(`kubectl get ingress -n interlink`):

```
NAME       CLASS   HOSTS                 ADDRESS        PORTS   AGE
wstunnel   nginx   interlink.dev.local   192.168.49.2   80      21m
```

Which will be the address for wstunnel client to connect to (see
[../../README.md#wstunnel-client](../../README.md#wstunnel-client)
for details).
