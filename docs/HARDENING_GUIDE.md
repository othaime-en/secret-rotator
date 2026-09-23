# Production Hardening Guide

This guide covers what to do (and know) before exposing Secret Rotator's
web dashboard beyond your own laptop. It reflects the actual current
state of the code — what's already handled for you, and what's still on
you to set up.

If you're only ever accessing the dashboard via `localhost` on a machine
only you use, most of this doesn't apply. Read it before you put this
behind a real network, a load balancer, or a second user.

## Already handled by the application

You don't need to configure these — they're on by default:

- **Authentication.** Every dashboard/API route requires a session
  login (see [Set the admin password](#set-the-admin-password) below);
  only `/login` and the liveness probe `/api/healthz` are reachable
  without one.
- **CSRF protection** on all state-changing (POST) endpoints, via
  Flask-WTF.
- **Path traversal protection** on backup restore — restore paths are
  resolved and checked against the backup directory before any file is
  opened.
- **Rate limiting** on the dashboard and API (Flask-Limiter), keyed by
  authenticated username where available, falling back to source IP.
- **A real WSGI server** (waitress) instead of Flask's development
  server — the dev server explicitly isn't hardened against slow
  clients or production traffic.
- **Audit logging** of rotations, restores, logins, and login failures
  to an append-only log (`logs/audit.log` by default, configurable via
  `audit.log_file`). This is logging, not access control — see
  [What this does _not_ give you](#what-this-does-not-give-you) below.
- **Secret masking in logs** — the log formatter redacts values
  adjacent to keywords like `password`, `secret`, `token`. This is a
  best-effort net, not a guarantee — see the note in that section.

## Your responsibility

### Set the admin password

There's no default password. Set one before starting the app in
anything other than local dev:

```bash
secret-rotator --mode set-web-password
```

This writes a hash to your config (or prints one you can put in
`SECRET_ROTATOR_ADMIN_PASSWORD_HASH`). You can also set the username
via `SECRET_ROTATOR_ADMIN_USERNAME` (default: `admin`) or
`web.auth.username` in config. Environment variables win over config
if both are set.

For Docker deployments, add both to your `.env`:

```bash
SECRET_ROTATOR_ADMIN_USERNAME=your-username
SECRET_ROTATOR_ADMIN_PASSWORD_HASH=<output of set-web-password>
```

### Set `FLASK_SECRET_KEY`

Also required outside local dev. A missing or placeholder value (the
app checks for and rejects known-insecure defaults like
`dev-key-change-in-production`) is a **hard startup failure** in
production mode — the app won't silently start with a guessable
session-signing key.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the result in `FLASK_SECRET_KEY` (env) or `web.secret_key` (config).

### Put TLS in front of it

**Nothing in this application terminates TLS.** The Docker image and
`docker-compose.yml` serve plain HTTP on port 8080 — this is
intentional (TLS termination is a deployment concern, not something to
bake into the app), but it means _you_ need a reverse proxy in front of
it before this leaves a trusted network. Don't map port 8080 directly
to a public interface.

Minimal example using [Caddy](https://caddyserver.com/) (automatic
HTTPS via Let's Encrypt, no manual cert management):

**`Caddyfile`:**

```caddyfile
secrets.yourdomain.com {
    reverse_proxy secret-rotator:8080
}
```

**`docker-compose.proxy.yml`** (overlay — run with
`docker-compose -f docker-compose.yml -f docker-compose.proxy.yml up -d`):

```yaml
services:
  secret-rotator:
    # Remove the public port mapping - only Caddy talks to this container.
    ports: []
    expose:
      - "8080"

  caddy:
    image: caddy:2-alpine
    container_name: secret-rotator-proxy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    networks:
      - secret-rotator-network

volumes:
  caddy-data:
  caddy-config:

networks:
  secret-rotator-network:
    external: false
```

If you're running behind an existing load balancer or ingress
controller (Kubernetes, an ALB, etc.) that already terminates TLS, you
don't need Caddy specifically — just make sure _something_ does, and
that the container itself is never reachable on plain HTTP from outside
your private network.

### Network isolation

- Don't publish port 8080 on `0.0.0.0` of a host with a public IP
  without a reverse proxy in front, per above.
- If you're running the [distributed mode](#distributed-mode-redis)
  (Redis-backed locking and job queue), make sure Redis itself isn't
  publicly reachable either — it has no auth by default in the example
  compose files.
- Database credentials the rotators connect with should follow your
  normal network-segmentation practices; this tool doesn't change what
  network access the database rotator needs to actually rotate a
  password.

### Plugins run with full process privileges

The plugin system (`plugin_system.py`) dynamically imports any `.py`
file dropped into `plugins/providers/`, `plugins/rotators/`, etc. This
is by design — it's how you'd add a custom provider without forking the
project — but it means **a plugin file has the same access as the main
process**: it can read the master key, read/write secrets, and make
arbitrary network/filesystem calls. It's not sandboxed.

Only install plugins you wrote yourself or trust as much as the core
codebase. Review any third-party plugin's source the same way you would
review a dependency before installing it, not the way you'd install a
config file.

### Distributed mode (Redis)

If you enable `distributed.enabled: true` (multi-instance deployment,
shared job queue and locking — see `docs/BACKUP_ARCHITECTURE.md` for
the underlying design):

- Use a Redis instance with authentication enabled and not exposed
  outside your private network.
- The distributed lock prevents two instances from racing on the same
  master-key rotation, but it doesn't replace network isolation for
  Redis itself.

### Master key backups

Back up your master key using the built-in tooling, not ad hoc copies:

```bash
secret-rotator-backup create-encrypted     # passphrase-protected
secret-rotator-backup create-split --shares 5 --threshold 3   # Shamir
```

Store backups in a different failure domain than your primary data
volume — a single host/volume loss shouldn't be able to take out both
your secrets and every backup of the key that decrypts them. See
`docs/BACKUP_ARCHITECTURE.md` for the full disaster-recovery design and
`docs/BACKUP_QUICK_REFERENCE.md` for command reference.

### Rotate the master key periodically

```bash
secret-rotator --mode rotate-master-key
```

Recommended: every 90 days, or immediately if you suspect the key file
may have been exposed (e.g., a misconfigured backup, a compromised
host). Rotation re-encrypts all managed secrets with a new key using a
two-phase-commit flow with automatic rollback on failure — see
`encryption_manager.py` if you want the details.

## What this does _not_ give you

Being direct about the current limits, rather than letting the feature
list imply more than what's built:

- **No per-secret access policies or RBAC.** There's one admin login
  for the whole dashboard. Audit logging tells you _who did what_
  after the fact; it doesn't let you restrict _who can do what_ ahead
  of time (e.g., "this user can rotate `stripe_key` but not
  `db_password`"). If you need that, you're currently looking at
  either a single shared admin credential per team, or scripting
  access at the reverse-proxy layer.
- **Log masking is best-effort, not guaranteed.** The masking filter
  matches values in a `keyword[:=]value` shape next to trigger words.
  A log line like `logger.info(f"Rotated value for db_password:
{new_secret}")` won't match and won't be masked. Don't rely on it as
  the only thing standing between a secret and your log aggregator —
  review what you log, especially in custom plugins.
- **No built-in multi-tenancy.** One instance protects one set of
  secrets for one team/admin group. Running it for multiple unrelated
  teams means running multiple instances with separate data volumes,
  not partitioning within a single instance.

If any of these matter for your deployment, please open an issue or
discussion — they're reasonable asks, just not implemented yet.
