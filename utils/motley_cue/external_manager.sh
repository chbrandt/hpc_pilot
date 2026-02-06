#!/bin/bash
#
# InterLink External Manager Script
#
# This script manages the initial setup of interLink in a motley_cue environment.
# It is executed by the remote agent connecting to the motley_cue server
# through mccli (motley cue client). The node where motley_cue server is running
# is referred to as the "edge node". The node where this script is executed
# is referred to as the "remote/manager node".
#
# The client mccli is a wrapper around ssh that allows connecting to the edge node
# using an access token for authentication. The general structure of mccli commands is:
# `mccli --token $ACCESS_TOKEN ssh $EDGE_NODE <command>`
#
# Steps performed by this script:
# 1. Check if $ACCESS_TOKEN and $EDGE_NODE environment variables are set.
# 2. Check if interLink is already installed on the edge node.
# 3. If already installed, guarantee that interLink is running.
# 4. If not installed, perform the installation steps:
#    a. Get the username of the user on the edge node.
#       (`mccli --token $ACCESS_TOKEN ssh $EDGE_NODE 'whoami'`)
#    b. Get the `sub` value from the ACCESS_TOKEN using flaat.
#       (`flaat-userinfo --userinfo $ACCESS_TOKEN | jq -r '.sub'`)
#    c. Define the port number to use for interLink based on the last three
#       characters of the username obtained in step a. Those characters will be
#       converted to an integer and added to a base port number (50000).
#    d. Launch interLink installation script (edgenode_setup.sh) with the collected parameters.
# 5. After installation, ensure interLink is running on the edge node.

set -e  # Exit on error

#===============================================================================
# CONNECTION SETTINGS
#===============================================================================

# SSH port
SSH_PORT=${SSH_PORT:-22}

#===============================================================================
# CONFIGURATION
#===============================================================================

# Base port for interLink services
BASE_PORT=50000

# Installation directory on edge node
IL_DIR="\$HOME/.interlink"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

#===============================================================================
# INTERLINK ATTRIBUTES
#===============================================================================
_PUBLIC_IP=""
_PUBLIC_PORT=""
_CHECKIN_SUB=""

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

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

#===============================================================================
# VALIDATION FUNCTIONS
#===============================================================================

validate_environment() {
    log_step "Validating environment variables..."
    
    if [ -z "$ACCESS_TOKEN" ]; then
        log_error "ACCESS_TOKEN environment variable is not set"
        log_error "Please set ACCESS_TOKEN before running this script"
        exit 1
    fi
    
    if [ -z "$EDGE_NODE" ]; then
        log_error "EDGE_NODE environment variable is not set"
        log_error "Please set EDGE_NODE before running this script"
        exit 1
    fi
    
    log_info "Environment variables validated"
    log_info "  EDGE_NODE: $EDGE_NODE (SSH_PORT: $SSH_PORT)"
    log_info "  ACCESS_TOKEN: ${ACCESS_TOKEN:0:20}..."
}

validate_dependencies() {
    log_step "Checking for required dependencies..."
    
    # Check for mccli
    if ! command -v mccli &> /dev/null; then
        log_error "mccli command not found"
        log_error "Please install motley_cue client"
        exit 1
    fi
    
    # Check for flaat-userinfo
    if ! command -v flaat-userinfo &> /dev/null; then
        log_error "flaat-userinfo command not found"
        log_error "Please install flaat (pip install flaat)"
        exit 1
    fi
    
    # Check for jq
    if ! command -v jq &> /dev/null; then
        log_error "jq command not found"
        log_error "Please install jq"
        exit 1
    fi
    
    log_info "All dependencies found"
}

call_mccli() {
    local cmd="$1"
    mccli --token "$ACCESS_TOKEN" ssh -p "$SSH_PORT" "$EDGE_NODE" "$cmd" 2>/dev/null
}

#===============================================================================
# INTERLINK CHECK FUNCTIONS
#===============================================================================

