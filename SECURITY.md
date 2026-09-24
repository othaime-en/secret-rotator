# Security Policy

Secret Rotator manages database credentials, API keys, JWT secrets, and
the master encryption key that protects all of them at rest. If you find
a security issue, please report it privately rather than opening a public
issue — a public issue about, say, an auth bypass or a path-traversal bug
is a live exploit notice for every unpatched deployment until a fix ships.

## Reporting a vulnerability

**Please use [GitHub Security Advisories](https://github.com/othaime-en/secret-rotator/security/advisories/new)
to report privately.** This is the preferred channel: it doesn't require
sharing an email address or PGP key, keeps the report and any discussion
private until a fix is ready, and lets us coordinate a CVE and a
coordinated disclosure date with you directly through GitHub.

When reporting, please include:

- A description of the vulnerability and its impact (what an attacker
  could do, and what they'd need — network access to the dashboard,
  a malicious plugin file, a crafted config value, etc.)
- Steps to reproduce, or a proof-of-concept if you have one
- The affected version(s) (`pip show secret-rotator` or the Docker image tag)
- Any suggested fix or mitigation, if you have one — not required

You don't need to have a fix ready, and you don't need to be a security
researcher by trade — a "this endpoint doesn't check X" report is exactly
as welcome as a full writeup.

### What to expect

- **Acknowledgement:** within 5 business days.
- **Initial assessment** (severity, affected versions): within 10
  business days of acknowledgement.
- **Fix timeline:** varies by severity, but we aim to ship a patch
  release for Critical/High findings within 30 days of confirming the
  report. We'll keep you updated if that slips.
- **Credit:** with your permission, we'll credit you in the release
  notes and the GitHub Security Advisory. Anonymous reporting is fine
  too — just say so.

We don't currently run a paid bug bounty program.

## Supported versions

| Version         | Supported |
| --------------- | --------- |
| 1.2.x (current) | ✅        |
| < 1.2           | ❌        |

Security fixes land on the latest release line. If you're running an
older version, please upgrade before reporting — we won't backport
fixes to unsupported versions, but we'll still confirm whether a
current release is also affected.

## Scope

In scope:

- The `secret_rotator` Python package (rotation engine, encryption,
  backup, plugin system, CLI tools)
- The web dashboard (`src/secret_rotator/web/`)
- The Docker image and `docker-compose*.yml` as shipped in this repo

Out of scope:

- Vulnerabilities in third-party dependencies — please report those
  upstream (though we do want to know if a dependency's CVE actually
  affects us here; `pip-audit` runs in CI, but a report is still
  useful if you've found a real exploitable path through it)
- Issues that require an attacker to already have write access to the
  host filesystem, the master key file, or the config file — at that
  point they already have the keys to everything this tool protects
- Missing security _hardening_ that's genuinely optional and documented
  as such (see [docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md)) —
  though if you think something documented as optional shouldn't be,
  that's a great thing to raise as a regular issue or discussion

## A note on the current security posture

This project has an internal [production-readiness security
audit](docs/) informing its roadmap; most Critical/High findings from
that audit (unauthenticated dashboard, path traversal in backup
restore, hardcoded Flask secret key, no CSRF protection, no audit
logging, dependency-confusion risk) have been fixed as of this
version. Known remaining gaps are tracked in
[docs/HARDENING_GUIDE.md](docs/HARDENING_GUIDE.md) rather than hidden —
please read it before deploying to anything beyond a trusted local
network.
