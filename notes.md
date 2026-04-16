## Setup interLink (K8s+HPC)

### 1. Deploy interLink + virtual-kubelet

```bash
% cd charts
% pwd
~/hpc_pilot/charts
```
```
% IL_CHART_VALUES='vk-interlink.yaml'
% cat $IL_CHART_VALUES
nodeName: vk-interlink

interlink:
  enabled: true
  address: http://localhost
  port: 4000

plugin:
  address: http://wstunnel.interlink.svc.cluster.local
  port: 3000
```

```bash
% IL_CHART_VERSION='0.6.1'
% helm install --create-namespace -n interlink interlink-deployment \
    oci://ghcr.io/interlink-hq/interlink-helm-chart/interlink --version $IL_CHART_VERSION \
    --values $IL_CHART_VALUES
```

This should have deployed a pod with virtual-kubelet and interLink API server in it (`vk` and `interlink` containers, resp.)

### 2. Deploy wstunnel + ingress

```bash
% cd interlink-wstunnel
% kubectl apply -f deploy-wstunnel.yaml
deployment.apps/wstunnel-server created
service/wstunnel created
% kubectl apply -f ingress.yaml
ingress.networking.k8s.io/wstunnel-ingress created
```