check_interlink_installed() {
    log_step "Checking if interLink is installed on edge node..."
    
    # Check if .interlink directory exists
    res=$(call_mccli "[ -d $IL_DIR ] && echo yes || echo no") 

    if [[ "$res" == *"yes"* ]]; then
        log_info "interLink installation detected at $IL_DIR"
        return 0
    else
        log_warn "interLink is not installed on edge node"
        return 1
    fi
}

check_interlink_running() {
    log_step "Checking if interLink services are running..."
    
    # Use edgenode_service.sh to check status
    local status_output
    # status_output=$(mccli --token "$ACCESS_TOKEN" ssh -p "$SSH_PORT" "$EDGE_NODE" "$IL_DIR/../edgenode_service.sh status" 2>&1 || true)
    status_output=$(call_mccli "$IL_DIR/../edgenode_service.sh status" 2>&1 || true)
    
    # Check if all services are running (return code 0 from status command)
    if call_mccli "$IL_DIR/../edgenode_service.sh status" >/dev/null 2>&1; then
        log_info "All interLink services are running"
        return 0
    else
        log_warn "Some or all interLink services are not running"
        return 1
    fi
}

ensure_interlink_running() {
    log_step "Ensuring interLink services are running..."
    
    if check_interlink_running; then
        log_info "interLink services are already running"
        return 0
    fi
    
    log_info "Starting interLink services..."
    call_mccli "$IL_DIR/../edgenode_service.sh start"
    
    # Wait a moment and verify
    sleep 2
    
    if check_interlink_running; then
        log_info "interLink services started successfully"
        return 0
    else
        log_error "Failed to start interLink services"
        log_error "Check logs on edge node: $IL_DIR/logs/"
        exit 1
    fi
}

#===============================================================================
# INSTALLATION FUNCTIONS
#===============================================================================

get_edge_username() {
    log_step "Getting username on edge node..."
    
    local username
    username=$(call_mccli 'whoami' 2>/dev/null)
    
    if [ -z "$username" ]; then
        log_error "Failed to get username from edge node"
        exit 1
    fi
    
    log_info "Edge node username: $username"
    # echo "$username"
    _USERNAME="$username"
}

get_user_sub() {
    log_step "Extracting user 'sub' from ACCESS_TOKEN..."
    
    local sub
    sub=$(flaat-userinfo --userinfo "$ACCESS_TOKEN" 2>/dev/null | jq -r '.sub')
    
    if [ -z "$sub" ] || [ "$sub" = "null" ]; then
        log_error "Failed to extract 'sub' from ACCESS_TOKEN"
        log_error "Please ensure ACCESS_TOKEN is valid"
        exit 1
    fi
    
    log_info "User sub: $sub"
    # echo "$sub"
    _CHECKIN_SUB="$sub"
}

