#!/bin/bash
#
# InterLink HPC Edge Node Setup Script
# 
# This script automates the setup of InterLink components on the HPC edge node:
# - OAuth2 Proxy (for authentication)
# - InterLink API Server
# - InterLink SLURM Plugin
#
# Usage:
#   ./edgenode_setup.sh [OPTIONS]
#
# Options:
#   --public-ip IP          Edge node public IP address (required)
#   --public-port PORT      Public port for OAuth proxy (default: 33333)
#   --checkin-sub SUB       Check-in user sub for OAuth (required)
#   --help                  Show this help message
#
# Example:
#   ./edgenode_setup.sh --public-ip 192.168.1.100 --checkin-sub "user-sub-id"
#

set -e  # Exit on error

#===============================================================================
# CONFIGURATION VARIABLES
#===============================================================================

# Default values
IL_PUBLIC_IP="${IL_PUBLIC_IP:-}"
IL_PUBLIC_PORT="${IL_PUBLIC_PORT:-33333}"
IL_CHECKIN_SUB="${IL_CHECKIN_SUB:-}"

# Directory structure
IL_DIR="${IL_DIR:-$HOME/.interlink}"
IL_DIR_BIN="$IL_DIR/bin"
IL_DIR_LOGS="$IL_DIR/logs"
IL_DIR_CONFIG="$IL_DIR/config"

# Socket paths
IL_SOCKET="${IL_DIR}/.interlink.sock"
IL_SOCKET_PG="${IL_DIR}/.plugin.sock"

# Download URLs
OAUTH2_PROXY_URL="https://github.com/dciangot/oauth2-proxy/releases/download/v0.0.3/oauth2-proxy_Linux_amd64"
INTERLINK_URL="https://github.com/interlink-hq/interLink/releases/download/0.5.1/interlink_Linux_x86_64"
PLUGIN_URL="https://github.com/interlink-hq/interlink-slurm-plugin/releases/download/0.5.2-patch1/interlink-sidecar-slurm_Linux_x86_64"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

#===============================================================================
# HELPER FUNCTIONS
#===============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    head -n 20 "$0" | grep "^#" | sed 's/^# \?//'
    exit 0
}

check_requirements() {
    log_info "Checking requirements..."
    
    # # Check if running as the correct user
    # if [ "$USER" != "interlink" ]; then
    #     log_warn "This script should ideally be run as user 'interlink'"
    #     log_warn "Current user: $USER"
    #     read -p "Continue anyway? (y/n) " -n 1 -r
    #     echo
    #     if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    #         exit 1
    #     fi
    # fi
    
    # Check for required commands
    local required_commands=("curl" "openssl" "sbatch" "scancel" "squeue")
    for cmd in "${required_commands[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            log_error "Required command not found: $cmd"
            exit 1
        fi
    done
    
    log_info "All requirements met"
}

#===============================================================================
# SETUP FUNCTIONS
#===============================================================================

setup_directories() {
    log_info "Setting up directory structure..."
    
    mkdir -p "$IL_DIR_BIN"
    mkdir -p "$IL_DIR_CONFIG"
    mkdir -p "$IL_DIR_LOGS"
    mkdir -p "$IL_DIR/jobs"
    
    log_info "Directories created at $IL_DIR"
}

setup_ssl_certificate() {
    log_info "Creating SSL certificate..."
    
    if [ -f "${IL_DIR_CONFIG}/tls.crt" ] && [ -f "${IL_DIR_CONFIG}/tls.key" ]; then
        log_warn "SSL certificate already exists"
        read -p "Regenerate certificate? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi
    
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
        -keyout "${IL_DIR_CONFIG}/tls.key" \
        -out "${IL_DIR_CONFIG}/tls.crt" \
        -subj "/CN=interlink.demo" \
        -addext "subjectAltName=IP:${IL_PUBLIC_IP}"
    
    log_info "SSL certificate created"
}

download_oauth2_proxy() {
    log_info "Downloading OAuth2 proxy..."
    
    if [ -f "${IL_DIR_BIN}/oauth2-proxy" ]; then
        log_warn "OAuth2 proxy binary already exists"
    else
        curl --fail -L -o "${IL_DIR_BIN}/oauth2-proxy" "$OAUTH2_PROXY_URL"
        chmod +x "${IL_DIR_BIN}/oauth2-proxy"
        log_info "OAuth2 proxy downloaded"
    fi
}

