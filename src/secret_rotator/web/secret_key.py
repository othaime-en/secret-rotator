"""
Resolution logic for the Flask ``SECRET_KEY``.

This module centralizes safe resolution so every entry point (the real
app via ``main.py``, direct use of ``FlaskWebServer``, or tests calling
``create_app`` directly) gets the same guarantees:

1. An explicit, real key (from config or the ``FLASK_SECRET_KEY`` env
   var) is always preferred.
2. Known-insecure/placeholder values are never silently accepted.
3. In production, a missing/insecure key is a hard startup failure —
   not a silent fallback to something guessable.
4. Outside production (local dev, tests), a missing key falls back to a
   random per-process value so things still "just work", with a loud
   warning that sessions won't survive a restart.
"""

import os
import secrets

from secret_rotator.utils.logger import logger

# Values that must never be treated as a real secret key, even if they
# somehow end up in config or the environment.
_INSECURE_DEFAULTS = {
    None,
    "",
    "dev-key-change-in-production",
    "changeme",
    "change-me",
    "secret",
}


def _looks_like_unexpanded_placeholder(value: str) -> bool:
    """True for values like '${FLASK_SECRET_KEY}' that were copied from
    the example config verbatim without actually being substituted."""
    value = value.strip()
    return value.startswith("${") and value.endswith("}")


def resolve_secret_key(explicit_key: str = None) -> str:
    """
    Resolve a real Flask SECRET_KEY.

    Args:
        explicit_key: A value already present in app/config (e.g. from
            ``web.secret_key`` in config.yaml). May be ``None``.

    Returns:
        A secret key string, safe to assign to ``app.config['SECRET_KEY']``.

    Raises:
        RuntimeError: if running with ``SECRET_ROTATOR_ENV=production``
            and no real key is configured anywhere.
    """
    env = os.getenv("SECRET_ROTATOR_ENV", "development").strip().lower()

    # 1. An explicit, real (non-placeholder, non-default) key wins.
    if (
        explicit_key
        and explicit_key not in _INSECURE_DEFAULTS
        and not _looks_like_unexpanded_placeholder(explicit_key)
    ):
        return explicit_key

    # 2. Fall back to the environment variable directly (this is the
    #    actual interpolation the example config assumed existed).
    env_key = os.getenv("FLASK_SECRET_KEY")
    if env_key and env_key not in _INSECURE_DEFAULTS:
        return env_key

    # 3. Nothing usable configured. In production this must be a hard
    #    failure, not a silent weak default.
    if env == "production":
        raise RuntimeError(
            "No valid Flask SECRET_KEY configured for a production run "
            "(SECRET_ROTATOR_ENV=production). Set the FLASK_SECRET_KEY "
            "environment variable, or set 'web.secret_key' in config.yaml "
            "to a real value (not the '${FLASK_SECRET_KEY}' placeholder). "
            "Refusing to start with an insecure or missing session "
            "signing key.\n"
            "Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )

    # 4. Dev/test convenience: generate an ephemeral key for this
    #    process only. Sessions will not survive a restart, which is
    #    fine for local development but must never happen in prod —
    #    hence the SECRET_ROTATOR_ENV check above.
    generated = secrets.token_hex(32)
    logger.warning(
        "No FLASK_SECRET_KEY/web.secret_key configured — generating a "
        "random, EPHEMERAL SECRET_KEY for this process. Sessions will not "
        "survive a restart. Set FLASK_SECRET_KEY before deploying, and set "
        "SECRET_ROTATOR_ENV=production so a missing key fails startup "
        "loudly instead of silently generating one."
    )
    return generated