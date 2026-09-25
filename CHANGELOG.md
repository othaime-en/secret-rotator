# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-09-25

This release closes out the four-phase security/quality roadmap from
the project's internal production-readiness audit - authentication,
hardening, distributed-deployment support, and testing/docs/release
process. It's the largest release in the project's history; see
below for the full breakdown.

### ⚠️ Upgrading from 1.2.x - action required

**The dashboard now requires a login, and has no default password.**
After upgrading, the dashboard will refuse every login attempt until
you run:

```bash
secret-rotator --mode set-web-password
```

**A Flask session-signing key is now required in production.** Set
`FLASK_SECRET_KEY` (recommended: `python -c "import secrets; print(secrets.token_hex(32))"`)
before running with `SECRET_ROTATOR_ENV=production` - without it,
startup now fails closed rather than falling back to an insecure
default.

Read [docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) before
redeploying - in particular, this application still does not
terminate TLS itself; a reverse proxy is required for anything beyond
`localhost` (a ready-to-use example is in the guide).

### Added

- **Web dashboard authentication**: session-based login, CSRF
  protection on all state-changing endpoints, rate limiting
  (Flask-Limiter)
- **Real audit logging**: append-only log of rotations, restores,
  logins, and login failures
- **Background job execution** for rotations — `/api/rotate` no
  longer blocks the HTTP request for the full duration of a
  multi-secret rotation
- **Distributed coordination** (opt-in via `distributed.enabled`):
  Redis-backed locking so multiple instances can't race on the same
  master-key rotation, and a distributed job queue (RQ-based) for
  multi-instance rotation scheduling
- **Remote backup sync**: S3-compatible upload of key backups (AWS
  S3, MinIO, Cloudflare R2, etc.), on-create and/or on a daily
  schedule
- **Production WSGI server** (waitress) — the dashboard no longer
  runs on Flask's development server
- Test coverage for modules that previously had none: encryption
  manager, plugin system, advanced rotators (database/JWT/SSH/TLS/
  OAuth2), key backup manager, passphrase manager, setup wizard, and
  the `manage_key_backups` CLI
- CI: Python 3.9–3.12 test matrix (previously 3.9 only), a lint gate
  (black + flake8, blocking), mypy type-checking (informational),
  an enforced test-coverage floor, and pip-audit/bandit dependency
  and static-analysis scanning
- **Auditable release pipeline**: version-consistency and changelog
  checks gate every release, full test suite re-run, PyPI publishing
  via Trusted Publishing (OIDC — no API token secret), automatic
  GitHub Release creation
- `SECURITY.md` (vulnerability disclosure process via GitHub Security
  Advisories), `CONTRIBUTING.md` (dev setup, testing, release
  checklist), `docs/HARDENING_GUIDE.md` (deployment security guidance
  and a TLS reverse-proxy example)
- Dependabot for both Python dependencies and GitHub Actions

### Fixed

- `DatabasePasswordRotator.generate_new_secret()` produced a password
  with no digit ~1% of the time, failing the rotator's own
  validation; generation now guarantees at least one uppercase,
  lowercase, and digit character
- Split-key (Shamir) backups from a single operation weren't grouped
  correctly by `list_backups()` — each share got its own timestamp
  and no group ever showed as complete; a related bug meant two
  split-key backups created within the same second could silently
  overwrite each other's share files
- The setup wizard silently prompted for a custom rotation schedule a
  second time on every run and discarded the answer, regardless of
  which option was actually selected
- A variable-shadowing bug in `encryption_manager.py` that could
  crash master-key rotation with an `UnboundLocalError`
- `/api/restore` crashed with a `NameError` (Python's `False` vs.
  JavaScript-style `false`) on malformed requests, returning a 500
  instead of the intended 400

### Security

- **The web dashboard previously had no authentication at all** —
  anyone with network access could rotate every secret, inspect
  backups, and restore old values. Fixed; see the upgrade note above.
- **Path traversal in the backup restore endpoints**, reachable
  unauthenticated, with a plausible path to master-key disclosure.
  Fixed with a containment check against the resolved backup
  directory.
- **Hardcoded Flask `SECRET_KEY`** (`dev-key-change-in-production`)
  that the app never actually loaded a real value for. Fixed; see the
  upgrade note above.
- **No CSRF protection** on state-changing endpoints. Fixed.
- **`mysql_connector_repackaged`**, an unofficial third-party
  repackaging with a dependency-confusion-shaped name, was a hard
  dependency. Replaced with the official `mysql-connector-python`.
- Known CVEs in pinned dependencies (`cryptography`, `Werkzeug`,
  `Flask`, `requests`, `PyJWT`) patched; dependencies are now pinned
  by a hash-verified lockfile and continuously scanned in CI
  (pip-audit, bandit) and by Dependabot.

### Removed

- `secret_access.py` — a fully-built access-control/audit module that
  was never actually wired into anything (389 lines of dead code; the
  README's audit-logging claims described this module, not what was
  running). Replaced by the real audit logging added above.

### Changed

- The legacy `web_interface.py` server is deprecated in favor of the
  Flask blueprint implementation (`web/`) and scheduled for removal
  in 1.4.0.
- `pyproject.toml` license metadata modernized to the SPDX format.

## [1.2.3] - 2026-01-25

### Added

- Initial Flask-based web dashboard (`web/` package: application
  factory, blueprints for dashboard/API/health, HTML templates and
  JS), running alongside the legacy server as groundwork for the
  authentication and hardening work landed in 1.3.0.

## [1.2.2] - 2026-01-20

### Changed

- Reworked master-key rotation to be more robust, with PBKDF2-based
  key derivation support.

### Added

- Integration tests covering additional key-rotation scenarios.

## [1.2.1] - 2026-01-18

### Added

- `PassphraseManager`: a unified priority chain (CLI argument > config
  > standard file locations > environment variable > stdin >
  > interactive prompt) for supplying the backup-encryption passphrase,
  > integrated into the setup wizard and all `secret-rotator-backup`
  > commands that need one.

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
