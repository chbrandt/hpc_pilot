#!/bin/sh
# entrypoint.sh – render the interLink config template and start the API server.
set -e

TEMPLATE_PATH="${TEMPLATE_PATH:-/opt/interlink/interlink.yaml.tpl}"
CONFIG_PATH="${INTERLINKCONFIGPATH:-/opt/interlink/config/interlink.yaml}"

# Ensure the config directory exists (it may live on a mounted volume).
mkdir -p "$(dirname "$CONFIG_PATH")"

# Ensure the run directory exists (for unix sockets).
mkdir -p /opt/interlink/run

# Ensure the jobs directory exists.
mkdir -p "${DATA_ROOT_FOLDER:-/opt/interlink/jobs}"

echo "Rendering config from template: $TEMPLATE_PATH -> $CONFIG_PATH"
envsubst <"$TEMPLATE_PATH" >"$CONFIG_PATH"

echo "--- interlink.yaml ---"
cat "$CONFIG_PATH"
echo "----------------------"

echo "Starting interLink API server..."
exec env INTERLINKCONFIGPATH="$CONFIG_PATH" /opt/interlink/bin/interlink
