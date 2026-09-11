# Roadmap

Items to fix or improve.

Status legend:
- [x] — implemented (see CHANGELOG.md)
- [ ] — not yet implemented

---

## Implementation order

The remaining items are listed below in the recommended implementation
order, grouped by component. The ordering follows three rules:

1. **Bug fixes first** — quick wins that unblock or correct existing behaviour.
2. **Foundational API changes before GUI** — so the GUI tasks can consume
   stable endpoints.
3. **Dependencies respected** — tasks that require another task are placed
   after it (e.g. GA9/GA10 require GA8; GA6 requires the prune API action).

| # | ID | Component | Task | Priority | Depends on |
|---|----|-----------|------|----------|------------|
| 1 | GA12 | GUI | Jobs page shows interlink nodes as if they were jobs — fix to list only jobs | high | — |
| 2 | US1 | API | Fix DELETE /api/userspace: SA cannot delete namespaces (RBAC) | high | — |
| 3 | AA4 | API | Merge charts_config + site_config + hpc/*.yaml into one config file | medium | — |
| 4 | AA5 | API | Add --config flag for launching the manager with a custom config path | low | AA4 |
| 5 | GA8 | GUI | Combine Deploy HPC + Deploy interLink into one Start/Stop Node button | high | — |
| 6 | GA9 | GUI | Spinning-wheel indicator while a node is being deployed/stopped | high | GA8 |
| 7 | GA10 | GUI | Show deployment/stopping logs in Manage Nodes | high | GA8 |
| 8 | US2 | API | Add /api/userspace prune action: remove everything (K8s + HPC edge) for the user | high | US1 |
| 9 | GA6 | GUI | Prune button: remove all interlink + HPC deployments for the user | high | US2 |
| 10 | GA1 | GUI | Landing page (Home): list of Jobs and interlink/HPC Nodes deployed | high | — |
| 11 | GA4 | GUI | EGI Check-in login button (authorization-code flow via aai.egi.eu Keycloak) | high | — |
| 12 | GA2 | GUI | Update Submit Job page for JT2 (cpu/memory), JT3 (preset), JT4 (spec) | medium | — |
| 13 | GA7 | GUI | Fix container-image placeholder to show the default image from configuration | medium | — |
| 14 | IT2 | API | Rename GET /api/nodes/interlink to GET /api/interlink/nodes | low | — |
| 15 | GA11 | GUI | Add menu-bar button to open Swagger UI (API docs) in a new tab | low | — |
| 16 | CR1 | Repo | Split the codebase into API-App, GUI-App, Helm-Chart | low | — |

---

## Codebase/Repository

ID | Task | Priority | Status
-- | ---- | -------- | ------
CR1 | Split the codebase in three parts: API-App, GUI-App, Helm-Chart | low | [ ]

## Manager (Helm) Chart

ID | Task | Priority | Status
-- | ---- | -------- | ------
HC1 | Remove redundant attributes in manager/values.yaml | high | [x]

## API

ID | Task | Priority | Status
-- | ---- | -------- | ------
AA1 | Remove /api/namespaces/ensure to POST/DELETE /api/userspace/. | low | [x]
AA2 | Implement GET /health endpoint, public call, return simple JSON Service alive. | high | [x]
AA3 | Combine configuration of interlink vk-node with hpc deployment. | high | [x]
AA4 | Merge configuration attributes into one configuration file. | medium | [ ]
AA5 | Add a --config flag for launching the manager with a custom config path. | low | [ ]

### /hpc

ID | Task | Priority | Status
-- | ---- | -------- | ------
HT1 | Change /api/hpc/status from POST to GET. | medium | [x]

### /interlink

ID | Task | Priority | Status
-- | ---- | -------- | ------
IT1 | Change /api/interlink POST to require hpc_name; name vk-node as vk-node-<user-hash>-<name>. | high | [x]
IT2 | Rename (GET) /api/nodes/interlink to /api/interlink/nodes | low | [ ]

### /jobs

ID | Task | Priority | Status
-- | ---- | -------- | ------
JT1 | In /api/jobs POST, validate node name; reject invalid HPC/node. | medium | [x]
JT2 | Include cpu and memory in the attributes of /api/jobs POST. | medium | [x]
JT3 | Change POST /api/jobs to POST /api/jobs/preset. | medium | [x]
JT4 | Create a POST /api/jobs/spec, accepting a Pod manifest spec. | medium | [x]

### /userspace

ID | Task | Priority | Status
-- | ---- | -------- | ------
US1 | Fix DELETE /api/userspace: SA cannot delete namespaces (lacks RBAC). | high | [ ]
US2 | Add /api/userspace prune action: remove everything for the user, including HPC edge. | high | [ ]

## GUI

ID | Task | Priority | Status
-- | ---- | -------- | ------
GA1 | Landing page (Home): list of Jobs and interlink/HPC Nodes deployed. | high | [ ]
GA2 | Update Submit Job page for JT2 (cpu/memory), JT3 (preset), JT4 (spec). | medium | [ ]
GA3 | Merge Charts and HPC pages into one Manage Nodes. | high | [x]
GA4 | EGI Check-in login button (authorization-code flow via aai.egi.eu Keycloak). | high | [ ]
GA5 | Remove save feature/buttons for HPC and Helm/interlink deployments. | high | [x]
GA6 | Add a prune button to remove all interlink and HPC deployments for the user. | high | [ ]
GA7 | Fix Container image placeholder to show the default image from configuration. | medium | [ ]
GA8 | Combine Deploy HPC + Deploy interLink into one Start/Stop Node button. | high | [ ]
GA9 | (requires GA8) Spinning wheel to indicate node is being deployed/stopped. | high | [ ]
GA10 | (requires GA8) Present logs from deployment/stopping process. | high | [ ]
GA11 | Add menu-bar button to open API documentation (Swagger UI) in a new tab. | low | [ ]
GA12 | Bug: Jobs page shows interlink nodes as if they were jobs. Fix to show only jobs. | high | [ ]
