# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2025-01-16

### Changed

- **Docker Architecture Overhaul**: Implemented proper separation of configuration and runtime data
  - Master encryption key moved from `config/.master.key` to `data/.master.key` (writable volume)
  - Config directory is now optional and truly read-only in production
  - Default configuration auto-created in data volume if custom config not provided
- Enhanced entrypoint script with comprehensive initialization and validation
- Updated default paths in `EncryptionManager` and `MasterKeyBackupManager` to use data volume
- Improved volume mount documentation in `docker-compose.yml`

### Added

- Automatic migration support for deployments upgrading from v1.1.0 and earlier
- `DOCKER_QUICKSTART.md` - Comprehensive Docker deployment guide
- Pre-flight configuration validation in entrypoint script
- Clear first-run instructions and backup reminders
- Health checks and error messages for common deployment issues

### Fixed

- Missing directories on fresh Docker installations causing startup failures
- "logger not associated with a value" errors due to improper initialization order
- Permission issues when config directory mounted read-only
- Dependency installation problems in containerized environments

### Security

- Config directory can now be safely mounted read-only in production
- Master key generated with proper permissions (600) in writable volume
- Non-root user (UID 1000) enforced in container runtime

## [1.1.0] - 2025-01-13

### Added

- Docker support with multi-stage Dockerfile for optimized production images
- Docker Compose configurations for both production and development environments
- Health check endpoint for container orchestration
- Entrypoint script for containerized deployments
- Environment variable configuration support via .env files
- Volume management for persistent data (config, data, logs)
- Resource limits and security configurations for Docker deployment

### Changed

- Enhanced deployment options with containerization support
- Improved documentation for Docker-based installations

### Documentation

- Added Docker installation and usage instructions
- Included docker-compose examples for production and development
- Added environment variable configuration guide

## [1.0.0] - 2025-01-10

### Added

- Initial public release
- Automated secret rotation with configurable schedules
- Support for multiple secret types (passwords, API keys, database credentials)
- File-based secret storage with encryption support
- Backup and restore functionality for all rotations
- Web-based dashboard for monitoring and manual operations
- Extensible plugin system for custom providers and rotators
- Retry logic with exponential backoff
- Comprehensive audit logging with structured logging support
- Master encryption key management with multiple backup strategies
- Backup integrity verification system
- Support for Shamir's Secret Sharing for master key backup
- CLI tools for key backup management
- Interactive setup wizard
- Support for Python 3.9, 3.10, 3.11, and 3.12

### Security

- Fernet (symmetric) encryption for secrets at rest
- Encrypted backups with passphrase protection
- Master key rotation capability
- Secure file permissions (0600) for sensitive files
- Sensitive data masking in logs

### Documentation

- Comprehensive README with installation and usage instructions
- Example configuration file
- Backup and recovery instructions
- API documentation for extending with custom providers/rotators

[1.2.0]: https://github.com/othaime-en/secret-rotator/releases/tag/v1.2.0
[1.1.0]: https://github.com/othaime-en/secret-rotator/releases/tag/v1.1.0
[1.0.0]: https://github.com/othaime-en/secret-rotator/releases/tag/v1.0.0