download_interlink() {
    log_info "Downloading InterLink API..."
    
    if [ -f "${IL_DIR_BIN}/interlink" ]; then
        log_warn "InterLink binary already exists"
    else
        curl --fail -L -o "${IL_DIR_BIN}/interlink" "$INTERLINK_URL"
        chmod +x "${IL_DIR_BIN}/interlink"
        log_info "InterLink API downloaded"
    fi
}

download_plugin() {
    log_info "Downloading SLURM plugin..."
    
    if [ -f "${IL_DIR_BIN}/plugin" ]; then
        log_warn "Plugin binary already exists"
    else
        curl --fail -L -o "${IL_DIR_BIN}/plugin" "$PLUGIN_URL"
        chmod +x "${IL_DIR_BIN}/plugin"
        log_info "SLURM plugin downloaded"
    fi
}

create_interlink_config() {
    log_info "Creating InterLink configuration..."
    
    cat <<EOF >"${IL_DIR_CONFIG}/interlink.yaml"
InterlinkAddress: unix://$IL_SOCKET
InterlinkPort: 0
SidecarURL: unix://$IL_SOCKET_PG
SidecarPort: 0
VerboseLogging: false
ErrorsOnlyLogging: false
DataRootFolder: $IL_DIR/jobs
EOF
    
    log_info "InterLink config created"
}

create_plugin_config() {
    log_info "Creating plugin configuration..."
    
    cat <<EOF >"$IL_DIR_CONFIG/plugin.yaml"
Socket: unix://$IL_SOCKET_PG
InterlinkPort: $IL_PUBLIC_PORT
SidecarPort: 4000
DataRootFolder: $IL_DIR/jobs
VerboseLogging: false
ErrorsOnlyLogging: false
BashPath: /bin/bash
SbatchPath: /usr/bin/sbatch
ScancelPath: /usr/bin/scancel
SqueuePath: /usr/bin/squeue
CommandPrefix: ""
SingularityPrefix: ""
EOF
    
    log_info "Plugin config created"
}

#===============================================================================
# SERVICE MANAGEMENT FUNCTIONS
#===============================================================================

start_oauth2_proxy() {
    log_info "Starting OAuth2 proxy..."
    
    # Check if already running
    if [ -f "${IL_DIR}/oauth2-proxy.pid" ]; then
        local pid=$(cat "${IL_DIR}/oauth2-proxy.pid")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warn "OAuth2 proxy already running (PID: $pid)"
            return 0
        fi
    fi
    
    ${IL_DIR_BIN}/oauth2-proxy \
        --upstream "unix://$IL_SOCKET" \
        --https-address "0.0.0.0:${IL_PUBLIC_PORT}" \
        --tls-cert-file "${IL_DIR_CONFIG}/tls.crt" \
        --tls-key-file "${IL_DIR_CONFIG}/tls.key" \
        --allowed-group "$IL_CHECKIN_SUB" \
        --client-id oidc-agent \
        --client-secret '""' \
        --provider oidc \
        --oidc-groups-claim sub \
        --oidc-audience-claim azp \
        --oidc-extra-audience oidc-agent \
        --oidc-issuer-url 'https://aai.egi.eu/auth/realms/egi' \
        --validate-url 'https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/token' \
        --cookie-secret 'RANDOM_VALUES_FOR_SESSION_SECRET' \
        --redirect-url 'http://localhost:8081' \
        --pass-authorization-header true \
        --skip-auth-route="*='*'" \
        --email-domain='*' \
        --force-https \
        --tls-cipher-suite=TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,TLS_RSA_WITH_AES_128_CBC_SHA,TLS_RSA_WITH_AES_128_GCM_SHA256,TLS_RSA_WITH_AES_256_CBC_SHA,TLS_RSA_WITH_AES_256_GCM_SHA384 \
        --skip-jwt-bearer-tokens true \
        >"${IL_DIR_LOGS}/oauth2-proxy.log" 2>&1 &
    
    echo $! >"${IL_DIR}/oauth2-proxy.pid"
    log_info "OAuth2 proxy started (PID: $!)"
}

