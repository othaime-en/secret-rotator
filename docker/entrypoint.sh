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

# -----------------------------------------------------------------------------
# Step 2: Handle Configuration File
# -----------------------------------------------------------------------------

log_info "Configuring application..."

# Check if user provided custom config in /app/config/
if [ -f /app/config/config.yaml ]; then
    log_info "Using custom configuration from /app/config/config.yaml"
    export SECRET_ROTATOR_CONFIG=/app/config/config.yaml
    CONFIG_SOURCE="custom"
else
    # No custom config, use/create one in data volume
    log_warn "No custom config found at /app/config/config.yaml"
    
    if [ ! -f /app/data/config.yaml ]; then
        log_info "Creating default configuration in /app/data/config.yaml"
        
        if [ -f /app/config/config.example.yaml ]; then
            cp /app/config/config.example.yaml /app/data/config.yaml
            log_info "✓ Default configuration created from example"
        else
            log_error "Cannot find config.example.yaml in /app/config/"
            exit 1
        fi
    else
        log_info "Using existing configuration from /app/data/config.yaml"
    fi
    
    export SECRET_ROTATOR_CONFIG=/app/data/config.yaml
    CONFIG_SOURCE="default"
fi

log_info "Configuration source: $CONFIG_SOURCE"

# -----------------------------------------------------------------------------
# Step 3: Handle Master Encryption Key Migration
# -----------------------------------------------------------------------------

log_info "Checking master encryption key..."

# New location (correct): /app/data/.master.key
# Old location (legacy): /app/config/.master.key

NEW_KEY_PATH="/app/data/.master.key"
OLD_KEY_PATH="/app/config/.master.key"

if [ -f "$NEW_KEY_PATH" ]; then
    log_info "✓ Master key found at $NEW_KEY_PATH"
    KEY_STATUS="exists"
    
elif [ -f "$OLD_KEY_PATH" ]; then
    # Migration scenario: key exists in old location
    log_warn "Master key found in legacy location: $OLD_KEY_PATH"
    log_info "Migrating master key to data volume..."
    
    if cp "$OLD_KEY_PATH" "$NEW_KEY_PATH"; then
        chmod 600 "$NEW_KEY_PATH"
        log_info "✓ Master key migrated to $NEW_KEY_PATH"
        log_info "  Old key preserved at $OLD_KEY_PATH for backup"
        KEY_STATUS="migrated"
    else
        log_error "Failed to migrate master key"
        exit 1
    fi
    
else
    # No key exists - will be generated on first run
    log_info "No master key found - will be generated on first run"
    log_warn "IMPORTANT: Backup the key after first run!"
    log_info "  Key location: $NEW_KEY_PATH"
    log_info "  Backup command: docker cp secret-rotator:/app/data/.master.key ./backup/"
    KEY_STATUS="will_generate"
fi

# -----------------------------------------------------------------------------
# Step 4: Verify Python Environment
# -----------------------------------------------------------------------------

log_info "Verifying Python environment..."

python --version || {
    log_error "Python not found"
    exit 1
}

# Verify package installation
if python -c "import secret_rotator; print(f'Version: {secret_rotator.__version__}')" 2>/dev/null; then
    log_info "✓ secret_rotator package verified"
else
    log_error "Failed to import secret_rotator module"
    log_error "Package may not be installed correctly"
    exit 1
fi

# -----------------------------------------------------------------------------
# Step 5: Display Configuration Summary
# -----------------------------------------------------------------------------

echo ""
echo "==================================================================="
echo "Configuration Summary"
echo "==================================================================="
echo "Config file:       $SECRET_ROTATOR_CONFIG"
echo "Master key:        $NEW_KEY_PATH ($KEY_STATUS)"
echo "Data directory:    /app/data"
echo "Logs directory:    /app/logs"
echo "Python version:    $(python --version 2>&1)"
echo "Running as user:   $(whoami) (UID: $(id -u))"
echo "==================================================================="

# -----------------------------------------------------------------------------
# Step 6: First Run Instructions
# -----------------------------------------------------------------------------

if [ "$KEY_STATUS" = "will_generate" ]; then
    echo ""
    echo "==================================================================="
    echo "⚠️  FIRST RUN SETUP REQUIRED"
    echo "==================================================================="
    echo ""
    echo "After the application starts successfully:"
    echo ""
    echo "1. BACKUP THE MASTER KEY (Critical!)"
    echo "   docker cp secret-rotator:/app/data/.master.key ./backup/"
    echo ""
    echo "2. Create an encrypted backup:"
    echo "   docker exec secret-rotator secret-rotator-backup create-encrypted"
    echo ""
    echo "3. Copy encrypted backup to external storage:"
    echo "   docker cp secret-rotator:/app/data/key_backups/backup_*.enc ./backup/"
    echo ""
    echo "4. Store the backup passphrase in a password manager"
    echo ""
    echo "==================================================================="
    echo ""
fi

# -----------------------------------------------------------------------------
# Step 7: Health Check
# -----------------------------------------------------------------------------

log_info "Running pre-flight checks..."

# Check if config file is valid YAML
if command -v python3 &> /dev/null; then
    python3 -c "import yaml; yaml.safe_load(open('$SECRET_ROTATOR_CONFIG'))" 2>/dev/null || {
        log_error "Configuration file is not valid YAML"
        log_error "Check $SECRET_ROTATOR_CONFIG for syntax errors"
        exit 1
    }
    log_info "✓ Configuration file is valid YAML"
fi

# -----------------------------------------------------------------------------
# Step 8: Start Application
# -----------------------------------------------------------------------------

echo ""
echo "==================================================================="
echo "Starting Secret Rotator Application"
echo "==================================================================="
echo ""

# Execute the main command (from CMD in Dockerfile)
exec "$@"