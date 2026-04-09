# interLink API server — Docker image

This image runs the **interLink API server** alone — no OAuth proxy, no SLURM plugin.
It is intended to be used alongside a separately-running plugin (e.g. the SLURM plugin)
over a shared unix socket or a local TCP connection.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Image definition; downloads the interlink binary at build time |
| `interlink.yaml.tpl` | Config template; env-var placeholders are substituted at start-up |
| `entrypoint.sh` | Renders the template with `envsubst` and starts the binary |

## Build

```bash
docker build -t interlink-api .
```

To pin a specific interLink release (default: `0.5.1`):

```bash
docker build --build-arg INTERLINK_VERSION=0.5.2 -t interlink-api .
```

## Configuration

All configuration is driven by environment variables.
The `interlink.yaml.tpl` template is rendered at container start-up using
`envsubst`; the result is written to `INTERLINKCONFIGPATH`.

| Variable | Default | Description |
|---|---|---|
| `INTERLINK_ADDRESS` | `unix:///opt/interlink/run/interlink.sock` | Address the API listens on. Use `http://0.0.0.0:3000` for TCP. |
| `INTERLINK_PORT` | `0` | TCP port (set to `0` when using a unix socket). |
| `SIDECAR_ADDRESS` | `unix:///opt/interlink/run/plugin.sock` | Address of the plugin/sidecar. |
| `SIDECAR_PORT` | `0` | TCP port for the plugin (`0` for unix socket). |
| `DATA_ROOT_FOLDER` | `/opt/interlink/jobs` | Directory where job data is stored. |
| `VERBOSE_LOGGING` | `false` | Enable verbose logging. |
| `ERRORS_ONLY_LOGGING` | `false` | Log errors only. |
| `INTERLINKCONFIGPATH` | `/opt/interlink/config/interlink.yaml` | Path to the rendered config file. |

## Run examples

### Unix-socket mode (default)

The API and the plugin share a directory via a bind-mount so they can
communicate through unix sockets.

```bash
docker run --rm \
  -v /tmp/interlink-run:/opt/interlink/run \
  -v /tmp/interlink-jobs:/opt/interlink/jobs \
  interlink-api
```

### TCP mode

```bash
docker run --rm \
  -e INTERLINK_ADDRESS="http://0.0.0.0:3000" \
  -e INTERLINK_PORT=3000 \
  -e SIDECAR_ADDRESS="http://127.0.0.1:4000" \
  -e SIDECAR_PORT=4000 \
  -p 3000:3000 \
  interlink-api
```

## Directory layout inside the container

```
/opt/interlink/
├── bin/
│   └── interlink          # API server binary
├── run/                   # unix socket directory (can be a volume)
│   ├── interlink.sock
│   └── plugin.sock
├── jobs/                  # job data root (can be a volume)
├── config/
│   └── interlink.yaml     # rendered config (generated at start-up)
├── interlink.yaml.tpl     # config template
└── entrypoint.sh
```
