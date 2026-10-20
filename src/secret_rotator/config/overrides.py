"""
The dashboard's own settings file, layered over config.yaml.

config.yaml is never written by the dashboard. It is often mounted
read-only (the shipped docker-compose recommends it), often kept in
version control, and is commented prose that a program rewriting it would
destroy. Instead, changes made in the dashboard are saved here, in a small
JSON file in the data volume, and merged over config.yaml when the
configuration is loaded. Everything that reads ``settings`` therefore sees
the combined result without knowing the dashboard exists.

What can be overridden:

* ``jobs``       add a job, replace a config.yaml job, hide one, or pause one
* ``providers``  add, replace or hide a provider
* ``rotators``   add, replace or hide a rotator
* ``settings``   individual values by dotted key (``rotation.retry_attempts``)

Removing a config.yaml item does not edit config.yaml: it records a
"removed" marker that hides it. Resetting an item deletes the marker (or
the replacement) and config.yaml's version is back.

File format (version 1)::

    {
      "version": 1, "revision": 7,
      "updated_at": "2026-10-17T10:00:00+00:00", "updated_by": "admin",
      "jobs":      {"<name>": {"definition": {...}, "removed": false, "paused": false}},
      "providers": {"<name>": {"definition": {...}} | {"removed": true}},
      "rotators":  {"<name>": {"definition": {...}} | {"removed": true}},
      "settings":  {"rotation.retry_attempts": 5}
    }

Safety properties:

* Every write is atomic (temporary file, fsync, rename) and the previous
  version is kept as ``<file>.bak``.
* The file is created with mode 0600.
* Each change re-reads the file under a lock, so two processes (or two
  requests) changing different things never overwrite each other.
  ``expected_revision`` gives callers compare-and-swap when they need it.
* An unreadable file never stops the application: it is ignored (config.yaml
  alone applies), ``load_error`` says why, and the first write moves the
  broken file aside instead of overwriting it.
* Item definitions and setting values are never written to logs.
* Settings that hold credentials (SECRET_SETTING_KEYS) are stored only
  encrypted, with a key of their own (see "Secret settings" below).

Secret settings
---------------
A few settings are credentials: the S3 access keys for remote backups and
the Redis URL (which usually carries a password). They are written to the
file as ``{"__encrypted__": "<Fernet token>"}``, never as plain text, and
the configuration seen by the rest of the program (``apply()``) contains
them decrypted, exactly as if they had been written in config.yaml.

The key is a Fernet key in its own file next to the settings file
(``dashboard-settings.key``, mode 0600), created the first time a secret
is saved. It is deliberately not the master key: rotating the master key
re-encrypts the secrets and backups it knows about, and would leave these
values undecryptable. If the key is lost, the encrypted values are ignored
with a warning (they can be entered again); nothing else is affected. As
with the master key, protecting the data directory protects both.

A secret setting found in the file as plain text (e.g. hand-edited) is
ignored with a warning rather than used.
"""

import copy
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional

from cryptography.fernet import Fernet, InvalidToken

try:  # POSIX only; Windows falls back to the in-process lock alone.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

# Not secret_rotator.utils.logger: that module imports settings, which imports
# this one. A child of the application's logger reaches the same handlers
# (and their sensitive-data filters) without the import cycle.
logger = logging.getLogger("secret-rotator.config.overrides")

FORMAT_VERSION = 1
KINDS = ("jobs", "providers", "rotators")

# Settings that are credentials: stored encrypted, never returned in clear.
SECRET_SETTING_KEYS: FrozenSet[str] = frozenset(
    {
        "backup.remote_backup.access_key_id",
        "backup.remote_backup.secret_access_key",
        "distributed.redis_url",
    }
)
WRAPPER_KEY = "__encrypted__"
MAX_SECRET_LENGTH = 4096

MAX_NAME_LENGTH = 100
MAX_DEFINITION_BYTES = 20_000
MAX_SETTING_BYTES = 10_000
MAX_KEY_DEPTH = 6

_KEY = re.compile(r"^[A-Za-z0-9_]{1,64}(\.[A-Za-z0-9_]{1,64})*$")

SOURCE_CONFIG = "config"  # untouched, exactly as in config.yaml
SOURCE_MODIFIED = "modified"  # a config.yaml item replaced or paused here
SOURCE_DASHBOARD = "dashboard"  # exists only in the dashboard's file
SOURCE_REMOVED = "removed"  # a config.yaml item hidden here


class OverridesError(Exception):
    """A change was rejected (bad name, bad value, ...)."""


class ConflictError(OverridesError):
    """expected_revision no longer matches: someone else changed the file."""


