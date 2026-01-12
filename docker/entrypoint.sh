#!/bin/bash
set -e

# Entrypoint script for Secret Rotator container
echo "==================================================================="
echo "Secret Rotator - Container Initialization"
echo "==================================================================="

# Function to check if file exists
file_exists() {
    [ -f "$1" ]
}

# Function to check directory permissions
check_permissions() {
    local dir=$1
    if [ ! -w "$dir" ]; then
        echo "ERROR: Directory $dir is not writable"
        exit 1
    fi
}

# Verify required directories exist and are writable
echo "Checking directory permissions..."
check_permissions /app/data
check_permissions /app/logs

# Check if config file exists
if ! file_exists /app/config/config.yaml; then
    echo "WARNING: Config file not found at /app/config/config.yaml"
    echo "Creating default configuration from example..."
    
    if file_exists /app/config/config.example.yaml; then
        cp /app/config/config.example.yaml /app/config/config.yaml
        echo "Default configuration created. Please customize it."
    else
        echo "ERROR: Neither config.yaml nor config.example.yaml found!"
        exit 1
    fi
fi

# Check if master encryption key exists
if ! file_exists /app/config/.master.key; then
    echo "WARNING: Master encryption key not found!"
    echo "The application will generate a new key on first run."
    echo "IMPORTANT: Backup the key file from /app/config/.master.key"
fi

# Verify Python environment
echo "Verifying Python environment..."
python --version
pip list | grep secret-rotator || echo "WARNING: secret-rotator package not found in pip list"

# Display configuration
echo ""
echo "Configuration:"
echo "  Config file: ${SECRET_ROTATOR_CONFIG:-/app/config/config.yaml}"
echo "  Data directory: /app/data"
echo "  Logs directory: /app/logs"
echo "  Python path: $(which python)"
echo ""

# Run pre-flight checks
echo "Running pre-flight checks..."

# Verify the application can import
python -c "import secret_rotator; print(f'Version: {secret_rotator.__version__}')" || {
    echo "ERROR: Failed to import secret_rotator module"
    exit 1
}

# Check if running as non-root
if [ "$(id -u)" = "0" ]; then
    echo "WARNING: Running as root user. This is not recommended for production."
else
    echo "Running as user: $(whoami) (UID: $(id -u))"
fi

echo "==================================================================="
echo "Starting Secret Rotator Application"
echo "==================================================================="
echo ""

# Execute the main command
exec "$@"