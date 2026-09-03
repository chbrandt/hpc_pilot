# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/chbrandt/hpc_pilot/compare/v0.3.10...HEAD
[v0.3.10]: https://github.com/chbrandt/hpc_pilot/compare/v0.3.9...v0.3.10