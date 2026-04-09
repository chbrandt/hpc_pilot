# interLink API server configuration template.
#
# This file is processed at container start-up by entrypoint.sh using
# `envsubst`.  Every ${VAR:-default} expression is replaced with the value of
# the corresponding environment variable, falling back to the given default
# when the variable is unset or empty.
#
# Environment variables (with defaults):
#
#   INTERLINK_ADDRESS   – address the API listens on
#                         (default: unix:///opt/interlink/run/interlink.sock)
#   INTERLINK_PORT      – TCP port; set to 0 when using a unix socket
#                         (default: 0)
#   SIDECAR_ADDRESS     – address of the plugin / sidecar
#                         (default: unix:///opt/interlink/run/plugin.sock)
#   SIDECAR_PORT        – TCP port for the plugin; 0 for unix socket
#                         (default: 0)
#   DATA_ROOT_FOLDER    – directory where job data is stored
#                         (default: /opt/interlink/jobs)
#   VERBOSE_LOGGING     – enable verbose logging (true/false)
#                         (default: false)
#   ERRORS_ONLY_LOGGING – log errors only (true/false)
#                         (default: false)

InterlinkAddress: "${INTERLINK_ADDRESS:-unix:///opt/interlink/run/interlink.sock}"
InterlinkPort: ${INTERLINK_PORT:-0}
SidecarURL: "${SIDECAR_ADDRESS:-unix:///opt/interlink/run/plugin.sock}"
SidecarPort: ${SIDECAR_PORT:-0}
DataRootFolder: "${DATA_ROOT_FOLDER:-/opt/interlink/jobs}"
VerboseLogging: ${VERBOSE_LOGGING:-false}
ErrorsOnlyLogging: ${ERRORS_ONLY_LOGGING:-false}
