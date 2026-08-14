#!/bin/bash
#
# HPC Pilot — Remote HPC Node Setup Script  (DEPRECATED)
#
# ⚠ DEPRECATED: This script is no longer executed by the manager. The HPC
#   node setup is now performed step-by-step from Python by the step functions
#   in manager/lib/hpc_client.py (setup_directories, install_supervisord,
#   install_wstunnel, install_plugin, copy_supervisord_conf,
#   start_supervisord, …) over mccli/SSH. It is kept here for reference only.
#
# Historically, this script was executed on the remote HPC login node via
# mccli (motley-cue SSH client), piped through stdin by hpc_client.py:
#
#   mccli --token $TOKEN ssh -p $PORT $HOST 'bash -s' < setup.sh
#
# The script installs and configures:
#   1. wstunnel  — WebSocket tunnel client (connects back to the K8s cluster)
#   2. supervisord — user-space process supervisor (manages wstunnel)
#
# Required environment variables (set by hpc_client.py as a shell prefix):
#   WSTUNNEL_SERVER     – hostname of the K8s-side wstunnel server
#   WSTUNNEL_PORT       – port on the wstunnel server (e.g. 8420)
#   WSTUNNEL_SECRET     – shared bearer-token / tunnel secret
#   WSTUNNEL_LOCAL_PORT – local TCP port to expose on this HPC node
#
# The script is idempotent: re-running it will upgrade binaries and
# regenerate configs without breaking a running supervisor.

set -euo pipefail

#===============================================================================
# CONFIGURATION
#===============================================================================

HPC_DIR="${HOME}/.hpc-pilot"
BIN_DIR="${HPC_DIR}/bin"
LOG_DIR="${HPC_DIR}/logs"
CONF_DIR="${HPC_DIR}/config"
SUPERVISORD_CONF="${HPC_DIR}/supervisord.conf"
SUPERVISOR_SOCK="${HPC_DIR}/supervisor.sock"
SUPERVISOR_PID="${HPC_DIR}/supervisord.pid"

# wstunnel release to download (Linux amd64 static binary from GitHub)
WSTUNNEL_VERSION="${WSTUNNEL_VERSION:-v10.1.0}"
WSTUNNEL_URL="https://github.com/erebe/wstunnel/releases/download/${WSTUNNEL_VERSION}/wstunnel_${WSTUNNEL_VERSION#v}_linux_amd64.tar.gz"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

#===============================================================================
# HELPERS
#===============================================================================

