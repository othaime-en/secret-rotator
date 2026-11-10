# Secret Rotator

A comprehensive Python-based system for automating the rotation of passwords, API keys, and other secrets across different services. The system provides scheduled rotation, encrypted backup management, and a web interface for monitoring and manual operations.

[![PyPI version](https://badge.fury.io/py/secret-rotator.svg)](https://badge.fury.io/py/secret-rotator)
[![Python Versions](https://img.shields.io/pypi/pyversions/secret-rotator.svg)](https://pypi.org/project/secret-rotator/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Commits since latest](https://img.shields.io/github/commits-since/othaime-en/secret-rotator/latest)](https://github.com/othaime-en/secret-rotator/commits/latest)

## Features

- **Automated Secret Rotation**: Configurable schedules, global and per job (daily, weekly, monthly, or every N minutes/hours)
- **Multiple Secret Types**: Passwords, API keys and JWT secrets out of the box (see [Supported Secret Types](#supported-secret-types) for what else exists in the code base)
- **Encrypted Storage**: Fernet-based symmetric encryption for secrets at rest with master key management
- **Backup Management**: Automatic encrypted backups with integrity verification and configurable retention policies
- **Web Dashboard**: Browser-based interface, in light and dark themes, for monitoring rotations and backups and for managing jobs, providers, rotators and settings (see [Web Dashboard](#web-dashboard))
- **Extensible Architecture**: Plugin system for custom secret providers and rotation strategies
- **Comprehensive Logging**: Structured logging with sensitive data masking and configurable output formats
- **Failure Handling**: A failed rotation is logged, audited and shown on the dashboard, and triggers a backup integrity check
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

### Docker Quick Start (Fresh Install)

```bash
# Clone the repository
git clone https://github.com/othaime-en/secret-rotator.git
cd secret-rotator

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your settings

# Create directories and start
mkdir -p data logs

docker-compose up -d
```

For development with hot-reload:

```bash
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

The container automatically handles:

- Directory creation and permissions
- Default configuration setup
- Master encryption key generation
- Application initialization

**Important:** Backup the master key after first run:

```bash
docker cp secret-rotator:/app/data/.master.key ./backup/
```

**Also important:** set `SECRET_ROTATOR_ADMIN_PASSWORD_HASH` in `.env`
before exposing port 8080 beyond your own machine — there is no
default password — and put a TLS-terminating reverse proxy in front of
it for anything beyond `localhost`. See
[docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) for both, including
a ready-to-use Caddy example.

### Production Deployment (Custom Config)

```bash
# Prepare custom configuration
mkdir -p config data logs
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your settings

# Uncomment config volume in docker-compose.yml:
# - ./config:/app/config:ro

# Deploy
docker-compose up -d
```

### Architecture (v1.2.0+)

```
./config/     → /app/config/ (read-only, optional)
./data/       → /app/data/ (read-write, required - secrets, keys, backups)
./logs/       → /app/logs/ (read-write, required)
```

See [DOCKER_QUICKSTART.md](docs/DOCKER_QUICKSTART.md) for detailed guide.

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
  schedule: "daily" # default for every job without its own schedule
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

Before starting in anything beyond local dev, set an admin password and
a Flask secret key — the dashboard requires login and refuses to start
in production mode without both configured:

```bash
secret-rotator --mode set-web-password
export FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

Start the daemon with web interface and scheduler:

```bash
secret-rotator
```

The web interface will be available at `http://localhost:8080`. See
[docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) before exposing it
beyond `localhost` — in particular, nothing in this application
terminates TLS, so a reverse proxy is required for anything outside a
trusted local network.

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

### Rotators

Available now, in `config.yaml` or from the dashboard:

- **Password Generator** (`type: password`): Configurable length and character requirements
- **API Key Generator** (`type: api_key`): Hex, base64, or alphanumeric formats with optional prefixes
- **JWT Secret** (`type: jwt_secret`): Cryptographically secure keys for HS256, HS384, HS512

Present in the code base but **not yet built when the daemon starts**, so a rotator of one of these types in `config.yaml` is skipped with a warning and the dashboard does not offer them: Database Password (PostgreSQL, MySQL, MongoDB), SSH Key Pair, TLS Certificate and OAuth2 Client Secret.

### Providers

- **File Storage** (`type: file`): JSON-based storage with encryption support

**AWS Secrets Manager** is not implemented yet: a provider of `type: aws` in `config.yaml` is skipped with a warning.

### Custom Extensions

Create custom providers and rotators using the plugin system. See the documentation for details on implementing custom handlers.

## Web Dashboard

Open `http://localhost:8080` while the application is running and sign in
(see [Running the Application](#running-the-application)). Every page has
its own URL.

| Page | What it does |
| --- | --- |
| **Overview** (`/`) | Totals, jobs that need attention, every job's status and next rotation, backup verification at a glance, recent activity, and **Rotate all** with live progress |
| **Jobs** (`/jobs`) | All jobs with search, per-job **Rotate** / **Retry**, **New job**, and any hidden `config.yaml` jobs to restore |
| **Job** (`/jobs/<name>`) | Run history (trigger, duration, result, failure reason), the job's backups with **Restore**, its configuration, **Edit**, **Pause** / **Resume**, **Delete**, **Reset to config.yaml** |
| **Backups** (`/backups`) | Filterable, paged list; a details drawer (values are always masked); **Restore** |
| **Backup health** (`/backup-health`) | Latest verification, which backups failed and why, the last runs, history for 7 / 30 / 90 days, and **Run verification now** |
| **Activity** (`/activity`) | The audit log as a live-updating log with level filter, search, and CSV / JSON export |
| **Providers** / **Rotators** | Add, edit, test (providers), preview a sample (rotators), delete, reset |
| **Settings** (`/settings`) | The settings the dashboard can change, in eight sections, saved together with an "Unsaved changes" bar |

### Managing configuration from the dashboard

Jobs, providers, rotators and settings can be changed from the dashboard.
**`config.yaml` is never written.** Changes are saved in a separate file in
the data volume, `data/dashboard-settings.json`, and layered over
`config.yaml` whenever the configuration is loaded, so `config.yaml` can
stay read-only (as the shipped `docker-compose.yml` recommends) or in
version control.

- **Precedence**: a value changed in the dashboard wins over `config.yaml`.
  Deleting something defined in `config.yaml` only *hides* it (it appears
  under "Hidden jobs" and can be restored); **Reset** drops the dashboard's
  changes and `config.yaml`'s version applies again.
- **Location**: set `SECRET_ROTATOR_DASHBOARD_SETTINGS` or
  `dashboard.settings_file` in `config.yaml` to move the file.
- **Credentials**: the S3 access keys for remote backups and the Redis URL
  can be entered in the dashboard. They are stored encrypted, in a key of
  their own, `data/dashboard-settings.key` (mode 0600), never shown again,
  and never written to `config.yaml`, the audit log or the logs.
- **What to back up**: `data/dashboard-settings.json` and
  `data/dashboard-settings.key` together, with the rest of the data
  directory. Each write also leaves `dashboard-settings.json.bak`. If the key
  is lost, the encrypted values are ignored with a warning and must be
  entered again; everything else is unaffected. A damaged settings file is
  ignored the same way: the daemon starts with `config.yaml` alone.
- **Restart**: changes to jobs, providers and rotators apply immediately,
  as do six settings (the default schedule, log level, backing up old
  secrets, retention days and two distributed-mode timeouts). Every other
  setting is flagged **Restart required** until the daemon restarts. A
  queue worker picks up changes before each rotation sweep; another web
  instance picks them up when it restarts.
- **Conflicts**: each form remembers the version of the settings it was
  loaded with; if something else changed them meanwhile, saving is refused
  instead of overwriting.
- **Not offered**: the Flask secret key, the admin credentials and the
  dashboard on/off switch can only be changed outside the dashboard. The
  master key file, encryption on/off, the backup directory and the log file
  are shown but read-only. A file provider's path must be a `.json` file
  inside the data directory (`dashboard.provider_root`).
- **Not available in this version** (listed on the Settings page rather than
  given controls that would do nothing): e-mail and Slack notifications (a
  job's `notification` flag is stored but nothing sends alerts), automatic
  retries, a rotation timeout, backup compression and a backup count limit.

## Security Features

### Encryption

- Fernet symmetric encryption (AES-128 in CBC mode with HMAC authentication)
- Master key rotation capability with automatic re-encryption (two-phase commit with rollback on failure)
- Encrypted backups with integrity verification, including Shamir's Secret Sharing split-key backups
- Secure key derivation from passphrases using PBKDF2 (600,000 iterations)

### Web Dashboard Security

- Session-based authentication (no default password — see [Running the Application](#running-the-application))
- CSRF protection on all state-changing endpoints
- Rate limiting on dashboard and API routes
- Path-traversal protection on backup restore endpoints
- Secret values are never sent to the browser: backup values stay masked, restoring is done on the server, and credentials entered in the dashboard are write-only and stored encrypted
- Every change made in the dashboard (jobs, providers, rotators, settings, restores, verifications, exports) is written to the audit log with who did it, never with values
- Input is validated server-side before anything is saved; a provider's file path is confined to the data directory
- Served via a production WSGI server (waitress), not Flask's development server

### Audit & Backup Integrity

- Append-only audit log of rotations, restores, logins, and login failures
- Automatic checksum verification for backups, with scheduled integrity checks
- File-based permissions (0600) for sensitive files (master key, backups, config)
- Best-effort sensitive-data masking in application logs (see the hardening guide for its limits)

**Not currently included:** per-secret access policies / RBAC (one
admin login covers the whole dashboard) and TLS termination (bring your
own reverse proxy). See [docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md)
for the full picture — what's handled for you, what's on you to set up,
and what's genuinely not built yet — before any production deployment.

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
black src/ tests/

# Linting (enforced in CI)
flake8 src/secret_rotator tests

# Type checking (enforced in CI)
mypy src/secret_rotator
```

## Configuration Reference

### Rotation Schedules

Every job rotates on `rotation.schedule` unless it sets its own `schedule`.
Jobs that share a schedule rotate together when it fires.

- `daily`: once a day at 02:00 (local time)
- `weekly`: every 7 days, counted from when the daemon started
- `monthly`: the 1st of each month at 02:00 (local time)
- `every_N_minutes`: custom minute interval (e.g., `every_30_minutes`)
- `every_N_hours`: custom hour interval (e.g., `every_12_hours`)

An unrecognised schedule, global or per job, stops the daemon at startup
with an error naming it, instead of silently never rotating.

### Backup Retention

```yaml
backup:
  retention:
    days: 90 # Keep backups for 90 days
```

### Logging Configuration

```yaml
logging:
  level: "INFO" # DEBUG, INFO, WARNING, ERROR, CRITICAL
  structured: true # JSON-formatted logs for aggregation
  separate_error_log: true # Separate file for errors
```

## Documentation

- [docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) — what to set up before a production deployment (read this first)
- [docs/BACKUP_ARCHITECTURE.md](docs/BACKUP_ARCHITECTURE.md) — how master-key backup and disaster recovery work
- [docs/BACKUP_QUICK_REFERENCE.md](docs/BACKUP_QUICK_REFERENCE.md) — command reference for `secret-rotator-backup`
- [docs/DOCKER_QUICKSTART.md](docs/DOCKER_QUICKSTART.md) — detailed Docker deployment guide
- [SECURITY.md](SECURITY.md) — vulnerability disclosure policy
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guidelines
- [CHANGELOG.md](CHANGELOG.md) — release history

## Upgrading

The full list is in the "Upgrade notes" of the [changelog](CHANGELOG.md).
The ones most likely to matter:

- A job's own `schedule` now really controls when it rotates (it used to be
  ignored), and an unrecognised schedule stops startup with an error.
- Changes made in the dashboard are saved in `data/dashboard-settings.json`
  and `data/dashboard-settings.key`, never in `config.yaml`. Back up both
  with the data directory.
- The previous dashboard, `/legacy`, `/health`, `web.use_flask` and three
  old verification endpoints are gone; see
  [Web Dashboard](#web-dashboard) for the new pages and
  `/api/verification/*` for the replacements.

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

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for
full setup instructions, coding standards, and PR guidelines. Short
version:

1. All tests pass: `pytest tests/`
2. Code follows style guidelines: `black src/ tests/` and `flake8 src/secret_rotator tests`
3. Changes to encryption, backup, auth, or the plugin loader get extra
   scrutiny — see [CONTRIBUTING.md's security-sensitive-changes section](CONTRIBUTING.md#security-sensitive-changes)
4. Documentation is updated for new features
5. Commit messages are clear and descriptive

## License

This project is licensed under the MIT License. See the [LICENSE.md](LICENSE.md) file for details.

## Security Considerations

This tool handles sensitive credentials. Before deploying beyond a
trusted local network, read
[docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) — it covers what's
handled for you out of the box (authentication, CSRF protection, rate
limiting, path-traversal protection), what you need to configure
(admin password, `FLASK_SECRET_KEY`, TLS via a reverse proxy), and what
isn't built yet (per-secret access policies, guaranteed log masking).

Quick summary:

- **Master Key**: back up using `secret-rotator-backup`, store backups
  in a different failure domain than your primary data volume
- **TLS**: this application does not terminate TLS itself — put a
  reverse proxy in front of it for anything beyond `localhost`
  (example in the hardening guide)
- **Key Rotation**: rotate the master encryption key periodically
  (recommended: every 90 days) with `secret-rotator --mode rotate-master-key`
- **Plugins**: the plugin system runs arbitrary code with full process
  privileges — only install plugins you trust as much as the core
  codebase

For security issues, please report privately via [GitHub Security
Advisories](https://github.com/othaime-en/secret-rotator/security/advisories/new)
rather than creating a public issue — see [SECURITY.md](SECURITY.md)
for the full disclosure policy and response timeline.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed history of changes.

## Support

For questions, feature requests, or bug reports:

- Open an issue on [GitHub](https://github.com/othaime-en/secret-rotator/issues)
- Check existing [discussions](https://github.com/othaime-en/secret-rotator/discussions)
