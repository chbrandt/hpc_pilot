# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v0.4-pr] - 2026-09-12

### Added

- **Unified configuration** (`AA4`): `manager/pilot_config.yaml` (sections
  `site`, `charts`, `hpc.nodes`) replaces `site_config.yaml`,
  `charts_config.yaml` and `manager/hpc/*.yaml`; new `lib/config.py` loader
  with configurable path resolution.
- **`--config` flag** (`AA5`): `python main.py --config <path>` (or
  `PILOT_CONFIG_PATH`) selects a custom configuration file.
- **Userspace prune** (`US2`): `POST /api/userspace/prune` removes everything
  for the user — best-effort per-node InterLink uninstall and HPC edge
  undeploy, then namespace deletion — with a per-node status report.
- **Prune button** (`GA6`): "Prune everything" on the Manage Nodes page
  (confirmation dialog) wired to a `POST /hpc/nodes/prune` GUI route.
- **Start/Stop Node** (`GA8`, `GA9`, `GA10`): `POST /hpc/nodes/{start,stop}`
  combine the HPC and InterLink operations in one click, with a
  spinning-wheel indicator and aggregated deployment/stopping logs.
- **Pod-spec jobs** (`JT4`): `POST /api/jobs/spec` creates a job from the
  `spec` field of a Pod manifest, injecting the InterLink toleration when
  missing.
- **CPU / memory resources** (`JT2`, `GA2`): `POST /api/jobs/preset` accepts
  `cpu`/`memory` (Kubernetes quantities); the Submit Job form forwards them.
- **EGI Check-in OAuth login** (`GA4`): "Log in with EGI Check-in" button
  implementing the authorization-code flow against `aai.egi.eu` (Keycloak),
  with CSRF state verification; configurable via `site.oidc`.
- **Home landing page** (`GA1`): `GET /` lists the user's jobs and their
  interlink/HPC nodes; the Submit Job form moved to `GET /submit`.
- **Health endpoint** (`AA2`): public `GET /health` returning
  `{"status": "Service alive"}`.
- **API Docs link** (`GA11`): navbar button opening the Swagger UI in a new
  tab.
- **`GET /api/interlink/nodes`** (`IT2`): renamed from
  `/api/nodes/interlink`, grouped under the interLink resource namespace.

### Changed

- **Jobs API** (`JT3`): job creation moved to `POST /api/jobs/preset`
  (`GET /api/jobs` still lists jobs).
- **Node-name validation** (`JT1`): `node_name` is validated against the
  InterLink virtual-kubelet nodes deployed in the cluster.
- **Per-(user, HPC node) InterLink releases** (`AA3`, `IT1`): release
  `interlink-<hpc_name>-<user_hash>`, virtual-kubelet node
  `vk-node-<hpc_name>-<user_hash>`.
- **HPC status is a GET** (`HT1`): `/api/hpc/status` now takes `hpc_name`
  as a query parameter.
- **InterLink chart hardening**: deploy with `virtualNode.disableCSR` so job
  output works with `--kubelet-insecure-tls`; `approve_pending_csrs` matches
  the exact node-name ServiceAccount.

### Fixed

- **Jobs page shows only jobs** (`GA12`): InterLink releases/nodes are no
  longer rendered as jobs.
- **Userspace teardown** (`US1`): ClusterRole grants the `delete` verb on
  namespaces so `DELETE /api/userspace/` succeeds.

### Removed

- **`POST /api/namespaces/ensure`** (`AA1`): superseded by
  `POST/DELETE /api/userspace/`.
- **Save feature** (`GA5`): removed the save route/button for HPC and
  Helm/interLink deployments.
- **Redundant chart values** (`HC1`): wstunnel ingress host/ports/secret are
  derived from `siteConfig`; the Ingress TLS host list comes from
  `siteConfig.hostname`.
- Obsolete templates `helm.html`, `hpc.html`, `releases.html`,
  `helm_result.html`.

## [v0.3.10] - 2026-09-03

### Added

- **Public health endpoint** (`AA2`): `GET /health` returns `{"status": "Service alive"}`
  without authentication, for liveness/readiness probes. Documented in the OpenAPI
  spec and documented in `documentation/rest_api.md`.
- **Per-HPC-node InterLink virtual-kubelets** (`AA3`, `IT1`): `POST /api/interlink`
  now requires an `hpc_name` (validated against `manager/hpc/*.yaml`) and deploys
  one release (`interlink-<hpc_name>`) and one virtual-kubelet node
  (`vk-node-<user-hash>-<hpc_name>`) per (user, HPC node) pair. `GET` / `DELETE`
  `/api/interlink` accept the HPC node via query parameter / JSON body
  respectively.
- **"Manage Nodes" page** (`GA3`): `GET /hpc/nodes` merges the previous "Charts"
  and "HPC" pages into one view of every configured HPC node, its HPC-side
  actions (deploy/status/start/stop) and its InterLink deployment state, with
  `POST /hpc/nodes/interlink/{deploy,delete}` routes. The old `/helm`, `/releases`
  and `GET /hpc` routes remain as backward-compatible redirects.
- **CPU / memory job resources** (`JT2`): `POST /api/jobs/preset` accepts `cpu`
  and `memory` (Kubernetes quantities) forwarded as container requests/limits.
- **Pod-spec job submission** (`JT4`): `POST /api/jobs/spec` creates a job from
  the `spec` field of a Pod manifest, injecting the InterLink toleration when
  missing.
- **User namespace teardown** (`AA1`): `DELETE /api/userspace/` deletes the
  authenticated user's namespace and all its resources.

### Changed

- **`GET /api/hpc/status`** (`HT1`): changed from POST to GET; the HPC node is
  selected via the `hpc_name` query parameter.
- **Jobs API** (`JT3`): `POST /api/jobs` renamed to `POST /api/jobs/preset`
  (`GET /api/jobs` still lists jobs).
- **Node-name validation** (`JT1`): `POST /api/jobs/preset` rejects `node_name`
  values that do not match a deployed InterLink virtual-kubelet node.
- **InterLink chart values** (`HC1`): removed redundant `values.yaml` attributes
  (`interlinkConfig.wstunnel.ingress.host`, `externalPort`, `internalPort`,
  `secret`); the wstunnel ingress host is derived from `siteConfig.hostname`,
  and the secret from the per-user namespace. The Ingress TLS host list is now
  derived from `siteConfig.hostname` (`ingress.tls.enabled` / `secretName`).

### Removed

- **Save features for Helm/interLink deployments** (`GA5`): removed the
  `POST /releases/<name>/save` route and its "Save" button.
- **`POST /api/namespaces/ensure`** (`AA1`): superseded by `POST/DELETE
  /api/userspace/`.
- Removed the now-unused templates `helm.html`, `hpc.html`, `releases.html` and
  `helm_result.html`.

[Unreleased]: https://github.com/chbrandt/hpc_pilot/compare/v0.4-pr...HEAD
[v0.4-pr]: https://github.com/chbrandt/hpc_pilot/compare/v0.3.10...v0.4-pr
[v0.3.10]: https://github.com/chbrandt/hpc_pilot/compare/v0.3.9...v0.3.10