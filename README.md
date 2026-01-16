# Secret Rotator

A comprehensive Python-based system for automating the rotation of passwords, API keys, and other secrets across different services. The system provides scheduled rotation, encrypted backup management, and a web interface for monitoring and manual operations.

[![PyPI version](https://badge.fury.io/py/secret-rotator.svg)](https://badge.fury.io/py/secret-rotator)
[![Python Versions](https://img.shields.io/pypi/pyversions/secret-rotator.svg)](https://pypi.org/project/secret-rotator/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Commits since latest](https://img.shields.io/github/commits-since/othaime-en/secret-rotator/latest)](https://github.com/othaime-en/secret-rotator/commits/latest)

## Features

- **Automated Secret Rotation**: Configurable schedules for automatic secret rotation (daily, weekly, or custom intervals)
- **Multiple Secret Types**: Support for passwords, API keys, database credentials, JWT secrets, SSH keys, and certificates
- **Encrypted Storage**: Fernet-based symmetric encryption for secrets at rest with master key management
- **Backup Management**: Automatic encrypted backups with integrity verification and configurable retention policies
- **Web Dashboard**: Browser-based interface for monitoring rotation status and manual operations
- **Extensible Architecture**: Plugin system for custom secret providers and rotation strategies
- **Comprehensive Logging**: Structured logging with sensitive data masking and configurable output formats
- **Retry Logic**: Built-in exponential backoff for handling transient failures
- **Master Key Backup**: Multiple backup strategies including encrypted backups and Shamir's Secret Sharing

## Installation

### From PyPI

```bash
pip install secret-rotator
```

### With Optional Dependencies

```bash
# For database support (PostgreSQL, MySQL, MongoDB)
pip install secret-rotator[databases]

# For advanced features (JWT, Shamir's Secret Sharing)
pip install secret-rotator[advanced]

# Install all optional dependencies
pip install secret-rotator[all]
```

### From Source

```bash
git clone https://github.com/othaime-en/secret-rotator.git
cd secret-rotator
pip install -e .
```

### Using Docker

```bash
# Clone the repository
git clone https://github.com/othaime-en/secret-rotator.git
cd secret-rotator

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your settings

# Build and run with Docker Compose
docker-compose up -d
```

For development with hot-reload:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Quick Start

### Initial Setup

Run the interactive setup wizard to create configuration files and directories:

```bash
secret-rotator-setup
```

This will guide you through:

- Creating configuration directories
- Generating a master encryption key
- Setting up initial configuration
- Configuring rotation schedules

### Configuration

Edit the generated configuration file at `~/.config/secret-rotator/config.yaml`:

```yaml
rotation:
  schedule: "daily"
  retry_attempts: 3
  backup_old_secrets: true

logging:
  level: "INFO"
  file: "logs/rotation.log"

providers:
  file_storage:
    type: "file"
    file_path: "~/.local/share/secret-rotator/secrets.json"

rotators:
  password_gen:
    type: "password"
    length: 16
    use_symbols: true
    use_numbers: true

jobs:
  - name: "database_password"
    provider: "file_storage"
    rotator: "password_gen"
    secret_id: "db_password"
    schedule: "weekly"
```

### Running the Application

Start the daemon with web interface and scheduler:

```bash
secret-rotator
```

The web interface will be available at `http://localhost:8080`

### One-Time Rotation

Execute a single rotation without starting the scheduler:

```bash
secret-rotator --mode once
```

### Other Operations

```bash
# Show system status
secret-rotator --mode status

# Verify encryption setup
secret-rotator --mode verify

# Verify backup integrity
secret-rotator --mode verify-backups

# Rotate master encryption key
secret-rotator --mode rotate-master-key

# Cleanup old backups
secret-rotator --mode cleanup-backups
```

## Master Key Backup

The system provides multiple strategies for backing up your master encryption key:

### Encrypted Backup (Recommended)

Create a passphrase-protected backup:

```bash
secret-rotator-backup create-encrypted
```

### Split Key Backup (Shamir's Secret Sharing)

Split the key into multiple shares where a threshold is needed to reconstruct:

```bash
secret-rotator-backup create-split --shares 5 --threshold 3
```

### List and Verify Backups

```bash
# List all available backups
secret-rotator-backup list

# Verify a backup
secret-rotator-backup verify /path/to/backup.enc

# Restore from backup
secret-rotator-backup restore /path/to/backup.enc
```

## Supported Secret Types

### Built-in Rotators

- **Password Generator**: Configurable length and character requirements
- **API Key Generator**: Hex, base64, or alphanumeric formats with optional prefixes
- **Database Password**: Tested connection validation for PostgreSQL, MySQL, MongoDB
- **JWT Secret**: Cryptographically secure keys for HS256, HS384, HS512
- **SSH Key Pair**: RSA or Ed25519 key generation
- **TLS Certificate**: Self-signed certificate generation
- **OAuth2 Client Secret**: Standard OAuth2 secret generation

### Built-in Providers

- **File Storage**: JSON-based storage with encryption support
- **AWS Secrets Manager**: Integration with AWS (requires configuration)

### Custom Extensions

Create custom providers and rotators using the plugin system. See the documentation for details on implementing custom handlers.

## Web Interface Features

The browser-based dashboard provides:

- Real-time rotation status monitoring
- Manual secret rotation triggers
- Backup history and restoration
- Backup integrity verification status
- System health metrics
- Activity logs and audit trail

Access the dashboard at `http://localhost:8080` when the application is running.

## Security Features

### Encryption

- Fernet symmetric encryption (AES-128 in CBC mode with HMAC authentication)
- Master key rotation capability with automatic re-encryption
- Encrypted backups with integrity verification
- Secure key derivation from passphrases using PBKDF2

### Access Controls

- File-based permissions (0600) for sensitive files
- Configurable access policies for secrets
- Audit logging of all secret access and modifications
- Sensitive data masking in logs

### Backup Integrity

- Automatic checksum verification
- Scheduled integrity checks
- Corruption detection and alerting
- Backup health monitoring

## Development

### Running Tests

```bash
# Install development dependencies
pip install secret-rotator[dev]

# Run test suite
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=secret_rotator --cov-report=html
```

### Code Quality

```bash
# Format code
black src/

# Type checking
mypy src/

# Linting
flake8 src/
```

## Configuration Reference

### Rotation Schedules

- `daily`: Rotate once per day at 02:00
- `weekly`: Rotate once per week
- `every_N_minutes`: Custom minute interval (e.g., `every_30_minutes`)
- `every_N_hours`: Custom hour interval (e.g., `every_12_hours`)

### Backup Retention

```yaml
backup:
  retention:
    days: 90 # Keep backups for 90 days
    max_backups_per_secret: 10 # Maximum backups per secret
```

### Logging Configuration

```yaml
logging:
  level: "INFO" # DEBUG, INFO, WARNING, ERROR, CRITICAL
  structured: true # JSON-formatted logs for aggregation
  mask_sensitive_data: true # Automatically mask secrets in logs
  separate_error_log: true # Separate file for errors
```

## Troubleshooting

### Common Issues

**Import Errors**: Ensure the package is properly installed with `pip install -e .` for development or `pip install secret-rotator` for production.

**Permission Denied**: Check file permissions on configuration and key files. They should be readable/writable only by the owner (mode 0600).

**Encryption Failures**: Verify the master key file exists and is not corrupted. Use `secret-rotator --mode verify` to check encryption setup.

**Backup Verification Failures**: Run `secret-rotator --mode verify-backups` to identify corrupted backups. Consider creating new backups if integrity checks fail.

### Getting Help

- Check the [documentation](https://github.com/othaime-en/secret-rotator#readme)
- Review [example configurations](https://github.com/othaime-en/secret-rotator/tree/main/config)
- Report issues on [GitHub](https://github.com/othaime-en/secret-rotator/issues)

## Contributing

Contributions are welcome! Please ensure:

1. All tests pass: `pytest tests/ -v`
2. Code follows style guidelines: `black src/` and `flake8 src/`
3. Documentation is updated for new features
4. Commit messages are clear and descriptive

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## License

This project is licensed under the MIT License. See the [LICENSE.md](LICENSE.md) file for details.

## Security Considerations

This tool handles sensitive credentials. Ensure proper security measures:

- **File Permissions**: Restrict access to configuration files and key files
- **Master Key**: Backup the master encryption key securely using provided tools
- **Network Security**: Use HTTPS/TLS in production environments
- **Access Control**: Implement appropriate access controls for the web interface
- **Audit Logs**: Regularly review audit logs for suspicious activity
- **Key Rotation**: Rotate the master encryption key periodically (recommended: every 90 days)

For security issues, please report privately to the maintainers rather than creating public issues.

## Acknowledgments

Built with:

- [cryptography](https://cryptography.io/) for encryption
- [PyYAML](https://pyyaml.org/) for configuration management
- [schedule](https://schedule.readthedocs.io/) for job scheduling

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed history of changes.

## Support

For questions, feature requests, or bug reports:

- Open an issue on [GitHub](https://github.com/othaime-en/secret-rotator/issues)
- Check existing [discussions](https://github.com/othaime-en/secret-rotator/discussions)