start_interlink() {
    log_info "Starting InterLink API..."
    
    # Check if already running
    if [ -f "${IL_DIR}/interlink.pid" ]; then
        local pid=$(cat "${IL_DIR}/interlink.pid")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warn "InterLink API already running (PID: $pid)"
            return 0
        fi
    fi
    
    INTERLINKCONFIGPATH="${IL_DIR_CONFIG}/interlink.yaml" \
        ${IL_DIR_BIN}/interlink &>"${IL_DIR_LOGS}/interlink.log" &
    
    echo $! >"${IL_DIR}/interlink.pid"
    log_info "InterLink API started (PID: $!)"
}

start_plugin() {
    log_info "Starting SLURM plugin..."
    
    # Check if already running
    if [ -f "${IL_DIR}/plugin.pid" ]; then
        local pid=$(cat "${IL_DIR}/plugin.pid")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warn "Plugin already running (PID: $pid)"
            return 0
        fi
    fi
    
    SLURMCONFIGPATH="$IL_DIR_CONFIG/plugin.yaml" \
        $IL_DIR_BIN/plugin &> "$IL_DIR_LOGS/plugin.log" &
    
    echo $! >"${IL_DIR}/plugin.pid"
    log_info "SLURM plugin started (PID: $!)"
}

#===============================================================================
# TESTING FUNCTION
#===============================================================================

test_installation() {
    log_info "Testing InterLink installation..."
    
    # Wait a moment for services to start
    sleep 2
    
    # Test the socket connection
    if curl -s --unix-socket "$IL_SOCKET" http://unix/pinglink > /dev/null 2>&1; then
        log_info "InterLink API test successful!"
        log_info "Running detailed test..."
        curl -v --unix-socket "$IL_SOCKET" http://unix/pinglink
    else
        log_error "InterLink API test failed"
        log_info "Check logs in $IL_DIR_LOGS/"
        return 1
    fi
}

#===============================================================================
# MAIN EXECUTION
#===============================================================================

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --public-ip)
                IL_PUBLIC_IP="$2"
                shift 2
                ;;
            --public-port)
                IL_PUBLIC_PORT="$2"
                shift 2
                ;;
            --checkin-sub)
                IL_CHECKIN_SUB="$2"
                shift 2
                ;;
            --help|-h)
                show_help
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                ;;
        esac
    done
    
    # Validate required parameters
    if [ -z "$IL_PUBLIC_IP" ]; then
        log_error "Public IP address is required (--public-ip)"
        exit 1
    fi
    
    if [ -z "$IL_CHECKIN_SUB" ]; then
        log_error "Check-in user sub is required (--checkin-sub)"
        exit 1
    fi
}

main() {
    echo "=========================================="
    echo "InterLink HPC Edge Node Setup"
    echo "=========================================="
    echo
    
    parse_arguments "$@"
    
    log_info "Configuration:"
    log_info "  Public IP:      $IL_PUBLIC_IP"
    log_info "  Public Port:    $IL_PUBLIC_PORT"
    log_info "  Check-in Sub:   $IL_CHECKIN_SUB"
    log_info "  Install Dir:    $IL_DIR"
    echo
    
    check_requirements
    echo
    
    # Setup phase
    log_info "=== SETUP PHASE ==="
    setup_directories
    setup_ssl_certificate
    download_oauth2_proxy
    download_interlink
    download_plugin
    create_interlink_config
    create_plugin_config
    echo
    
    # Service start phase
    log_info "=== STARTING SERVICES ==="
    start_plugin
    sleep 1
    start_interlink
    sleep 1
    start_oauth2_proxy
    echo
    
    # Testing phase
    log_info "=== TESTING INSTALLATION ==="
    test_installation
    echo
    
    echo "=========================================="
    log_info "Setup complete!"
    echo "=========================================="
    echo
    echo "Service PIDs stored in:"
    echo "  - ${IL_DIR}/oauth2-proxy.pid"
    echo "  - ${IL_DIR}/interlink.pid"
    echo "  - ${IL_DIR}/plugin.pid"
    echo
    echo "Logs available in:"
    echo "  - ${IL_DIR_LOGS}/oauth2-proxy.log"
    echo "  - ${IL_DIR_LOGS}/interlink.log"
    echo "  - ${IL_DIR_LOGS}/plugin.log"
    echo
    echo "To test the API:"
    echo "  curl -v --unix-socket $IL_SOCKET http://unix/pinglink"
    echo
}

# Run main function
main "$@"
