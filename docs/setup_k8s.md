## InterLink setup 3/3: K8S Virtual Kubelet

The Kubernetes node setup is simpler, essentially the deployment of a Helm chart.
InterLink's Helm chart will create a virtual node -- aka, virtual kubelet (vk);
We will check and write some settings, and that's pretty much about it.

Requirements:

- an account with permissions to manage the k8s cluster and deploy helm charts
  (ie, "su" rights to run `helm` and `kubectl`)

### Helm chart repo

InterLink's Helm chart is available at:

- https://github.com/interlink-hq/interlink-helm-chart

We start by adding interLink's (helm) repository:

```bash
% helm repo add interlink https://interlink-hq.github.io/interlink-helm-chart/
% helm repo update
```

> Quick check on the repository charts and latest version:
>
> ```bash
> % helm search repo interlink
> NAME               	CHART VERSION	APP VERSION	DESCRIPTION
> interlink/interlink	0.5.0        	0.3.5      	Install interLink components and initiate your ...
> ```

### Chart values

[interLink Chart values]: https://github.com/interlink-hq/interlink-helm-chart/blob/main/interlink/values.yaml

In the next step we will _install_ the chart we just fetched, but before we
want to adjust some settings.

The following settings are a subset of available [interLink Chart values][]
defaults, the ones necessary to define our use case -- _ie_, HPC edge-node.

Copy-and-paste to a local `values.yaml` file, and fill up the `<...>` spaces:

```yaml
nodeName: my-vk-node

interlink:
  address: <edge-node public IP>
  port: <edge-node port where interLink API (eg, 33333)>
  disableProjectedVolumes: true

virtualNode:
  resources:
    CPUs: <number of CPUs defining your node (eg, 10)>
    memGiB: <amount of memory in GB (eg, 16)>
    pods: <number of pods the vk can manage (eg, 10)>
  HTTPProxies:
    HTTP: null
    HTTPs: null
  HTTP:
    CACert:
    Insecure: true

OAUTH:
  enabled: true
  TokenURL: https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token
  ClientID: oidc-agent
  ClientSecret:
  RefreshToken: <EGI Check-in *refresh* token>
  GrantType: authorization_code
  Audience: oidc-agent

---
```

### Chart install

Install the chart with custom values:

```bash
% helm install --create-namespace -n interlink virtual-node \
    interlink/interlink --values 'values.yaml'
```
