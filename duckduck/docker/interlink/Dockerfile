# Dockerfile – interLink API server (no OAuth proxy, no plugin)
#
# Build arguments:
#   INTERLINK_VERSION  – release tag to download (default: 0.5.1)
#
# Runtime environment variables (all optional, see interlink.yaml.tpl):
#   INTERLINK_ADDRESS   – API listen address  (default: unix socket)
#   INTERLINK_PORT      – API TCP port        (default: 0 / unix socket)
#   SIDECAR_ADDRESS     – plugin address      (default: unix socket)
#   SIDECAR_PORT        – plugin TCP port     (default: 0 / unix socket)
#   DATA_ROOT_FOLDER    – job data directory  (default: /opt/interlink/jobs)
#   VERBOSE_LOGGING     – verbose logging     (default: false)
#   ERRORS_ONLY_LOGGING – errors-only logging (default: false)
#   INTERLINKCONFIGPATH – path for the rendered config file
#                         (default: /opt/interlink/config/interlink.yaml)

ARG INTERLINK_VERSION=0.5.1

# ---------------------------------------------------------------------------
FROM debian:12-slim

ARG INTERLINK_VERSION
ENV INTERLINK_VERSION=${INTERLINK_VERSION}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Download the interLink API server binary.
RUN mkdir -p /opt/interlink/bin && \
    curl --fail -L \
    -o /opt/interlink/bin/interlink \
    "https://github.com/interlink-hq/interLink/releases/download/${INTERLINK_VERSION}/interlink_Linux_x86_64" && \
    chmod +x /opt/interlink/bin/interlink

# Copy the config template and entrypoint script.
COPY interlink.yaml.tpl /opt/interlink/interlink.yaml.tpl
COPY entrypoint.sh      /opt/interlink/entrypoint.sh
RUN chmod +x /opt/interlink/entrypoint.sh

# ---------------------------------------------------------------------------
# Default environment (mirrors the defaults in interlink.yaml.tpl)
ENV INTERLINK_ADDRESS="unix:///opt/interlink/run/interlink.sock" \
    INTERLINK_PORT=0 \
    SIDECAR_ADDRESS="unix:///opt/interlink/run/plugin.sock" \
    SIDECAR_PORT=0 \
    DATA_ROOT_FOLDER="/opt/interlink/jobs" \
    VERBOSE_LOGGING="false" \
    ERRORS_ONLY_LOGGING="false" \
    INTERLINKCONFIGPATH="/opt/interlink/config/interlink.yaml"

# Expose a port only when running in TCP mode (override INTERLINK_ADDRESS /
# INTERLINK_PORT at runtime to use it).
EXPOSE 3000

ENTRYPOINT ["/opt/interlink/entrypoint.sh"]
