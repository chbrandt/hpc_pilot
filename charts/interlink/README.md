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

Here's an example configuration for the interLink pod:

```yaml
nodeName: vk-node # name of the virtual-kubelet node, unique per namespace

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
  # Hostname for wstunnel server (for wstunnel client to connect to).
  host: some.example.net
  # Secret value for wstunnel authentication, must match the secret used 
  # by wstunnel client (--http-upgrade-path-prefix "<secret>").
  secret: "secret-string-for-wstunnel"
```

The interLink pod can be installed using the following command:
