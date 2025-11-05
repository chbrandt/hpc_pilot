#!/bin/bash
#
# InterLink Service Management Script
#
# This script manages the InterLink services on the HPC edge node:
# - plugin (SLURM Plugin)
# - interlink (InterLink API)
# - oauth2-proxy (OAuth2 Proxy)
#
# Usage:
#   ./edgenode_service.sh <action> [service]
#
# Actions:
#   start [service]      Start all services or a specific service
#   stop [service]       Stop all services or a specific service
#   restart [service]    Restart all services or a specific service
#   status               Show status of all services
#   logs <service> [n]   Show last n lines of service logs (default: 20)
#
# Services:
#   plugin               SLURM Plugin
#   interlink            InterLink API
#   oauth2-proxy         OAuth2 Proxy
#   all                  All services (default)
#
# Examples:
#   ./edgenode_service.sh start
#   ./edgenode_service.sh stop oauth2-proxy
#   ./edgenode_service.sh restart interlink
#   ./edgenode_service.sh status
#   ./edgenode_service.sh logs plugin 50
#

#===============================================================================
# CONFIGURATION
#===============================================================================

# Directory structure (matches edgenode_setup.sh)
IL_DIR="${IL_DIR:-$HOME/.interlink}"
IL_DIR_BIN="$IL_DIR/bin"
IL_DIR_LOGS="$IL_DIR/logs"
IL_DIR_CONFIG="$IL_DIR/config"

# Socket paths
IL_SOCKET="${IL_DIR}/.interlink.sock"
IL_SOCKET_PG="${IL_DIR}/.plugin.sock"

# Service definitions
declare -A SERVICE_NAMES=(
    ["plugin"]="SLURM Plugin"
    ["interlink"]="InterLink API"
    ["oauth2-proxy"]="OAuth2 Proxy"
)

declare -A SERVICE_PIDS=(
    ["plugin"]="${IL_DIR}/plugin.pid"
    ["interlink"]="${IL_DIR}/interlink.pid"
    ["oauth2-proxy"]="${IL_DIR}/oauth2-proxy.pid"
)

declare -A SERVICE_LOGS=(
    ["plugin"]="${IL_DIR_LOGS}/plugin.log"
    ["interlink"]="${IL_DIR_LOGS}/interlink.log"
    ["oauth2-proxy"]="${IL_DIR_LOGS}/oauth2-proxy.log"
)

# Service start order (plugin -> interlink -> oauth2-proxy)
SERVICE_ORDER=("plugin" "interlink" "oauth2-proxy")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

log_service() {
    local service=$1
    local status=$2
    local message=$3
    
    if [ "$status" = "running" ]; then
        echo -e "${BLUE}[${service}]${NC} ${GREEN}●${NC} $message"
    elif [ "$status" = "stopped" ]; then
        echo -e "${BLUE}[${service}]${NC} ${RED}●${NC} $message"
    else
        echo -e "${BLUE}[${service}]${NC} ${YELLOW}●${NC} $message"
    fi
}

show_help() {
    head -n 33 "$0" | grep "^#" | sed 's/^# \?//'
    exit 0
}

check_installation() {
    if [ ! -d "$IL_DIR" ]; then
        log_error "InterLink installation not found at $IL_DIR"
        log_error "Please run edgenode_setup.sh first"
        exit 1
    fi
    
    if [ ! -d "$IL_DIR_CONFIG" ] || [ ! -d "$IL_DIR_BIN" ]; then
        log_error "InterLink configuration incomplete"
        log_error "Please run edgenode_setup.sh first"
        exit 1
    fi
}

validate_service() {
    local service=$1
    if [ "$service" != "all" ] && [ -z "${SERVICE_NAMES[$service]}" ]; then
        log_error "Unknown service: $service"
        log_error "Valid services: plugin, interlink, oauth2-proxy, all"
        exit 1
    fi
}

#===============================================================================
# SERVICE STATUS FUNCTIONS
#===============================================================================

is_service_running() {
    local service=$1
    local pid_file="${SERVICE_PIDS[$service]}"
    
    if [ ! -f "$pid_file" ]; then
        return 1
    fi
    
    local pid=$(cat "$pid_file")
    if ps -p "$pid" > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

get_service_pid() {
    local service=$1
    local pid_file="${SERVICE_PIDS[$service]}"
    
    if [ -f "$pid_file" ]; then
        cat "$pid_file"
    else
        echo "N/A"
    fi
}

get_service_uptime() {
    local pid=$1
    
    if [ "$pid" = "N/A" ] || ! ps -p "$pid" > /dev/null 2>&1; then
        echo "N/A"
        return
    fi
    
    # Get process start time
    local start_time=$(ps -p "$pid" -o lstart= 2>/dev/null)
    if [ -n "$start_time" ]; then
        local start_epoch=$(date --date="$start_time" "+%s" 2>/dev/null)
        local now_epoch=$(date +%s)
        local uptime_seconds=$((now_epoch - start_epoch))
        
        local days=$((uptime_seconds / 86400))
        local hours=$(((uptime_seconds % 86400) / 3600))
        local minutes=$(((uptime_seconds % 3600) / 60))
        
        if [ $days -gt 0 ]; then
            echo "${days}d ${hours}h ${minutes}m"
        elif [ $hours -gt 0 ]; then
            echo "${hours}h ${minutes}m"
        else
            echo "${minutes}m"
        fi
    else
        echo "N/A"
    fi
}

#===============================================================================
# SERVICE START FUNCTIONS
#===============================================================================

start_plugin() {
    local pid_file="${SERVICE_PIDS[plugin]}"
    
    if is_service_running "plugin"; then
        log_service "plugin" "running" "Already running (PID: $(get_service_pid plugin))"
        return 0
    fi
    
    log_service "plugin" "starting" "Starting SLURM Plugin..."
    
    SLURMCONFIGPATH="$IL_DIR_CONFIG/plugin.yaml" \
        $IL_DIR_BIN/plugin &> "${SERVICE_LOGS[plugin]}" &
    
    echo $! > "$pid_file"
    sleep 0.5
    
    if is_service_running "plugin"; then
        log_service "plugin" "running" "Started successfully (PID: $(get_service_pid plugin))"
        return 0
    else
        log_service "plugin" "stopped" "Failed to start"
        return 1
    fi
}

start_interlink() {
    local pid_file="${SERVICE_PIDS[interlink]}"
    
    if is_service_running "interlink"; then
        log_service "interlink" "running" "Already running (PID: $(get_service_pid interlink))"
        return 0
    fi
    
    log_service "interlink" "starting" "Starting InterLink API..."
    
    INTERLINKCONFIGPATH="${IL_DIR_CONFIG}/interlink.yaml" \
        ${IL_DIR_BIN}/interlink &>"${SERVICE_LOGS[interlink]}" &
    
    echo $! > "$pid_file"
    sleep 0.5
    
    if is_service_running "interlink"; then
        log_service "interlink" "running" "Started successfully (PID: $(get_service_pid interlink))"
        return 0
    else
        log_service "interlink" "stopped" "Failed to start"
        return 1
    fi
}

start_oauth2_proxy() {
    local pid_file="${SERVICE_PIDS[oauth2-proxy]}"
    
    if is_service_running "oauth2-proxy"; then
        log_service "oauth2-proxy" "running" "Already running (PID: $(get_service_pid oauth2-proxy))"
        return 0
    fi
    
    # Check if configuration exists
    if [ ! -f "${IL_DIR_CONFIG}/interlink.yaml" ]; then
        log_service "oauth2-proxy" "stopped" "Configuration not found. Run edgenode_setup.sh first."
        return 1
    fi
    
    # Load configuration values
    local IL_PUBLIC_PORT=$(grep "InterlinkPort:" "${IL_DIR_CONFIG}/plugin.yaml" | awk '{print $2}')
    local IL_CHECKIN_SUB=""
    
    # Try to extract from existing PID or config
    if [ -f "${IL_DIR}/.config" ]; then
        source "${IL_DIR}/.config"
    else
        log_service "oauth2-proxy" "warning" "Configuration file missing. Using defaults."
        IL_PUBLIC_PORT="${IL_PUBLIC_PORT:-33333}"
    fi
    
    log_service "oauth2-proxy" "starting" "Starting OAuth2 Proxy..."
    
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
        >"${SERVICE_LOGS[oauth2-proxy]}" 2>&1 &
    
    echo $! > "$pid_file"
    sleep 0.5
    
    if is_service_running "oauth2-proxy"; then
        log_service "oauth2-proxy" "running" "Started successfully (PID: $(get_service_pid oauth2-proxy))"
        return 0
    else
        log_service "oauth2-proxy" "stopped" "Failed to start"
        return 1
    fi
}

start_service() {
    local service=$1
    
    case $service in
        plugin)
            start_plugin
            ;;
        interlink)
            start_interlink
            ;;
        oauth2-proxy)
            start_oauth2_proxy
            ;;
        all)
            start_plugin
            sleep 1
            start_interlink
            sleep 1
            start_oauth2_proxy
            ;;
        *)
            log_error "Unknown service: $service"
            return 1
            ;;
    esac
}

#===============================================================================
# SERVICE STOP FUNCTIONS
#===============================================================================

stop_service() {
    local service=$1
    local pid_file="${SERVICE_PIDS[$service]}"
    
    if ! is_service_running "$service"; then
        log_service "$service" "stopped" "Not running"
        return 0
    fi
    
    local pid=$(get_service_pid "$service")
    log_service "$service" "stopping" "Stopping service (PID: $pid)..."
    
    kill "$pid" 2>/dev/null
    
    # Wait for process to terminate
    local count=0
    while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 10 ]; do
        sleep 0.5
        count=$((count + 1))
    done
    
    # Force kill if still running
    if ps -p "$pid" > /dev/null 2>&1; then
        log_service "$service" "stopping" "Force stopping..."
        kill -9 "$pid" 2>/dev/null
        sleep 0.5
    fi
    
    # Clean up PID file
    rm -f "$pid_file"
    
    if ! is_service_running "$service"; then
        log_service "$service" "stopped" "Stopped successfully"
        return 0
    else
        log_service "$service" "running" "Failed to stop"
        return 1
    fi
}

stop_all_services() {
    # Stop in reverse order
    for ((i=${#SERVICE_ORDER[@]}-1; i>=0; i--)); do
        stop_service "${SERVICE_ORDER[$i]}"
    done
}

#===============================================================================
# STATUS FUNCTION
#===============================================================================

show_status() {
    echo "InterLink Services Status"
    echo "=========================="
    echo
    
    local all_running=true
    
    for service in "${SERVICE_ORDER[@]}"; do
        local name="${SERVICE_NAMES[$service]}"
        local pid=$(get_service_pid "$service")
        
        if is_service_running "$service"; then
            local uptime=$(get_service_uptime "$pid")
            log_service "$service" "running" "${name} is running (PID: $pid, Uptime: $uptime)"
        else
            log_service "$service" "stopped" "${name} is stopped"
            all_running=false
        fi
    done
    
    echo
    echo "Logs directory: ${IL_DIR_LOGS}"
    echo "Config directory: ${IL_DIR_CONFIG}"
    echo
    
    if $all_running; then
        return 0
    else
        return 1
    fi
}

#===============================================================================
# LOGS FUNCTION
#===============================================================================

show_logs() {
    local service=$1
    local lines=${2:-20}
    
    validate_service "$service"
    
    if [ "$service" = "all" ]; then
        log_error "Please specify a service for logs"
        log_error "Available: plugin, interlink, oauth2-proxy"
        exit 1
    fi
    
    local log_file="${SERVICE_LOGS[$service]}"
    
    if [ ! -f "$log_file" ]; then
        log_error "Log file not found: $log_file"
        exit 1
    fi
    
    echo "=== Last $lines lines of ${SERVICE_NAMES[$service]} logs ==="
    echo
    tail -n "$lines" "$log_file"
}

#===============================================================================
# MAIN EXECUTION
#===============================================================================

main() {
    local action=$1
    local service=${2:-all}
    local extra=$3
    
    # Show help if no action
    if [ -z "$action" ]; then
        show_help
    fi
    
    # Handle help
    if [ "$action" = "--help" ] || [ "$action" = "-h" ] || [ "$action" = "help" ]; then
        show_help
    fi
    
    # Check installation
    check_installation
    
    # Validate service if not logs action
    if [ "$action" != "logs" ]; then
        validate_service "$service"
    fi
    
    # Execute action
    case $action in
        start)
            echo "Starting InterLink Services..."
            echo
            start_service "$service"
            ;;
        stop)
            echo "Stopping InterLink Services..."
            echo
            if [ "$service" = "all" ]; then
                stop_all_services
            else
                stop_service "$service"
            fi
            ;;
        restart)
            echo "Restarting InterLink Services..."
            echo
            if [ "$service" = "all" ]; then
                stop_all_services
                sleep 1
                start_service "all"
            else
                stop_service "$service"
                sleep 1
                start_service "$service"
            fi
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs "$service" "$extra"
            ;;
        *)
            log_error "Unknown action: $action"
            echo
            show_help
            ;;
    esac
}

# Run main function
main "$@"
