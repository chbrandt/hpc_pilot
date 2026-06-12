# InterLink setup 3/3

## K8S Virtual Kubelet

The Kubernetes node setup is simpler, essentially the deployment of a Helm chart.
InterLink's Helm chart will create a virtual node -- aka, virtual kubelet (vk);
We will check and write some settings, and that's pretty much about it.

Requirements:

- an account with permissions to manage the k8s cluster and deploy helm charts
  (ie, "su" rights to run `helm` and `kubectl`)

### Helm Chart Repo

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

### Chart Values

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

Check the list of helm installs for your `virtual-node` (or however you called),
you should get something like:

```bash
% helm list -n interlink
NAME          NAMESPACE REVISION  UPDATED             STATUS    CHART           APP VERSION
virtual-node  interlink 3         2025-11-07 ... UTC  deployed  interlink-0.5.3 0.3.5
```

### Check setup

At this point, you should see the virtual _node_ (which I called 'my-vk-node'
in the above `values.yaml`) in the list of nodes:

```bash
% kubectl get nodes
NAME                     STATUS   ROLES           AGE   VERSION
kubeserver.localdomain   Ready    control-plane   54d   v1.32.4
my-vk-node               Ready    agent           54d   0.5.3-pre1
vnode-1.localdomain      Ready    <none>          54d   v1.32.4
...
```

In the list of deployments, we see the service operating interLink's:

```bash
% kubectl get deployment -n interlink
NAME              READY   UP-TO-DATE   AVAILABLE   AGE
my-vk-node-node   1/1     1            1           47d
```

And the pod that handles interLink data/communication to/from the API:

```bash
% kubectl get pods -n interlink
NAME                              READY   STATUS      RESTARTS        AGE
my-vk-node-node-98547ddc5-p6dbv   2/2     Running     1 (5d19h ago)   5d19h
```

#### Test Pod

If everything looks good -- ie, the above commands ran succesfully and you
see similar (components/status) output -- we can try running a test pod.

The test pod defines a container to run in the HPC cluster.

```bash
% cat podtest.yaml

apiVersion: v1
kind: Pod
metadata:
  name: test-my-vk-node
  namespace: interlink
spec:
  nodeSelector:
    kubernetes.io/hostname: my-vk-node
  tolerations:
    - key: virtual-node.interlink/no-schedule
      operator: Exists
  containers:
  - name: test
    image: busybox:1.35
    command: ["sh", "-c"]
    args: ["echo 'Hello from virtual node my-vk-node!' && sleep 300"]
    resources:
      requests:
        memory: "100M"
        cpu: "1"
      limits:
        memory: "200M"
        cpu: "1"
  restartPolicy: Never
```

Run the pod:

```bash
% kubectl apply -f podtest.yaml
```

Running this pod should/will create a job in the HPC cluster that will generate
`Hello from virtual node my-vk-node!` as output, and complete succesfully
after `300` seconds.

On this side, the pod will inform `Running` during the (SLURM) job execution,
`Error` if the job failed, `Completed` if everything went well:

```bash
% kubectl get pods test-my-vk-node -n interlink
NAME              READY   STATUS      RESTARTS   AGE
test-my-vk-node   0/1     Completed   0          5d18h
```
