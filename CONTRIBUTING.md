# Contributing to Secret Rotator

Thanks for considering a contribution. This project handles credentials
and encryption keys, so the bar for changes here is a little higher than
"tests pass" — please read the [Security-sensitive changes](#security-sensitive-changes)
section below before touching anything in `encryption_manager.py`,
`key_backup_manager.py`, `web/auth.py`, or the plugin loader.

## Getting set up

```bash
git clone https://github.com/othaime-en/secret-rotator.git
cd secret-rotator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -e ".[dev]"
```

`.[dev]` installs the package in editable mode plus pytest, pytest-cov,
black, flake8, mypy, and the test-only mocking libraries (fakeredis,
moto). If your change touches a database rotator or the distributed
lock/job-queue code, you'll also want:

```bash
pip install -e ".[all]"   # databases + advanced + distributed extras
```

## Running the test suite

```bash
pytest tests/                          # full suite, with coverage
pytest tests/test_encryption_manager.py -v   # a single file
pytest tests/ -k "test_rotation"       # by name
```

Run this from the **repo root**, not from inside `tests/` — coverage
configuration in `pyproject.toml` (the `web_interface.py` exclusion, the
64% floor) is only discovered relative to the current directory.

A few tests (in `test_distributed_lock.py`, `test_job_queue.py`,
`test_remote_backup.py`) need `fakeredis`/`moto`, which `.[dev]` already
installs — you don't need a real Redis server or AWS account to run the
full suite.

### Coverage

CI enforces a coverage floor (`--cov-fail-under`, currently 64%, see
`pyproject.toml`). New code doesn't need to hit 100%, but:

- **Any change to `encryption_manager.py`, `key_backup_manager.py`, or a
  rotator's `generate_new_secret()`/`validate_secret()`** should come
  with tests that actually exercise failure modes (wrong key, corrupted
  input, edge-case lengths), not just the happy path. Two real bugs
  shipped in this codebase were caught exactly this way — a generator
  that silently produced ~1% invalid passwords, and a backup-grouping
  bug — both invisible on the happy path alone.
- If you can't get a change above the floor on its own, that's fine —
  just don't be the PR that drops the overall number.

## Code style

```bash
black src/ tests/          # auto-format
flake8 src/secret_rotator tests   # lint
mypy src/secret_rotator --exclude 'web_interface.py'   # type check
```

`black` and `flake8` are enforced in CI and will block merging.

`mypy` currently runs as **informational only** in CI (not a hard gate)
— this codebase's first mypy pass surfaced a real backlog of
pre-existing type errors that need individual review rather than a bulk
fix. If your change fixes one, great; if it introduces a new one,
please fix it before opening the PR anyway — the check is
non-blocking for the existing backlog, not an invitation to add to it.

`web_interface.py` is excluded from both flake8 and mypy — it's
deprecated and scheduled for removal in 1.4.0 (see `CHANGELOG.md`), and
isn't worth polishing lint debt in code that's on its way out. Please
don't add new features there; extend `src/secret_rotator/web/` (the
Flask blueprint implementation) instead.

## Security-sensitive changes

If your PR touches any of the following, please say so explicitly in
the PR description and expect closer review:

- `encryption_manager.py`, `key_backup_manager.py`, `passphrase_manager.py`
- `web/auth.py`, `web/secret_key.py`, CSRF/session handling
- `plugin_system.py` (dynamic `importlib` loading — plugins run with
  full process privileges; see the warning in `docs/HARDENING_GUIDE.md`)
- Anything in `backup_manager.py`'s restore path (path handling in
  particular — this project has had a real path-traversal
  vulnerability here before)
- Dependency version bumps for `cryptography`, `Flask`, `Werkzeug`, or
  any database driver

For these, please include:

- Tests for the failure/attack path, not just the happy path
- A one-line note on what you verified manually (e.g. "confirmed
  restoring from a backup file outside `backup_dir/` still raises")

If you're reporting a vulnerability rather than fixing one, see
[SECURITY.md](SECURITY.md) instead — please don't open a public PR or
issue with exploit details.

## Commit messages

No strict format required, but please:

- Use a short imperative summary line (`fix: ...`, `test: ...`,
  `docs: ...`, `feat: ...` prefixes are appreciated but not required)
- Explain _why_, not just _what_, in the body for anything non-trivial
  — especially for bug fixes, where "what was actually broken and how
  you confirmed the fix" is more useful to future readers than a
  restatement of the diff

## Opening a pull request

1. Fork the repo and create a branch from `main`
2. Make your change, with tests
3. Run `black src/ tests/ && flake8 src/secret_rotator tests && pytest tests/`
   locally — this is exactly what CI runs
4. Update `CHANGELOG.md` under an "Unreleased" heading if your change
   is user-facing
5. Open the PR against `main`; the description should explain the
   change and, for anything non-obvious, why this approach over
   alternatives

Small, focused PRs get reviewed faster than large ones. If you're
planning a large change (a new provider type, a new rotator, a
significant refactor), consider opening an issue first to discuss the
approach before investing a lot of time in it.

## Adding a new provider or rotator

The plugin system (`src/secret_rotator/plugin_system.py`) is the
supported extension point — you don't need to modify core files to add
support for a new secret type or storage backend. See the
`providers/base.py` / `rotators/base.py` abstract base classes and the
example plugin scaffolded under `plugins/` on first run
(`*.py.example` files) for the interface to implement.

If your provider/rotator is broadly useful (not specific to your
infrastructure), consider contributing it upstream via a normal PR
rather than keeping it as a local plugin — it'll get test coverage and
maintenance from the project going forward.

## Questions

Open a [GitHub Discussion](https://github.com/othaime-en/secret-rotator/discussions)
for anything that isn't a specific bug report or feature request.
