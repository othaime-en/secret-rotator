"""
Viewing and changing settings from the dashboard.

Changes are saved in the dashboard's own settings file (config/overrides.py),
never in config.yaml. Only settings in settings_catalog.py can be touched;
anything else, including every setting that would let someone take over the
dashboard, is refused. A whole form is validated before anything is saved
and then written in one step.

Whether a change needs a restart is tracked against what *this process*
started with: a setting is "restart pending" only while its current value
differs from the one the running code read, so changing something and
changing it back leaves nothing pending.

Credentials are write-only: the listing says whether one is set and
where it came from, never its value.
"""

import hashlib
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.config.settings import settings as default_settings
from secret_rotator.settings_catalog import (
    BY_KEY,
    SECTIONS,
    SETTINGS,
    Setting,
    unavailable_for,
    validate_value,
)
from secret_rotator.utils.logger import logger


class SettingsValidationError(Exception):
    """One or more settings are not acceptable. ``fields`` maps each
    setting key to what is wrong with it."""

    def __init__(self, fields: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


def _lookup(config: Any, dotted: str) -> Tuple[bool, Any]:
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _fingerprint(value: Any) -> str:
    """A stable digest of a value, so changes can be detected without ever
    keeping (or exposing) secret values around."""
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


class SettingsAdminService:
    def __init__(self, engine, settings_obj=None, audit=None):
        self.engine = engine
        self.settings = settings_obj if settings_obj is not None else default_settings
        self.audit = audit if audit is not None else default_audit_log
        self._hooks: Dict[str, Callable[[Any], None]] = {
            "rotation.schedule": self._apply_schedule,
            "logging.level": self._apply_log_level,
        }
        # What the running process read at startup, for "restart pending".
        self._startup = {s.key: self._current_fingerprint(s) for s in SETTINGS if s.restart}

    # ---- reading -----------------------------------------------------------

    @property
    def revision(self) -> int:
        return self.settings.overrides.revision

    def _effective(self, setting: Setting) -> Any:
        return self.settings.get(setting.key, setting.default)

    def _current_fingerprint(self, setting: Setting) -> str:
        return _fingerprint(self._effective(setting))

    def _source(self, setting: Setting) -> str:
        store = self.settings.overrides
        if setting.key in store.overridden_settings() or setting.key in store.secrets_set():
            return "dashboard"
        found, value = _lookup(self.settings.base_config, setting.key)
        if found and value not in (None, ""):
            return "config"
        return "default"

    def pending_restart(self) -> List[str]:
        return sorted(
            s.key
            for s in SETTINGS
            if s.restart and self._startup.get(s.key) != self._current_fingerprint(s)
        )

    def _describe(self, setting: Setting, pending: List[str]) -> Dict[str, Any]:
        data = setting.describe()
        source = self._source(setting)
        data["source"] = source
        data["restart_pending"] = setting.key in pending
        if setting.secret:
            data["value"] = None
            data["is_set"] = source != "default"
        else:
            value = self._effective(setting)
            data["value"] = value
        return data

    def listing(self) -> Dict[str, Any]:
        self.settings.reload_overrides()
        pending = self.pending_restart()
        sections = []
        for key, label, description in SECTIONS:
            sections.append(
                {
                    "key": key,
                    "label": label,
                    "description": description,
                    "settings": [self._describe(s, pending) for s in SETTINGS if s.section == key],
                    "unavailable": unavailable_for(key),
                }
            )
        store = self.settings.overrides
        return {
            "sections": sections,
            "pending_restart": pending,
            "warnings": list(store.warnings),
            "file_error": store.load_error,
            "revision": store.revision,
        }

    # ---- validation --------------------------------------------------------

    def _after(self, key: str, validated: Dict[str, Any], reset: List[str]) -> Any:
        """What a setting will be once the pending change is applied."""
        setting = BY_KEY[key]
        if key in validated:
            return validated[key]
        if key in reset:
            found, value = _lookup(self.settings.base_config, key)
            return value if found else setting.default
        if setting.secret:
            return self._source(setting) != "default" or None
        return self._effective(setting)

    def _cross_checks(
        self, validated: Dict[str, Any], reset: List[str], problems: Dict[str, str]
    ) -> None:
        def after(key):
            return self._after(key, validated, reset)

        if after("backup.remote_backup.enabled") and not after("backup.remote_backup.bucket"):
            problems.setdefault(
                "backup.remote_backup.bucket", "is required when remote backup is enabled"
            )

        if after("distributed.enabled"):
            has_url = bool(after("distributed.redis_url")) or bool(os.getenv("REDIS_URL"))
            if not has_url:
                problems.setdefault(
                    "distributed.redis_url",
                    "is required when distributed locking is enabled (or set REDIS_URL)",
                )

        key_id, secret = after("backup.remote_backup.access_key_id"), after(
            "backup.remote_backup.secret_access_key"
        )
        if bool(key_id) != bool(secret):
            missing = (
                "backup.remote_backup.secret_access_key"
                if key_id
                else "backup.remote_backup.access_key_id"
            )
            problems.setdefault(
                missing,
                "must be set together with the other key (or neither, to use the credential chain)",
            )

    # ---- changing ----------------------------------------------------------

    def update(
        self,
        changes: Any,
        reset: Any,
        actor: str,
        revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save a set of changes and resets in one step.

        Args:
            changes: ``{key: new value}``.
            reset: keys whose dashboard value is dropped (config.yaml's
                value, or the default, applies again).
            revision: the settings revision the client loaded, for conflict
                detection.

        Returns:
            ``saved`` and ``reset`` (the keys that changed), and
            ``restart_required`` (every key that now needs a restart).

        Raises:
            SettingsValidationError: nothing was saved; ``fields`` says why.
        """
        if not isinstance(changes, dict) or not isinstance(reset, list):
            raise SettingsValidationError({"_": "send changes as an object and reset as a list"})

        problems: Dict[str, str] = {}
        validated: Dict[str, Any] = {}

        for key, value in changes.items():
            setting = BY_KEY.get(key)
            if setting is None:
                problems[str(key)] = "unknown setting"
            elif not setting.editable:
                problems[key] = "cannot be changed here: " + setting.reason
            else:
                canonical, problem = validate_value(setting, value)
                if problem:
                    problems[key] = problem
                else:
                    validated[key] = canonical

        reset_keys: List[str] = []
        for key in reset:
            setting = BY_KEY.get(key) if isinstance(key, str) else None
            if setting is None:
                problems[str(key)] = "unknown setting"
            elif not setting.editable:
                problems[key] = "cannot be changed here: " + setting.reason
            elif key in changes:
                problems[key] = "cannot be both changed and reset"
            else:
                reset_keys.append(key)

        if not problems:
            self._cross_checks(validated, reset_keys, problems)
        if problems:
            raise SettingsValidationError(problems)

        self.settings.reload_overrides()
        store = self.settings.overrides
        # Skip changes that would not change anything.
        to_set = {
            key: value
            for key, value in validated.items()
            if BY_KEY[key].secret
            or self._effective(BY_KEY[key]) != value
            or key in store.overridden_settings()
        }
        to_reset = [
            key
            for key in reset_keys
            if key in store.overridden_settings() or key in store.secrets_set()
        ]
        if not to_set and not to_reset:
            return {"saved": [], "reset": [], "restart_required": self.pending_restart()}

        store.change_settings(to_set, to_reset, actor, expected_revision=revision)
        self.settings.reload_overrides()

        for key in list(to_set) + to_reset:
            hook = self._hooks.get(key)
            if hook is not None:
                try:
                    hook(self._effective(BY_KEY[key]))
                except Exception as e:  # saved either way; the restart applies it
                    logger.error(f"Saved setting {key} but could not apply it live: {e}")

        self.audit.log(
            "settings_update",
            actor,
            details={"changed": sorted(to_set), "reset": sorted(to_reset)},
        )
        return {
            "saved": sorted(to_set),
            "reset": sorted(to_reset),
            "restart_required": self.pending_restart(),
        }

    # ---- applying live -----------------------------------------------------

    def _apply_schedule(self, value: Any) -> None:
        scheduler = getattr(self.engine, "scheduler", None)
        if scheduler is not None:
            scheduler.set_default_schedule(value)

    @staticmethod
    def _apply_log_level(value: Any) -> None:
        from secret_rotator.utils.logger import set_log_level

        set_log_level(str(value))
