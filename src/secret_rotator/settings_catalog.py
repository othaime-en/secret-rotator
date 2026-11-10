"""
Which settings the dashboard can show and change, and how each is checked.

The list is deliberately limited to settings the program actually reads.
An earlier design listed controls for things that do nothing (a rotation
timeout, a backup master switch, e-mail and Slack alerts, ...); showing a
switch that changes nothing in a security tool is worse than not showing
it, because it suggests protection that is not there. Those are listed in
UNAVAILABLE instead, with the reason, so the page can say so plainly.

Each setting records:

* ``restart``: whether a change needs the daemon restarted to take effect.
  True is the safe claim; it is False only where the code reads the value
  when it uses it (or the dashboard applies it itself): the default
  schedule, the log level, whether to back up old secrets, backup
  retention and two distributed-mode timeouts.
* ``editable``: some settings are shown but cannot be changed here, with a
  reason. Changing the master key file, turning encryption off, or moving
  the backup or log directories from a web page could lock the operator
  out, make stored secrets unreadable, or redirect where the daemon
  writes, so those stay in config.yaml.
* ``secret``: credentials. They are write-only: never shown, never
  returned, stored encrypted (see config/overrides.py).

Not offered at all, on purpose: the Flask secret key, the admin
credentials and whether the dashboard is enabled. Anyone who could change
those from the dashboard could take it over.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from secret_rotator.config.overrides import SECRET_SETTING_KEYS
from secret_rotator.schedule_spec import parse_schedule

SECTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("rotation", "Rotation", "How and when secrets are rotated."),
    ("security", "Security & encryption", "Encryption at rest and the master key."),
    ("backups", "Backups & retention", "Local backups, verification and cleanup."),
    ("remote", "Remote backup", "Off-site copies to S3 or S3-compatible storage (MinIO, R2)."),
    (
        "distributed",
        "Distributed mode",
        "Required when running more than one instance. Uses Redis.",
    ),
    ("web", "Web & access", "The dashboard server."),
    ("logging", "Logging", "Application log output."),
    ("notifications", "Notifications", "Alerts for rotation results and failures."),
)

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SIZE = re.compile(r"^[1-9]\d{0,3}(KB|MB|GB)$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
_QUEUE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_REGION = re.compile(r"^[a-z0-9\-]{1,32}$")
_PREFIX = re.compile(r"^[A-Za-z0-9_.\-/]{0,200}$")
_HOSTNAME = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
)


@dataclass(frozen=True)
class Setting:
    key: str
    section: str
    label: str
    kind: str  # int bool choice time schedule size host url bucket region prefix queue secret
    default: Any = None
    help: str = ""
    restart: bool = True
    editable: bool = True
    reason: str = ""  # why it cannot be edited here
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    choices: Tuple[str, ...] = ()
    unit: str = ""
    schemes: Tuple[str, ...] = ()
    allow_userinfo: bool = False

    @property
    def secret(self) -> bool:
        return self.key in SECRET_SETTING_KEYS

    def describe(self) -> Dict[str, Any]:
        """The setting's constraints, for building the form."""
        data: Dict[str, Any] = {
            "key": self.key,
            "section": self.section,
            "label": self.label,
            "kind": self.kind,
            "help": self.help,
            "restart": self.restart,
            "editable": self.editable,
            "reason": self.reason,
            "secret": self.secret,
            "default": None if self.secret else self.default,
            "unit": self.unit,
        }
        if self.kind == "int":
            data["min"], data["max"] = self.minimum, self.maximum
        if self.kind == "choice":
            data["choices"] = list(self.choices)
        return data


def _s(key, section, label, kind, default=None, **kw) -> Setting:
    return Setting(key=key, section=section, label=label, kind=kind, default=default, **kw)


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

