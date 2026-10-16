"""
What the dashboard may show about a rotator's configuration.

A rotator's config can hold things that must not be displayed: a database
rotator carries a host, database and account name, an OAuth rotator a
client ID. So nothing is shown by default. Each built-in rotator lists
the settings that are safe and useful, and anything else (including every
setting of a plugin rotator the dashboard has never heard of) stays
hidden.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from secret_rotator.rotators.advanced_rotators import (
    APIKeyRotator,
    CertificateRotator,
    DatabasePasswordRotator,
    JWTSecretRotator,
    OAuth2TokenRotator,
    SSHKeyRotator,
)
from secret_rotator.rotators.password_rotator import PasswordRotator


def _yes_no(value: Any) -> str:
    return "Yes" if value else "No"


def _password_characters(rotator) -> str:
    parts = []
    if getattr(rotator, "use_lowercase", True):
        parts.append("a-z")
    if getattr(rotator, "use_uppercase", True):
        parts.append("A-Z")
    if getattr(rotator, "use_numbers", True):
        parts.append("0-9")
    if getattr(rotator, "use_symbols", True):
        parts.append("symbols")
    return " ".join(parts) or "—"


# class -> (label, [(setting label, function(rotator) -> value)])
_Setting = Tuple[str, Callable[[Any], Any]]
_DISPLAY: List[Tuple[type, str, List[_Setting]]] = [
    (
        PasswordRotator,
        "Password",
        [
            ("Length", lambda r: r.length),
            ("Characters", _password_characters),
            ("Skip look-alikes", lambda r: _yes_no(r.exclude_ambiguous)),
        ],
    ),
    (
        APIKeyRotator,
        "API key",
        [
            ("Length", lambda r: r.length),
            ("Format", lambda r: r.format),
            ("Checksum", lambda r: _yes_no(r.include_checksum)),
        ],
    ),
    (
        DatabasePasswordRotator,
        "Database",
        [("Database type", lambda r: r.db_type), ("Length", lambda r: r.length)],
    ),
    (JWTSecretRotator, "JWT secret", [("Algorithm", lambda r: r.algorithm)]),
    (
        SSHKeyRotator,
        "SSH key",
        [("Key type", lambda r: r.key_type), ("Key size", lambda r: r.key_size)],
    ),
    (
        CertificateRotator,
        "Certificate",
        [("Validity (days)", lambda r: r.validity_days), ("Key size", lambda r: r.key_size)],
    ),
    (OAuth2TokenRotator, "OAuth2 token", [("Length", lambda r: r.length)]),
]


def describe_rotator(rotator: Optional[Any]) -> Dict[str, Any]:
    """Display-safe summary of a rotator.

    Returns ``{"type": label, "settings": [{"label", "value"}, ...]}``.
    An unknown or missing rotator gives type ``None`` and no settings.
    """
    if rotator is None:
        return {"type": None, "settings": []}

    for cls, label, settings in _DISPLAY:
        if isinstance(rotator, cls):
            shown = []
            for setting_label, read in settings:
                try:
                    value = read(rotator)
                except AttributeError:
                    continue
                shown.append({"label": setting_label, "value": str(value)})
            return {"type": label, "settings": shown}

    return {"type": type(rotator).__name__, "settings": []}
