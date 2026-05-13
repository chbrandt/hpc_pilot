# InterLink’s Edge Node Scenario

Among interLink deployment possibilities, our use case is called the Edge node deployment: the HPC system is accessible through an edge-node where interLink API server will be deployed to serve the requests from the (elsewhere) K8S cluster.

Remarks & assumptions:

- The HPC system submits jobs using SLURM.
- The HPC's _edge-node_ can submit jobs (through Slurm).
- AuthN/AuthZ is done by an external OAuth/OIDC server, EGI Check-in in our case.

## Computing resources

We have two clusters for this experiment:

- an HPC cluster (deployed in Tubitak):

  - IP: <public-IP> 
  - Sudo user: cloudadm
  - Slurm user: ubuntu
  - InterLink port: <public-port>

- a K8S cluster (deployed in Tubitak):
  - IP: <public-IP>
  - Sudo user: cloudadm

## Setup overview

There are three components we need to set up:

- InterLink Server: on the HPC side, in the edge-node, composed by three parts:

  - an OAuth proxy
  - the interLink API
  - interLink plugin

- AuthN/AuthZ: OAuth/OIDC protocol is used to authenticate and authorize the
  interaction between components. AuthN/AuthZ has the components itself:

  - an OAuth/OIDC server/client duo somewhere in the Web: EGI Check-in/`oidc-agent`, in our case
  - in the HPC: an OAuth proxy (i.e., oauth2-proxy)
  - in the K8S: a refresh token

- InterLink Node: on the K8S side, interLink comes as a _virtual node_
  deploying multiple pods:
  - refresh-token pod: in possession of a Check-in's refresh token, continuously
    renews access tokens used to communicate with interLink server.

The setup of those components is provided in the following documents:

- [AuthN/AuthZ](setup_oauth.md)
- [HPC](setup_edge.md)
- [K8S](setup_k8s.md)