SETTINGS: Tuple[Setting, ...] = (
    # ---- rotation
    _s(
        "rotation.schedule",
        "rotation",
        "Default schedule",
        "schedule",
        "daily",
        help="Used by every job that does not have a schedule of its own.",
        restart=False,
    ),
    _s(
        "rotation.backup_old_secrets",
        "rotation",
        "Back up old secrets",
        "bool",
        True,
        help="Create an encrypted backup before every rotation.",
        restart=False,
    ),
    # ---- security
    _s(
        "security.encryption.enabled",
        "security",
        "Encrypt secrets at rest",
        "bool",
        True,
        help="Fernet symmetric encryption.",
        editable=False,
        reason="Turning this off or on for an existing installation can make stored secrets unreadable. Change it in config.yaml.",
    ),
    _s(
        "security.encryption.master_key_file",
        "security",
        "Master key file",
        "text",
        "data/.master.key",
        help="Generated on first run in the data volume.",
        editable=False,
        reason="Whoever can change this can point the daemon at a key of their choosing. Change it in config.yaml.",
    ),
    _s(
        "security.encryption.rotate_master_key_days",
        "security",
        "Warn when the master key is older than (days)",
        "int",
        90,
        minimum=1,
        maximum=3650,
        unit="days",
        help="At startup a warning is logged once the key is older than this. Rotating it is a separate, manual step.",
    ),
    # ---- backups
    _s("backup.encrypt_backups", "backups", "Encrypt backups", "bool", True),
    _s(
        "backup.storage_path",
        "backups",
        "Storage path",
        "text",
        "data/backup",
        editable=False,
        reason="Moving it would hide existing backups and redirect where the daemon writes. Change it in config.yaml.",
    ),
    _s(
        "backup.cleanup_time",
        "backups",
        "Cleanup time",
        "time",
        "03:00",
        help="Daily retention cleanup.",
    ),
    _s(
        "backup.verification_time",
        "backups",
        "Verification time",
        "time",
        "04:00",
        help="Daily integrity verification run.",
    ),
    _s("backup.verify_integrity", "backups", "Verify integrity", "bool", True),
    _s(
        "backup.full_verification_enabled",
        "backups",
        "Full verification",
        "bool",
        True,
        help="Weekly: decrypt and validate content.",
    ),
    _s(
        "backup.checksum_verification_enabled",
        "backups",
        "Checksum verification",
        "bool",
        True,
        help="Every six hours.",
    ),
    _s(
        "backup.retention.days",
        "backups",
        "Retention (days)",
        "int",
        90,
        minimum=1,
        maximum=3650,
        unit="days",
        help="Delete backups older than this.",
        restart=False,
    ),
    # ---- remote backup
    _s("backup.remote_backup.enabled", "remote", "Enable remote backup", "bool", False),
    _s("backup.remote_backup.bucket", "remote", "Bucket", "bucket"),
    _s("backup.remote_backup.region", "remote", "Region", "region", help="For example us-east-1."),
    _s("backup.remote_backup.prefix", "remote", "Prefix", "prefix", "rotator/backups/"),
    _s(
        "backup.remote_backup.endpoint_url",
        "remote",
        "Endpoint URL",
        "url",
        help="Leave empty for AWS S3. For MinIO or R2, the endpoint of your server.",
        schemes=("https", "http"),
    ),
    _s(
        "backup.remote_backup.access_key_id",
        "remote",
        "Access key ID",
        "secret",
        help="Leave unset to use the AWS credential chain. Only set keys for non-AWS providers. Stored encrypted.",
    ),
    _s(
        "backup.remote_backup.secret_access_key",
        "remote",
        "Secret access key",
        "secret",
        help="Stored encrypted and never shown again.",
    ),
    _s(
        "backup.remote_backup.upload_on_create",
        "remote",
        "Upload on create",
        "bool",
        False,
        help="Upload every backup immediately.",
    ),
    _s(
        "backup.remote_backup.sync_time",
        "remote",
        "Daily sync time",
        "time",
        "01:00",
        help="Bulk sync catches missed uploads.",
    ),
    # ---- distributed
    _s("distributed.enabled", "distributed", "Enable distributed locking", "bool", False),
    _s(
        "distributed.redis_url",
        "distributed",
        "Redis URL",
        "secret",
        help="The REDIS_URL environment variable takes priority. Stored encrypted: it usually contains a password.",
    ),
    _s(
        "distributed.master_key_lock_timeout",
        "distributed",
        "Master key lock timeout (s)",
        "int",
        600,
        minimum=10,
        maximum=86400,
        unit="seconds",
        restart=False,
    ),
    _s(
        "distributed.rotation_lock_timeout",
        "distributed",
        "Rotation lock timeout (s)",
        "int",
        3600,
        minimum=60,
        maximum=86400,
        unit="seconds",
    ),
    _s("distributed.queue_name", "distributed", "Queue name", "queue", "secret-rotator-rotations"),
    _s(
        "distributed.job_timeout",
        "distributed",
        "Worker job timeout (s)",
        "int",
        3600,
        minimum=60,
        maximum=86400,
        unit="seconds",
        restart=False,
    ),
    # ---- web
    _s(
        "web.port",
        "web",
        "Port",
        "int",
        8080,
        minimum=1,
        maximum=65535,
        help="If you change this, reach the dashboard on the new port after the restart.",
    ),
    _s(
        "web.host",
        "web",
        "Host",
        "host",
        "localhost",
        help="localhost keeps the dashboard private to this machine; 0.0.0.0 listens on every interface.",
    ),
    _s(
        "web.threads",
        "web",
        "Server threads",
        "int",
        8,
        minimum=1,
        maximum=64,
        help="Waitress request thread pool.",
    ),
    # ---- logging
    _s("logging.level", "logging", "Level", "choice", "INFO", choices=_LOG_LEVELS, restart=False),
    _s(
        "logging.structured",
        "logging",
        "Structured (JSON)",
        "bool",
        False,
        help="Recommended with ELK or Splunk.",
    ),
    _s("logging.console_enabled", "logging", "Log to the console", "bool", True),
    _s(
        "logging.file",
        "logging",
        "Log file",
        "text",
        "logs/rotation.log",
        editable=False,
        reason="It decides where the daemon writes. Change it in config.yaml.",
    ),
    _s(
        "logging.max_file_size",
        "logging",
        "Max file size",
        "size",
        "10MB",
        help="For example 10MB.",
    ),
    _s("logging.backup_count", "logging", "Files to keep", "int", 5, minimum=0, maximum=100),
    _s("logging.separate_error_log", "logging", "Separate error log", "bool", True),
)

BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}

# Controls the design offered that this version cannot honour, with why.
# (section, label, reason)
UNAVAILABLE: Tuple[Tuple[str, str, str], ...] = (
    (
        "rotation",
        "Retry attempts",
        "A rotation that fails is reported, not retried, so this would change nothing.",
    ),
    ("rotation", "Timeout", "The rotation engine does not use a timeout setting."),
    (
        "security",
        "Key backup passphrase and key backups path",
        "Master key backups are made with the manage-key-backups command line tool, not from the dashboard.",
    ),
    (
        "backups",
        "Enable backups, compression, max backups per secret",
        "Backups are always made and the daemon has no such options.",
    ),
    (
        "web",
        "Admin username and password",
        "Account changes are not available in the dashboard yet. Use the command line.",
    ),
    ("web", "Rate limiting", "Rate limiting is always on and is not configurable."),
    ("logging", "Mask sensitive data", "Masking of sensitive values in logs is always on."),
    (
        "notifications",
        "Email and Slack alerts",
        "Nothing in this version sends notifications. A job's notification flag is stored but has no effect yet.",
    ),
)


def unavailable_for(section: str) -> List[Dict[str, str]]:
    return [
        {"label": label, "reason": reason} for sec, label, reason in UNAVAILABLE if sec == section
    ]


# ---- validation -------------------------------------------------------------


def _url_problem(setting: Setting, value: str) -> Optional[str]:
    try:
        parsed = urlparse(value)
        host = parsed.hostname
    except ValueError:
        return "is not a valid URL"
    if parsed.scheme not in setting.schemes:
        return f"must start with {' or '.join(s + '://' for s in setting.schemes)}"
    if not host:
        return "needs a host name"
    if (parsed.username or parsed.password) and not setting.allow_userinfo:
        return "must not contain a user name or password"
    if any(c in value for c in " \t\r\n"):
        return "must not contain spaces"
    return None


def validate_value(setting: Setting, value: Any) -> Tuple[Any, Optional[str]]:
    """Check one value. Returns ``(canonical_value, None)`` or ``(None, problem)``."""
    kind = setting.kind

    if kind == "bool":
        return (value, None) if isinstance(value, bool) else (None, "must be on or off")

    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "must be a whole number"
        if (setting.minimum is not None and value < setting.minimum) or (
            setting.maximum is not None and value > setting.maximum
        ):
            return None, f"must be between {setting.minimum} and {setting.maximum}"
        return value, None

    if kind == "choice":
        return (
            (value, None)
            if value in setting.choices
            else (None, f"must be one of: {', '.join(setting.choices)}")
        )

    if not isinstance(value, str):
        return None, "must be text"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None, "must not contain control characters"

    if kind == "time":
        return (value, None) if _TIME.match(value) else (None, "must be a time like 03:00")
    if kind == "schedule":
        try:
            return parse_schedule(value).name, None
        except ValueError as e:
            return None, str(e)
    if kind == "size":
        return (
            (value, None)
            if _SIZE.match(value)
            else (None, "must be a size like 10MB (KB, MB or GB)")
        )
    if kind == "host":
        if value == "localhost" or _HOSTNAME.match(value):
            return value, None
        try:
            ipaddress.ip_address(value)
            return value, None
        except ValueError:
            return None, "must be an IP address or a host name"
    if kind == "bucket":
        return (
            (value, None)
            if _BUCKET.match(value)
            else (None, "must be 3-63 lowercase letters, numbers, dots or hyphens")
        )
    if kind == "region":
        return (
            (value, None)
            if _REGION.match(value)
            else (None, "must be a region name like us-east-1")
        )
    if kind == "prefix":
        if not _PREFIX.match(value) or value.startswith("/") or ".." in value:
            return (
                None,
                "may use letters, numbers and _ . - / (at most 200), not start with / or contain ..",
            )
        return value, None
    if kind == "queue":
        return (
            (value, None)
            if _QUEUE.match(value)
            else (None, "must be 1-64 letters, numbers and _ . : -")
        )
    if kind == "url":
        if value == "":
            return "", None  # empty = not set (for example AWS S3's default endpoint)
        if len(value) > 2048:
            return None, "is too long"
        problem = _url_problem(setting, value)
        return (None, problem) if problem else (value, None)
    if kind == "secret":
        if not value:
            return None, "must not be empty (use Reset to clear it)"
        if len(value) > 4096:
            return None, "is too long"
        if setting.key == "distributed.redis_url":
            problem = (
                _url_problem(
                    Setting(
                        key=setting.key,
                        section=setting.section,
                        label=setting.label,
                        kind="url",
                        schemes=("redis", "rediss", "unix"),
                        allow_userinfo=True,
                    ),
                    value,
                )
                if not value.startswith("unix://")
                else None
            )
            if problem:
                return None, problem
        return value, None
    return None, "has an unsupported type"  # pragma: no cover