calculate_port() {
    local username=$1
    log_step "Calculating port number from username..."
    
    # Get last 3 characters of username
    local suffix="${username: -3}"
    
    # Convert to integer, handling non-numeric characters
    local port_offset=0
    
    # Try to convert suffix to number
    if [[ "$suffix" =~ ^[0-9]+$ ]]; then
        port_offset=$((10#$suffix))
    else
        # If not purely numeric, use ASCII values
        for ((i=0; i<${#suffix}; i++)); do
            char="${suffix:$i:1}"
            # Get ASCII value and add to offset
            port_offset=$((port_offset + $(printf '%d' "'$char")))
        done
        # Ensure we stay within reasonable range (0-999)
        port_offset=$((port_offset % 1000))
    fi
    
    local port=$((BASE_PORT + port_offset))
    
    log_info "Calculated port: $port (base: $BASE_PORT + offset: $port_offset)"
    # echo "$port"
    _PUBLIC_PORT="$port"
}

get_edge_public_ip() {
    log_step "Getting edge node public IP..."
    
    local public_ip

    if [[ "$EDGE_NODE" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        public_ip="$EDGE_NODE"
    else
        # Try to resolve EDGE_NODE hostname
        public_ip=$(host "$EDGE_NODE" 2>/dev/null | grep "has address" | awk '{print $4}' | head -1)
    fi

    if [ -z "$public_ip" ]; then
        log_error "Failed to determine edge node public IP"
        log_error "Please provide it manually or check EDGE_NODE value"
        exit 1
    fi
    
    log_info "Edge node public IP: $public_ip"
    # echo "$public_ip"
    _PUBLIC_IP="$public_ip"
}

install_interlink() {
    log_step "Starting interLink installation..."
    
    # Gather installation parameters
    local username
    local user_sub
    local port
    local public_ip
    
    # username=$(get_edge_username)
    # user_sub=$(get_user_sub)
    # port=$(calculate_port "$username")
    # public_ip=$(get_edge_public_ip)
    get_edge_username
    get_user_sub
    calculate_port "$_USERNAME"
    get_edge_public_ip
    
    log_info "Installation parameters:"
    # log_info "  Username:   $username"
    # log_info "  User Sub:   $user_sub"
    # log_info "  Port:       $port"
    # log_info "  Public IP:  $public_ip"
    log_info "  Username:   $_USERNAME"
    log_info "  User Sub:   $_CHECKIN_SUB"
    log_info "  Port:       $_PUBLIC_PORT"
    log_info "  Public IP:  $_PUBLIC_IP"
    echo
    
    # Check if edgenode_setup.sh exists on edge node
    log_step "Checking for installation script on edge node..."

    res=$(call_mccli "[ -d ~/hpc_pilot ] && echo yes || echo no" 2>/dev/null)

    if [[ "$res" == *"no"* ]]; then
        call_mccli "git clone https://github.com/chbrandt/hpc_pilot.git ~/hpc_pilot" 2>/dev/null || true
        # log_error "edgenode_setup.sh not found on edge node"
        # log_error "Please ensure the script is available at ~/edgenode_setup.sh"
        # exit 1
    fi
    
    # Run installation
    log_step "Running edgenode_setup.sh on edge node..."
    log_info "This may take a few minutes..."
    echo
    
    # call_mccli \
    #     "bash ~/hpc_pilot/utils/edgenode_setup.sh --public-ip $public_ip --public-port $port --checkin-sub '$user_sub'"
    call_mccli \
        "bash ~/hpc_pilot/utils/edgenode_setup.sh --public-ip $_PUBLIC_IP --public-port $_PUBLIC_PORT --checkin-sub '$_CHECKIN_SUB'"
    
    local install_status=$?
    
    if [ $install_status -eq 0 ]; then
        log_info "Installation completed successfully"
    else
        log_error "Installation failed with status code: $install_status"
        exit 1
    fi
}

#===============================================================================
# MAIN EXECUTION
#===============================================================================

main() {
    echo "=========================================="
    echo "InterLink External Manager"
    echo "=========================================="
    echo
    
    # Validate environment and dependencies
    validate_environment
    echo
    validate_dependencies
    echo
    
    # Check if already installed
    if check_interlink_installed; then
        echo
        # Already installed, just ensure it's running
        ensure_interlink_running
        echo
        
        log_info "interLink is ready on edge node: $EDGE_NODE"
    else
        echo
        # Not installed, perform installation
        log_warn "interLink not found, starting installation process..."
        echo
        
        install_interlink
        echo
        
        # Ensure services are running after installation
        ensure_interlink_running
        echo
        
        log_info "interLink installation and setup completed successfully"
    fi
    
    echo
    echo "=========================================="
    log_info "Setup complete!"
    echo "=========================================="
    echo
    echo "Edge node: $EDGE_NODE"
    echo "Installation directory: $IL_DIR"
    echo
    echo "To manage services, run on edge node:"
    echo "  $IL_DIR/../edgenode_service.sh status"
    echo "  $IL_DIR/../edgenode_service.sh start|stop|restart"
    echo
}

# Run main function
main "$@"
