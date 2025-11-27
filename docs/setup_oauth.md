# Interlink setup 1/3

## OAuth server

The communication between the K8S cluster and the HPC edge-node is authenticated
by an OAuth server.
This layer is composed by two components: (i) an OAuth/OICD server
(EGI Check-in, for instance), and (ii) an OAuth proxy.
The OAuth proxy sits on the edge node, its setup is placed in the
[Setup Edge-node](setup_edge.md) document. Here, we focus on setting up the
OAuth server/client setup, on generating a secrets/tokens to be used later
in the [K8S setup](setup_k8s.md).