log_info()  { printf "%b\n" "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { printf "%b\n" "${YELLOW}[WARN]${NC}  $*"; }
log_error() { printf "%b\n" "${RED}[ERROR]${NC} $*"; }
log_step()  { printf "%b\n" "${BLUE}[STEP]${NC}  $*"; }

die() { log_error "$*"; exit 1; }

#===============================================================================
# VALIDATION
#===============================================================================

validate_env() {
    log_step "Validating required environment variables..."

    : "${WSTUNNEL_SERVER:?WSTUNNEL_SERVER is not set}"
    : "${WSTUNNEL_PORT:?WSTUNNEL_PORT is not set}"
    : "${WSTUNNEL_SECRET:?WSTUNNEL_SECRET is not set}"
    : "${WSTUNNEL_LOCAL_PORT:?WSTUNNEL_LOCAL_PORT is not set}"

    log_info "  WSTUNNEL_SERVER     = ${WSTUNNEL_SERVER}"
    log_info "  WSTUNNEL_PORT       = ${WSTUNNEL_PORT}"
    log_info "  WSTUNNEL_SECRET     = ${WSTUNNEL_SECRET:0:8}..."
    log_info "  WSTUNNEL_LOCAL_PORT = ${WSTUNNEL_LOCAL_PORT}"
}

#===============================================================================
# DIRECTORY SETUP
#===============================================================================

setup_directories() {
    log_step "Creating HPC Pilot directories..."
    mkdir -p "${BIN_DIR}" "${LOG_DIR}" "${CONF_DIR}"
    log_info "  Base dir: ${HPC_DIR}"
}

#===============================================================================
# WSTUNNEL INSTALLATION
#===============================================================================

install_wstunnel() {
    log_step "Installing wstunnel ${WSTUNNEL_VERSION}..."

    if [[ -x "${BIN_DIR}/wstunnel" ]]; then
        local installed_ver
        installed_ver=$("${BIN_DIR}/wstunnel" --version 2>/dev/null | head -1 || echo "unknown")
        log_warn "  wstunnel already installed: ${installed_ver}"
        log_warn "  Skipping download (delete ${BIN_DIR}/wstunnel to force reinstall)"
        return 0
    fi

    # Require curl
    command -v curl >/dev/null 2>&1 || die "curl is required but not found"

    log_info "  Downloading from: ${WSTUNNEL_URL}"
    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "${tmpdir}"' RETURN

    curl --fail --silent --show-error -L \
        -o "${tmpdir}/wstunnel.tar.gz" \
        "${WSTUNNEL_URL}" \
        || die "Failed to download wstunnel"

    tar -xzf "${tmpdir}/wstunnel.tar.gz" -C "${tmpdir}" \
        || die "Failed to extract wstunnel archive"

    # The binary may be named 'wstunnel' directly or inside a sub-dir
    local wstunnel_bin
    wstunnel_bin=$(find "${tmpdir}" -name "wstunnel" -type f | head -1)
    [[ -n "${wstunnel_bin}" ]] || die "wstunnel binary not found in archive"

    install -m 0755 "${wstunnel_bin}" "${BIN_DIR}/wstunnel"
    log_info "  wstunnel installed at ${BIN_DIR}/wstunnel"
}

#===============================================================================
# SUPERVISORD INSTALLATION
#===============================================================================

install_supervisord() {
    log_step "Installing supervisord..."

    if command -v supervisord >/dev/null 2>&1; then
        log_info "  System supervisord found: $(command -v supervisord)"
        # Ensure supervisorctl is also available
        return 0
    fi

    if command -v pip3 >/dev/null 2>&1; then
        log_info "  Installing via pip3 (--user)..."
        pip3 install --quiet --user supervisor \
            || die "pip3 install supervisor failed"
    elif command -v pip >/dev/null 2>&1; then
        log_info "  Installing via pip (--user)..."
        pip install --quiet --user supervisor \
            || die "pip install supervisor failed"
    else
        die "Neither pip3 nor pip found. Cannot install supervisord."
    fi

    # pip --user installs into ~/.local/bin; make sure it is on PATH
    export PATH="${HOME}/.local/bin:${PATH}"

    command -v supervisord >/dev/null 2>&1 \
        || die "supervisord still not on PATH after pip install. \
Add ~/.local/bin to your PATH."

    log_info "  supervisord installed: $(command -v supervisord)"
}

#===============================================================================
# SUPERVISORD CONFIGURATION
#===============================================================================

write_supervisord_conf() {
    log_step "Writing supervisord configuration..."

    # Resolve supervisord binary location for the [supervisorctl] section
    local supervisord_bin
    supervisord_bin=$(command -v supervisord)

    cat > "${SUPERVISORD_CONF}" <<CONF
; supervisord.conf — HPC Pilot managed services
; Generated by manager/hpc/setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

[unix_http_server]
file=${SUPERVISOR_SOCK}

[supervisord]
nodaemon=false
logfile=${LOG_DIR}/supervisord.log
logfile_maxbytes=10MB
logfile_backups=3
loglevel=info
pidfile=${SUPERVISOR_PID}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://${SUPERVISOR_SOCK}

; ── wstunnel client ──────────────────────────────────────────────────────────
; Tunnels back to the wstunnel server in the user's Kubernetes namespace.
[program:wstunnel]
command=${BIN_DIR}/wstunnel client
    --local-to-remote tcp://0.0.0.0:${WSTUNNEL_LOCAL_PORT}:localhost:${WSTUNNEL_LOCAL_PORT}
    -H "authorization: Bearer ${WSTUNNEL_SECRET}"
    wss://${WSTUNNEL_SERVER}:${WSTUNNEL_PORT}
directory=${HPC_DIR}
environment=HOME="${HOME}"
autostart=true
autorestart=true
startretries=5
startsecs=3
stdout_logfile=${LOG_DIR}/wstunnel-stdout.log
stderr_logfile=${LOG_DIR}/wstunnel-stderr.log
stdout_logfile_maxbytes=5MB
stderr_logfile_maxbytes=5MB
CONF

    log_info "  Config written to ${SUPERVISORD_CONF}"
}

#===============================================================================
# START / RESTART SUPERVISORD
#===============================================================================

start_supervisord() {
    log_step "Starting supervisord..."

    # Ensure ~/.local/bin is on PATH (pip --user installs here)
    export PATH="${HOME}/.local/bin:${PATH}"

    # If supervisord is already running with our config, reload it
    if [[ -f "${SUPERVISOR_PID}" ]]; then
        local pid
        pid=$(cat "${SUPERVISOR_PID}")
        if kill -0 "${pid}" 2>/dev/null; then
            log_info "  supervisord already running (PID ${pid}); reloading config..."
            supervisorctl -c "${SUPERVISORD_CONF}" reread  >/dev/null 2>&1 || true
            supervisorctl -c "${SUPERVISORD_CONF}" update  >/dev/null 2>&1 || true
            supervisorctl -c "${SUPERVISORD_CONF}" restart all 2>&1 || true
            log_info "  Config reloaded and services restarted"
            return 0
        fi
    fi

    supervisord -c "${SUPERVISORD_CONF}" \
        || die "Failed to start supervisord"

    # Give it a moment to initialize
    sleep 2

    if [[ -S "${SUPERVISOR_SOCK}" ]]; then
        log_info "  supervisord started successfully"
    else
        die "supervisord socket not found after startup. Check ${LOG_DIR}/supervisord.log"
    fi
}

#===============================================================================
# STATUS CHECK
#===============================================================================

check_status() {
    log_step "Checking service status..."
    export PATH="${HOME}/.local/bin:${PATH}"
    supervisorctl -c "${SUPERVISORD_CONF}" status 2>&1 || true
}

#===============================================================================
# MAIN
#===============================================================================

main() {
    echo "=========================================="
    echo " HPC Pilot — Remote Node Setup"
    echo "=========================================="
    echo

    validate_env
    echo

    setup_directories
    echo

    install_wstunnel
    echo

    install_supervisord
    echo

    write_supervisord_conf
    echo

    start_supervisord
    echo

    check_status
    echo

    echo "=========================================="
    log_info "Setup complete!"
    echo "=========================================="
    echo
    echo "Installation directory : ${HPC_DIR}"
    echo "wstunnel binary        : ${BIN_DIR}/wstunnel"
    echo "supervisord config     : ${SUPERVISORD_CONF}"
    echo "Logs                   : ${LOG_DIR}/"
    echo
    echo "Tunnel endpoint (on this node):"
    echo "  tcp://0.0.0.0:${WSTUNNEL_LOCAL_PORT}"
    echo
    echo "To manage services manually:"
    echo "  supervisorctl -c ${SUPERVISORD_CONF} status"
    echo "  supervisorctl -c ${SUPERVISORD_CONF} stop wstunnel"
    echo "  supervisorctl -c ${SUPERVISORD_CONF} start wstunnel"
    echo
}

main "$@"
