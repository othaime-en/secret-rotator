#!/bin/bash
set -e

# =============================================================================
# Secret Rotator - Enhanced Entrypoint Script
# Handles initialization for both fresh installs and existing deployments
# =============================================================================

echo "==================================================================="
echo "Secret Rotator - Container Initialization"
echo "==================================================================="

# -----------------------------------------------------------------------------
# Functions
# -----------------------------------------------------------------------------

log_info() {
    echo "[INFO] $1"
}

log_warn() {
    echo "[WARN] $1"
}

log_error() {
    echo "[ERROR] $1"
}

check_directory_writable() {
    local dir=$1
    if [ ! -w "$dir" ]; then
        log_error "Directory $dir is not writable"
        return 1
    fi
    return 0
}

create_directory() {
    local dir=$1
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        log_info "Created directory: $dir"
    fi
}

# -----------------------------------------------------------------------------
# Step 1: Verify Required Directories and Create if Missing
# -----------------------------------------------------------------------------

log_info "Checking directory permissions..."

# Writable directories (MUST be writable)
WRITABLE_DIRS=(
    "/app/data"
    "/app/data/backup"
    "/app/data/key_backups"
    "/app/logs"
)

for dir in "${WRITABLE_DIRS[@]}"; do
    create_directory "$dir"
    if ! check_directory_writable "$dir"; then
        log_error "Cannot write to $dir. Check volume mount permissions."
        exit 1
    fi
done

log_info "✓ All required directories exist and are writable"

# Check if config file exists
if ! file_exists /app/config/config.yaml; then
    log_warn "Config file not found at /app/config/config.yaml"
    log_info "Creating default configuration from example..."
    
    if file_exists /app/config/config.example.yaml; then
        cp /app/config/config.example.yaml /app/config/config.yaml
        log_info "Default configuration created. Please customize it."
    else
        log_error "Neither config.yaml nor config.example.yaml found!"
        exit 1
    fi
fi

# Check if master encryption key exists
if ! file_exists /app/config/.master.key; then
    log_warn "Master encryption key not found!"
    log_info "The application will generate a new key on first run."
    log_info "IMPORTANT: Backup the key file from /app/config/.master.key"
fi

# Verify Python environment
log_info "Verifying Python environment..."
python --version
pip list | grep secret-rotator || log_warn "secret-rotator package not found in pip list"

# Display configuration
echo ""
echo "Configuration:"
echo "  Config file: ${SECRET_ROTATOR_CONFIG:-/app/config/config.yaml}"
echo "  Data directory: /app/data"
echo "  Logs directory: /app/logs"
echo "  Python path: $(which python)"
echo ""

# Run pre-flight checks
log_info "Running pre-flight checks..."

# Verify the application can import
python -c "import secret_rotator; print(f'Version: {secret_rotator.__version__}')" || {
    log_error "Failed to import secret_rotator module"
    exit 1
}

# Check if running as non-root
if [ "$(id -u)" = "0" ]; then
    log_warn "Running as root user. This is not recommended for production."
else
    log_info "Running as user: $(whoami) (UID: $(id -u))"
fi

echo "==================================================================="
echo "Starting Secret Rotator Application"
echo "==================================================================="
echo ""

# Execute the main command
exec "$@"