def validate_name(name: Any, what: str = "name") -> str:
    if not isinstance(name, str):
        raise OverridesError(f"{what} must be text")
    if not name or len(name) > MAX_NAME_LENGTH:
        raise OverridesError(f"{what} must be 1-{MAX_NAME_LENGTH} characters")
    if name != name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise OverridesError(
            f"{what} must not have leading or trailing spaces or control characters"
        )
    return name


def validate_setting_key(key: Any) -> str:
    if not isinstance(key, str) or not _KEY.match(key) or key.count(".") + 1 > MAX_KEY_DEPTH:
        raise OverridesError(
            "setting keys are dotted names such as rotation.retry_attempts "
            f"(letters, digits and underscores, at most {MAX_KEY_DEPTH} levels)"
        )
    return key


def _json_size(value: Any, what: str) -> int:
    try:
        return len(json.dumps(value))
    except (TypeError, ValueError) as e:
        raise OverridesError(
            f"{what} must be plain data (text, numbers, true/false, lists, objects)"
        ) from e


def _empty() -> Dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "revision": 0,
        "updated_at": None,
        "updated_by": None,
        "jobs": {},
        "providers": {},
        "rotators": {},
        "settings": {},
    }


class OverridesStore:
    """Reads, merges and atomically writes the dashboard settings file."""

    def __init__(
        self,
        path: Path,
        base_provider: Callable[[], Dict[str, Any]],
        secret_keys: FrozenSet[str] = SECRET_SETTING_KEYS,
        key_path: Optional[Path] = None,
    ):
        """
        Args:
            path: where the JSON file lives (it need not exist yet).
            base_provider: returns config.yaml's parsed contents; needed to
                tell "hide a config.yaml item" from "delete a dashboard one".
            secret_keys: dotted setting keys that are stored encrypted.
            key_path: the encryption key file; by default
                ``dashboard-settings.key`` beside ``path``.
        """
        self.path = Path(path)
        self._base_provider = base_provider
        self.secret_keys = frozenset(secret_keys)
        self._key_path = Path(key_path) if key_path else None
        self._warned_key_permissions = False
        self._thread_lock = threading.RLock()
        self._data: Dict[str, Any] = _empty()
        self.load_error: Optional[str] = None
        self.warnings: List[str] = []

    # ---- reading -----------------------------------------------------------

    @property
    def revision(self) -> int:
        return self._data["revision"]

    @property
    def updated_at(self) -> Optional[str]:
        return self._data.get("updated_at")

    @property
    def updated_by(self) -> Optional[str]:
        return self._data.get("updated_by")

    def load(self) -> Dict[str, Any]:
        """Read the file from disk (a missing file means no overrides).

        Never raises for a bad file: sets ``load_error`` and uses no
        overrides instead, so a damaged file cannot stop the daemon.
        """
        with self._thread_lock:
            self._data, self.load_error, self.warnings = self._read_file()
            if self.load_error:
                logger.error(
                    f"Ignoring dashboard settings file {self.path}: {self.load_error}. "
                    "Only config.yaml applies until it is fixed."
                )
            for warning in self.warnings:
                logger.warning(f"Dashboard settings file: {warning}")
            return self._data

    def _read_file(self):
        warnings: List[str] = []
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return _empty(), None, warnings
        except OSError as e:
            return _empty(), f"cannot read the file ({e.strerror or e})", warnings

        try:
            data = json.loads(raw)
        except ValueError as e:
            return _empty(), f"not valid JSON ({e})", warnings
        if not isinstance(data, dict):
            return _empty(), "the top level must be an object", warnings
        if data.get("version") != FORMAT_VERSION:
            return _empty(), f"unsupported format version {data.get('version')!r}", warnings

        clean = _empty()
        clean["revision"] = (
            data["revision"]
            if isinstance(data.get("revision"), int) and data["revision"] >= 0
            else 0
        )
        for field in ("updated_at", "updated_by"):
            clean[field] = data[field] if isinstance(data.get(field), str) else None

        for kind in KINDS:
            section = data.get(kind, {})
            if not isinstance(section, dict):
                warnings.append(f"'{kind}' is not an object; ignored")
                continue
            for name, entry in section.items():
                problem = self._entry_problem(kind, name, entry)
                if problem:
                    warnings.append(f"{kind}/{name!r}: {problem}; ignored")
                    continue
                clean[kind][name] = entry

        section = data.get("settings", {})
        if not isinstance(section, dict):
            warnings.append("'settings' is not an object; ignored")
        else:
            for key, value in section.items():
                try:
                    validate_setting_key(key)
                    _json_size(value, "value")
                except OverridesError as e:
                    warnings.append(f"setting {key!r}: {e}; ignored")
                    continue
                wrapped = self._is_wrapper(value)
                if key in self.secret_keys and not wrapped:
                    warnings.append(
                        f"setting {key!r} is a credential but is stored as plain text; ignored "
                        "(set it again in the dashboard to store it encrypted)"
                    )
                    continue
                if key not in self.secret_keys and wrapped:
                    warnings.append(
                        f"setting {key!r} holds an encrypted value but is not a secret setting; ignored"
                    )
                    continue
                clean["settings"][key] = value
        return clean, None, warnings

    @staticmethod
    def _entry_problem(kind: str, name: Any, entry: Any) -> Optional[str]:
        try:
            validate_name(name)
        except OverridesError as e:
            return str(e)
        if not isinstance(entry, dict):
            return "entry is not an object"
        if "definition" in entry and not isinstance(entry["definition"], dict):
            return "definition is not an object"
        for flag in ("removed", "paused"):
            if flag in entry and not isinstance(entry[flag], bool):
                return f"'{flag}' is not true/false"
        return None

    # ---- merging -----------------------------------------------------------

    def apply(self, base: Dict[str, Any]) -> Dict[str, Any]:
        """The effective configuration: ``base`` (config.yaml) with the
        overrides applied. ``base`` itself is not modified."""
        data = self._data
        effective = copy.deepcopy(base) if isinstance(base, dict) else {}

        # Sections are only touched when there is something to merge, so an
        # empty config with no overrides stays exactly as it was.
        if data["jobs"]:
            effective["jobs"] = self._merged_jobs(effective.get("jobs"), data["jobs"])
        for kind in ("providers", "rotators"):
            if data[kind]:
                effective[kind] = self._merged_named(effective.get(kind), data[kind])

        for key, value in data["settings"].items():
            if key in self.secret_keys:
                value = self._decrypt_setting(key, value)
                if value is None:
                    continue  # undecryptable: config.yaml's value (if any) applies
            node = effective
            parts = key.split(".")
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = copy.deepcopy(value)
        return effective

    @staticmethod
    def _merged_jobs(base_jobs: Any, entries: Dict[str, Any]) -> List[Dict[str, Any]]:
        base_jobs = base_jobs if isinstance(base_jobs, list) else []
        merged: List[Dict[str, Any]] = []
        seen = set()

        for job in base_jobs:
            if not isinstance(job, dict) or "name" not in job:
                merged.append(job)  # not ours to judge; engine reports it
                continue
            name = job["name"]
            seen.add(name)
            entry = entries.get(name, {})
            if entry.get("removed"):
                continue
            result = (
                copy.deepcopy(entry["definition"]) if "definition" in entry else copy.deepcopy(job)
            )
            result["name"] = name
            if entry.get("paused"):
                result["paused"] = True
            merged.append(result)

        for name, entry in entries.items():
            if name in seen or entry.get("removed") or "definition" not in entry:
                continue
            result = copy.deepcopy(entry["definition"])
            result["name"] = name
            if entry.get("paused"):
                result["paused"] = True
            merged.append(result)
        return merged

    @staticmethod
    def _merged_named(base_items: Any, entries: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(base_items) if isinstance(base_items, dict) else {}
        for name, entry in entries.items():
            if entry.get("removed"):
                merged.pop(name, None)
            elif "definition" in entry:
                merged[name] = copy.deepcopy(entry["definition"])
        return merged

    def item_source(self, kind: str, name: str) -> Optional[str]:
        """Where an item comes from: SOURCE_CONFIG, SOURCE_MODIFIED,
        SOURCE_DASHBOARD or SOURCE_REMOVED; None if it exists nowhere."""
        entry = self._data[kind].get(name)
        in_base = name in self._base_names(kind)
        if entry is None:
            return SOURCE_CONFIG if in_base else None
        if entry.get("removed"):
            return SOURCE_REMOVED if in_base else None
        if not in_base:
            return SOURCE_DASHBOARD if "definition" in entry else None
        return SOURCE_MODIFIED

    def names(self, kind: str) -> List[str]:
        """Names of every item the dashboard has an entry for."""
        self._check_kind(kind)
        return list(self._data[kind])

    def has_entry(self, kind: str, name: str) -> bool:
        """Whether the dashboard has changed (or created) this item, so there
        is something to reset."""
        self._check_kind(kind)
        return name in self._data[kind]

    def is_paused(self, name: str) -> bool:
        return bool(self._data["jobs"].get(name, {}).get("paused"))

    def _base_names(self, kind: str) -> set:
        base = self._base_provider() or {}
        items = base.get(kind)
        if kind == "jobs":
            return (
                {j["name"] for j in items if isinstance(j, dict) and "name" in j}
                if isinstance(items, list)
                else set()
            )
        return set(items) if isinstance(items, dict) else set()

    # ---- changing ----------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive access for a read-modify-write: a thread lock plus, on
        POSIX, a file lock so other processes wait too."""
        with self._thread_lock:
            if fcntl is None:  # pragma: no cover
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + ".lock")
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _change(
        self,
        actor: str,
        description: str,
        mutate: Callable[[Dict[str, Any]], None],
        expected_revision: Optional[int],
    ) -> int:
        """Re-read the file, apply ``mutate``, write atomically. Returns the
        new revision."""
        with self._locked():
            data, error, _ = self._read_file()
            if error and self.path.exists():
                self._move_aside()
                data = _empty()

            if expected_revision is not None and data["revision"] != expected_revision:
                raise ConflictError(
                    f"the settings changed since you loaded them (now revision {data['revision']}, "
                    f"you had {expected_revision}); reload and try again"
                )

            mutate(data)
            data["revision"] += 1
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            data["updated_by"] = str(actor)
            self._write(data)
            self._data = data
            self.load_error = None
            logger.info(
                f"Dashboard settings changed by {actor}: {description} (revision {data['revision']})"
            )
            return data["revision"]

    def _move_aside(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        aside = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        os.replace(self.path, aside)
        logger.warning(f"Moved the unreadable dashboard settings file to {aside}")

    def _write(self, data: Dict[str, Any]) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_name, 0o600)

            if self.path.exists():
                # Copy, not rename: the live file stays in place until the
                # atomic replace below, so a failure never leaves it missing.
                backup = self.path.with_name(self.path.name + ".bak")
                try:
                    shutil.copyfile(self.path, backup)
                    os.chmod(backup, 0o600)
                except OSError as e:
                    logger.warning(f"Could not keep a backup of the dashboard settings: {e}")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def set_item(
        self,
        kind: str,
        name: str,
        definition: Dict[str, Any],
        actor: str,
        expected_revision: Optional[int] = None,
    ) -> int:
        """Add or replace a job, provider or rotator."""
        self._check_kind(kind)
        validate_name(name)
        if not isinstance(definition, dict):
            raise OverridesError("definition must be an object")
        if _json_size(definition, "definition") > MAX_DEFINITION_BYTES:
            raise OverridesError(f"definition is too large (over {MAX_DEFINITION_BYTES} bytes)")
        stored = copy.deepcopy(definition)
        stored.pop("paused", None)  # paused is its own flag
        if kind == "jobs":
            stored["name"] = name

        def mutate(data):
            entry = data[kind].setdefault(name, {})
            entry["definition"] = stored
            entry.pop("removed", None)

        return self._change(actor, f"{kind}/{name} saved", mutate, expected_revision)

    def remove_item(
        self, kind: str, name: str, actor: str, expected_revision: Optional[int] = None
    ) -> int:
        """Delete a dashboard-created item, or hide a config.yaml one."""
        self._check_kind(kind)
        validate_name(name)
        in_base = name in self._base_names(kind)

        def mutate(data):
            if in_base:
                data[kind][name] = {"removed": True}
            else:
                data[kind].pop(name, None)

        return self._change(actor, f"{kind}/{name} removed", mutate, expected_revision)

    def reset_item(
        self, kind: str, name: str, actor: str, expected_revision: Optional[int] = None
    ) -> int:
        """Drop every dashboard change to an item: a replaced or hidden
        config.yaml item is back as written there (a dashboard-only item
        is deleted)."""
        self._check_kind(kind)
        validate_name(name)
        return self._change(
            actor,
            f"{kind}/{name} reset",
            lambda data: data[kind].pop(name, None),
            expected_revision,
        )

    def set_paused(
        self, name: str, paused: bool, actor: str, expected_revision: Optional[int] = None
    ) -> int:
        """Pause or resume a job (a dashboard-only flag; works on config.yaml jobs)."""
        validate_name(name, "job name")

        def mutate(data):
            entry = data["jobs"].setdefault(name, {})
            if paused:
                entry["paused"] = True
            else:
                entry.pop("paused", None)
            if not entry:  # nothing left worth storing
                del data["jobs"][name]

        return self._change(
            actor, f"jobs/{name} {'paused' if paused else 'resumed'}", mutate, expected_revision
        )

    def set_setting(
        self, key: str, value: Any, actor: str, expected_revision: Optional[int] = None
    ) -> int:
        """Override one setting by dotted key. Credentials (SECRET_SETTING_KEYS)
        are encrypted before they are written."""
        validate_setting_key(key)
        if key in self.secret_keys:
            stored = self._encrypt_setting(value)
        else:
            if self._is_wrapper(value):
                raise OverridesError("that value is reserved for encrypted settings")
            if _json_size(value, "value") > MAX_SETTING_BYTES:
                raise OverridesError(f"value is too large (over {MAX_SETTING_BYTES} bytes)")
            stored = copy.deepcopy(value)
        return self._change(
            actor,
            f"setting {key} changed",
            lambda data: data["settings"].__setitem__(key, stored),
            expected_revision,
        )

    def reset_setting(self, key: str, actor: str, expected_revision: Optional[int] = None) -> int:
        """Drop a setting override: config.yaml's value (or the default) applies."""
        validate_setting_key(key)
        return self._change(
            actor,
            f"setting {key} reset",
            lambda data: data["settings"].pop(key, None),
            expected_revision,
        )

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise OverridesError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")

    def overridden_settings(self) -> Dict[str, Any]:
        """A copy of the setting overrides (for the Settings page). Secret
        settings are left out: see secrets_set()."""
        return {
            key: copy.deepcopy(value)
            for key, value in self._data["settings"].items()
            if key not in self.secret_keys
        }

    def secrets_set(self) -> List[str]:
        """Secret settings that have a value stored here and can be
        decrypted. The values themselves are never returned."""
        return sorted(
            key
            for key, value in self._data["settings"].items()
            if key in self.secret_keys and self._decrypt_setting(key, value, quiet=True) is not None
        )

    # ---- secret settings ---------------------------------------------------

    @staticmethod
    def _is_wrapper(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == {WRAPPER_KEY}
            and isinstance(value[WRAPPER_KEY], str)
        )

    @property
    def key_path(self) -> Path:
        """Where the encryption key for secret settings lives."""
        return self._key_path or self.path.with_name(self.path.stem + ".key")

    def _warn_about_key_permissions(self) -> None:
        if self._warned_key_permissions:
            return
        try:
            mode = os.stat(self.key_path).st_mode & 0o077
        except OSError:
            return
        if mode:
            self._warned_key_permissions = True
            logger.warning(
                f"The dashboard settings key {self.key_path} is readable by other users; "
                f"run: chmod 600 {self.key_path}"
            )

    def _load_key(self) -> Optional[Fernet]:
        """The encryption key, or None if there is no usable key file."""
        try:
            fernet = Fernet(self.key_path.read_bytes().strip())
        except (OSError, ValueError):
            return None
        self._warn_about_key_permissions()
        return fernet

    def _create_key(self) -> Fernet:
        """The encryption key, creating the file (mode 0600) if needed.

        Safe against two processes creating it at once: the file is created
        exclusively, and whoever loses the race reads the winner's key.
        """
        existing = self._load_key()
        if existing is not None:
            return existing

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = self._load_key()
            if existing is None:
                raise OverridesError(
                    f"the settings key file {self.key_path} exists but is not a valid key; "
                    "fix or remove it"
                )
            return existing

        key = Fernet.generate_key()
        with os.fdopen(fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"Created the dashboard settings encryption key at {self.key_path}")
        return Fernet(key)

    def _encrypt_setting(self, value: Any) -> Dict[str, str]:
        if not isinstance(value, str) or not value:
            raise OverridesError("a secret value must be non-empty text")
        if len(value) > MAX_SECRET_LENGTH or any(ord(c) < 32 for c in value):
            raise OverridesError(
                f"a secret value must be at most {MAX_SECRET_LENGTH} characters, "
                "with no control characters"
            )
        token = self._create_key().encrypt(value.encode("utf-8")).decode("ascii")
        return {WRAPPER_KEY: token}

    def _decrypt_setting(self, key: str, wrapper: Any, quiet: bool = False) -> Optional[str]:
        if not self._is_wrapper(wrapper):
            return None
        fernet = self._load_key()
        if fernet is None:
            problem = f"the settings key file {self.key_path} is missing or unreadable"
        else:
            try:
                return fernet.decrypt(wrapper[WRAPPER_KEY].encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeError):
                problem = "it cannot be decrypted with the current settings key"
        if not quiet:
            message = (
                f"secret setting {key!r} is ignored: {problem}; enter it again in the dashboard"
            )
            if message not in self.warnings:
                self.warnings.append(message)
                logger.warning(f"Dashboard settings file: {message}")
        return None
