# HPC Pilot — Kubernetes Pod Deployer

A minimalist web application for deploying pods to a Kubernetes cluster.
The app runs **outside** the cluster and connects via `kubeconfig`.

## Features

- **Deploy Pods** — Simple form with full options: name, image, namespace, resources, env vars, ports, command
- **Create Namespaces** — Define new namespaces directly from the form
- **External Access** — Automatically creates a NodePort Service when a port is specified
- **Pod Management** — List all deployed pods with status, filter by namespace, delete pods
- **Auto-refresh** — Status page polls for pod readiness updates

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set kubeconfig (optional — defaults to ~/.kube/config)
export KUBECONFIG=/path/to/your/kubeconfig

# 3. Run the app
python app.py

# 4. Open in browser
# → http://localhost:5000
```

## Configuration

| Environment Variable | Default          | Description                           |
| -------------------- | ---------------- | ------------------------------------- |
| `KUBECONFIG`         | `~/.kube/config` | Path to kubeconfig file               |
| `FLASK_PORT`         | `5000`           | Port for the web server               |
| `FLASK_DEBUG`        | `0`              | Set to `1` for debug mode             |
| `FLASK_SECRET_KEY`   | `dev-secret-...` | Secret key for session/flash messages |

## Form Fields

| Field                 | Required | Description                                                     |
| --------------------- | -------- | --------------------------------------------------------------- |
| Pod Name              | ✅       | Must follow K8s naming rules (lowercase, alphanumeric, hyphens) |
| Container Image       | ✅       | Docker image reference (e.g., `nginx:latest`)                   |
| Namespace             | —        | Select existing or create new                                   |
| CPU Request/Limit     | —        | e.g., `100m`, `0.5`, `1`                                        |
| Memory Request/Limit  | —        | e.g., `64Mi`, `256Mi`, `1Gi`                                    |
| Environment Variables | —        | Key-value pairs                                                 |
| Container Port        | —        | If set, a NodePort Service is created for external access       |
| Command Override      | —        | Executed as `/bin/sh -c "your command"`                         |

## Project Structure

```
webapp/
├── app.py              # Flask application (routes, form handling)
├── k8s_client.py       # Kubernetes client wrapper
├── requirements.txt    # Python dependencies
├── README.md
├── templates/
│   ├── base.html       # Base template (nav, flash messages)
│   ├── index.html      # Pod deployment form
│   ├── status.html     # Deployment result page
│   └── pods.html       # Pod listing page
└── static/
    └── style.css       # Minimal CSS styling
```

## How It Works

1. User fills the deployment form and submits
2. Flask backend validates input
3. If a new namespace is needed, it's created first
4. A Pod is created via the Kubernetes Python client
5. If a port is specified, a NodePort Service is also created
6. User is redirected to a status page showing the result
7. The status page auto-refreshes until the pod is Running

## Notes

- Only pods created by this app (labeled `created-by=hpc-pilot-webapp`) appear in the pod list
- The app requires `kubectl` access via kubeconfig — no in-cluster auth
- No authentication is built in; this is meant for operators with cluster access
