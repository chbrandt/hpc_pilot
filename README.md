# HPC Pilot

Pilot builds on top of [interLink](https://interlink-project.dev)
to manage cloud-native jobs in HPC appliances for EGI communities through
[Check-in](https://www.egi.eu/service/check-in/).

![HPC Pilot Architecture](./docs/assets/pilot_architecture.png)

## Components

- Cloud
  - manager app: manages interLink pod, jobs, HPC client, Check-in integration.
  - interLink pod
    - interLink API server: provides REST API for HPC job management.
    - wstunnel server: provides secure tunneling for HPC client connections.

- HPC
  - interLink plugin: runs on HPC's edge-node on behalf of interLink API server.
  - wsTunnel client: connects to wstunnel server for API-plugin communication.

- Check-in: manages user authentication and authorization for HPC access

## CLoud

### Manager/Pilot app

The manager is a cloud application responsible for orchestrating the deployment
and management of the interLink pod, as well as handling HPC job submissions
and interactions with Check-in.
It provides a user interface for users to submit HPC jobs, monitor their status,
and retrieve results. The manager also manages the lifecycle of the interLink pod,
ensuring it is running and accessible for HPC clients. Additionally, the manager
integrates with Check-in to authenticate users and manage their access to
HPC resources.
The manager can be deployed on any cloud platform that supports
Kubernetes, and it interacts with the interLink pod through Kubernetes
APIs to manage resources and facilitate communication between the HPC
clients and the cloud environment.

- Go to [pilot/README.md](./pilot/README.md) for details.

### interLink pod

The interLink pod is deployed in the cloud and consists of the following components:

- [interLink API server](#interlink-api-server):
  A REST API server that provides endpoints for managing HPC jobs, including
  job submission, status monitoring, and result retrieval.
- [wstunnel server](#wstunnel-server):
  A secure tunneling server that allows the HPC client (e.g., interLink plugin)
  to connect to the interLink pod.

#### interLink API server

The interLink API server is a core component of the interLink pod that provides
a RESTful interface for managing HPC jobs.
It allows users to submit HPC jobs, monitor their status, and retrieve results.
The API server interacts with the [interLink plugin](#interlink-plugin)
(running on the HPC's edge-node) to schedule and manage the jobs.
The communication between the interLink API server and the interLink plugin is
facilitated through the wstunnel ([server](#wstunnel-server) and
[client](#wstunnel-client)).

#### wsTunnel server

The wstunnel server is a component of the interLink pod that provides secure
tunneling capabilities for the HPC client to connect to the interLink pod
from the HPC site.
It listens/sends for in/out connections from the wsTunnel client (on the HPC's
edge-node) and establishes a secure tunnel to facilitate communication
between the HPC client and the interLink pod.
The wstunnel server is configured to listen on a specific  port (e.g., 8080)
and can be accessed through the hostname `interlink.dev.local` .

## HPC

### interLink plugin

The interLink plugin runs on the HPC cluster and is responsible for
communicating with the interLink API server.

### wsTunnel client

The wsTunnel client is a component that runs on the HPC's edge-node and
connects to the wstunnel server in the interLink pod to create a secure tunnel.
This allows the interlink plugin to communicate with the interLink API server.

## Check-in

Check-in provides authN/authZ for EGI communities, here used to manage user
access to both the Manager app and the HPC edge-node.
