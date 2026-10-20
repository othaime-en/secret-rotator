"""
Adding, changing and removing providers and rotators from the dashboard.

Like jobs (job_admin.py), changes are saved in the dashboard's own settings
file, never config.yaml, and applied to the running engine immediately.

Rules, enforced before anything is saved:

* only types listed in component_catalog.py, and only their listed fields
  (so no credentials or arbitrary data can be stored);
* a rotator's settings must actually work: a trial rotator is built from
  them and asked for a value, so "all character classes off" or "password
  shorter than its required characters" is caught here, not at 02:00;
* a provider's file must be inside the data directory;
* a name and a type cannot change after creation;
* a provider or rotator that jobs use cannot be deleted.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from secret_rotator import bootstrap
from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.component_catalog import (
    CATALOG,
    KIND_PROVIDERS,
    KIND_ROTATORS,
    defaults,
    type_label,
    validate_fields,
)
from secret_rotator.config.overrides import (
    SOURCE_CONFIG,
    SOURCE_REMOVED,
    OverridesError,
    validate_name,
)
from secret_rotator.config.settings import settings as default_settings
from secret_rotator.rotator_display import describe_rotator
from secret_rotator.utils.logger import logger

_SINGULAR = {KIND_PROVIDERS: "provider", KIND_ROTATORS: "rotator"}
_JOB_FIELD = {KIND_PROVIDERS: "provider", KIND_ROTATORS: "rotator"}


class ComponentNotFoundError(KeyError):
    """No provider or rotator has this name."""


class ComponentExistsError(Exception):
    """A provider or rotator with this name already exists."""


class ComponentValidationError(Exception):
    """The definition is not acceptable. ``fields`` maps each field to what
    is wrong with it ("_" for problems with the whole definition)."""

    def __init__(self, fields: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


class ComponentInUseError(Exception):
    """Jobs still use the provider or rotator."""

    def __init__(self, kind: str, name: str, jobs: List[str]):
        super().__init__(f"{_SINGULAR[kind]} '{name}' is used by: {', '.join(jobs)}")
        self.jobs = jobs


class ComponentAdminService:
    def __init__(self, engine, settings_obj=None, audit=None):
        self.engine = engine
        self.settings = settings_obj if settings_obj is not None else default_settings
        self.audit = audit if audit is not None else default_audit_log

    # ---- reading -----------------------------------------------------------

    @property
    def revision(self) -> int:
        return self.settings.overrides.revision

    def effective(self, kind: str) -> Dict[str, Any]:
        self.settings.reload_overrides()
        items = self.settings.get(kind, {}) or {}
        return items if isinstance(items, dict) else {}

    def _registry(self, kind: str) -> Dict[str, Any]:
        return self.engine.providers if kind == KIND_PROVIDERS else self.engine.rotators

    def used_by(self, kind: str, name: str) -> List[str]:
        jobs = self.settings.get("jobs", []) or []
        return [
            job["name"]
            for job in jobs
            if isinstance(job, dict) and job.get(_JOB_FIELD[kind]) == name and "name" in job
        ]

    def types(self, kind: str) -> List[Dict[str, Any]]:
        return [spec.describe() for spec in CATALOG[kind].values()]

    def hidden(self, kind: str) -> List[str]:
        store = self.settings.overrides
        return sorted(n for n in store.names(kind) if store.item_source(kind, n) == SOURCE_REMOVED)

    def _summary(self, kind: str, name: str, config: Dict[str, Any]) -> List[Dict[str, str]]:
        """Display-safe settings of an item, as label/value pairs."""
        if kind == KIND_ROTATORS:
            return describe_rotator(self.engine.rotators.get(name))["settings"]
        if config.get("type") == "file":
            return [{"label": "File path", "value": str(config.get("file_path", ""))}]
        return []

    def describe(self, kind: str, name: str, config: Any) -> Dict[str, Any]:
        config = config if isinstance(config, dict) else {}
        type_key = config.get("type")
        spec = CATALOG[kind].get(type_key)
        fields = {}
        if spec:
            fields = {
                **defaults(kind, type_key),
                **{k: v for k, v in config.items() if spec.field(k)},
            }
        return {
            "name": name,
            "type": type_key,
            "type_label": type_label(kind, type_key),
            "supported": spec is not None,
            "registered": name in self._registry(kind),
            "enabled": config.get("enabled", True) is not False,
            "source": self.settings.overrides.item_source(kind, name),
            "used_by": self.used_by(kind, name),
            "fields": fields,
            "summary": self._summary(kind, name, config),
        }

    def listing(self, kind: str) -> Dict[str, Any]:
        items = [self.describe(kind, name, cfg) for name, cfg in self.effective(kind).items()]
        return {
            "items": items,
            "types": self.types(kind),
            "hidden": self.hidden(kind),
            "revision": self.revision,
        }

    # ---- validation --------------------------------------------------------

    def _protected_paths(self) -> List[Path]:
        paths = [
            Path(self.settings.get("security.encryption.master_key_file", "data/.master.key")),
            Path(self.settings.get("backup.storage_path", "data/backup")),
            self.settings.overrides.path,
            self.settings.overrides.key_path,
        ]
        paths.append(
            self.settings.overrides.path.with_name(self.settings.overrides.path.name + ".bak")
        )
        paths.append(
            self.settings.overrides.path.with_name(self.settings.overrides.path.name + ".lock")
        )
        audit_file = getattr(self.audit, "audit_file", None)
        if audit_file:
            paths.append(Path(audit_file))
        return paths

    def _validate(
        self, kind: str, name: str, definition: Any, existing_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return the canonical definition (with ``type``) or raise."""
        if not isinstance(definition, dict):
            raise ComponentValidationError({"_": "the definition must be an object"})

        problems: Dict[str, str] = {}
        if "name" in definition and definition["name"] != name:
            problems["name"] = "cannot be renamed; create a new one and delete this one"

        type_key = definition.get("type")
        spec = CATALOG[kind].get(type_key) if isinstance(type_key, str) else None
        if spec is None:
            allowed = ", ".join(CATALOG[kind])
            problems["type"] = f"required; one of: {allowed}"
        elif existing_type is not None and type_key != existing_type:
            problems["type"] = "cannot be changed; create a new one and delete this one"

        if spec is None:
            raise ComponentValidationError(problems)

        root = Path(self.settings.get("dashboard.provider_root", "data"))
        canonical = validate_fields(
            spec, definition, problems, root=root, protected=self._protected_paths()
        )
        if problems:
            raise ComponentValidationError(problems)

        canonical = {"type": type_key, **canonical}
        if kind == KIND_ROTATORS:
            self._probe_rotator(name, canonical)
        return canonical

    @staticmethod
    def _probe_rotator(name: str, definition: Dict[str, Any]) -> None:
        """Build the rotator and ask it for a value, so settings that cannot
        work are refused now rather than at rotation time."""
        try:
            rotator = bootstrap.build_rotator(name, definition)
            if rotator is None:
                raise ValueError("this type cannot be built")
            value = rotator.generate_new_secret()
            if not rotator.validate_secret(value):
                raise ValueError("the values it generates fail its own validation")
        except Exception as e:
            raise ComponentValidationError(
                {"_": f"these settings do not work together: {e}"}
            ) from e

    # ---- applying ----------------------------------------------------------

    def _apply(self, kind: str, name: str) -> None:
        """Make the running engine match what was saved for one item."""
        item = self.effective(kind).get(name)
        registry = self._registry(kind)
        if item is None:
            registry.pop(name, None)
            return

        builder = bootstrap.build_provider if kind == KIND_PROVIDERS else bootstrap.build_rotator
        built = builder(name, item)
        if built is None:
            registry.pop(name, None)
        elif kind == KIND_PROVIDERS:
            self.engine.register_provider(built)
        else:
            self.engine.register_rotator(built)

    def _audit(
        self, action: str, actor: str, name: str, type_key: Optional[str], **details: Any
    ) -> None:
        self.audit.log(action, actor, details={"name": name, "type": type_key, **details})

    def _current(self, kind: str, name: str) -> Dict[str, Any]:
        items = self.effective(kind)
        if name not in items:
            raise ComponentNotFoundError(name)
        return items[name] if isinstance(items[name], dict) else {}

    # ---- changes -----------------------------------------------------------

    def create(self, kind: str, definition: Any, actor: str, revision: Optional[int] = None) -> str:
        if not isinstance(definition, dict):
            raise ComponentValidationError({"_": "the definition must be an object"})
        name = definition.get("name")
        try:
            validate_name(name, "name")
        except OverridesError as e:
            raise ComponentValidationError({"name": str(e)}) from e
        if name in self.effective(kind):
            raise ComponentExistsError(f"A {_SINGULAR[kind]} named '{name}' already exists")

        canonical = self._validate(kind, name, definition)
        self.settings.overrides.set_item(kind, name, canonical, actor, expected_revision=revision)
        self._apply(kind, name)
        self._audit(f"{_SINGULAR[kind]}_create", actor, name, canonical["type"])
        return name

    def update(
        self, kind: str, name: str, definition: Any, actor: str, revision: Optional[int] = None
    ) -> List[str]:
        """Replace an item's settings (not its name or type). Returns the
        fields that changed; if none did, nothing is saved."""
        current = self._current(kind, name)
        canonical = self._validate(kind, name, definition, existing_type=current.get("type"))

        before = self.describe(kind, name, current)["fields"]
        after = {
            **defaults(kind, canonical["type"]),
            **{k: v for k, v in canonical.items() if k != "type"},
        }
        changed = sorted(k for k in after if before.get(k) != after[k])
        if not changed:
            return []

        self.settings.overrides.set_item(kind, name, canonical, actor, expected_revision=revision)
        self._apply(kind, name)
        self._audit(f"{_SINGULAR[kind]}_update", actor, name, canonical["type"], changed=changed)
        return changed

    def delete(self, kind: str, name: str, actor: str, revision: Optional[int] = None) -> str:
        """Delete a dashboard item, or hide a config.yaml one. Returns where
        it came from. Refused while jobs use it."""
        current = self._current(kind, name)
        users = self.used_by(kind, name)
        if users:
            raise ComponentInUseError(kind, name, users)
        source = self.settings.overrides.item_source(kind, name) or SOURCE_CONFIG
        self.settings.overrides.remove_item(kind, name, actor, expected_revision=revision)
        self._apply(kind, name)
        self._audit(f"{_SINGULAR[kind]}_delete", actor, name, current.get("type"), source=source)
        return source

    def reset(self, kind: str, name: str, actor: str, revision: Optional[int] = None) -> str:
        """Drop every dashboard change to an item (a dashboard-only item is
        deleted; a config.yaml one is back as written there).

        Raises:
            ComponentValidationError: nothing to reset.
            ComponentInUseError: it is a dashboard-only item still in use.
        """
        store = self.settings.overrides
        source = store.item_source(kind, name)
        if not store.has_entry(kind, name):
            if source is None:
                raise ComponentNotFoundError(name)
            raise ComponentValidationError({"_": "this has no dashboard changes to reset"})
        if source == "dashboard":
            users = self.used_by(kind, name)
            if users:
                raise ComponentInUseError(kind, name, users)

        store.reset_item(kind, name, actor, expected_revision=revision)
        self._apply(kind, name)
        self._audit(f"{_SINGULAR[kind]}_reset", actor, name, None, source=source)
        return source or "dashboard"

    # ---- trying things out ---------------------------------------------------

    def test_provider(self, name: str) -> Dict[str, Any]:
        """Check that a registered provider can be reached and used."""
        provider = self.engine.providers.get(name)
        if provider is None:
            if name not in self.effective(KIND_PROVIDERS):
                raise ComponentNotFoundError(name)
            return {
                "ok": False,
                "message": "This provider is not available: its type is not implemented.",
            }
        try:
            ok = bool(provider.validate_connection())
        except Exception as e:
            logger.error(f"Provider test for '{name}' failed: {e}")
            return {"ok": False, "message": "The check failed. See the application log."}
        return {
            "ok": ok,
            "message": (
                "Connected."
                if ok
                else "Could not use this provider (is the file readable and writable?)."
            ),
        }

    def sample_rotator(self, name: Optional[str] = None, definition: Any = None) -> Dict[str, Any]:
        """A made-up example of what a rotator generates. It is random, never
        stored or used, and never logged: it only shows the shape of the
        values (length, characters, prefix).

        Give ``name`` for a saved rotator or ``definition`` for unsaved form
        settings.
        """
        if name is not None:
            current = self._current(KIND_ROTATORS, name)
            type_key = current.get("type")
            rotator = self.engine.rotators.get(name)
            if rotator is None:
                return {"sample": None, "message": "This rotator is not available."}
        else:
            if isinstance(definition, dict):
                definition = {k: v for k, v in definition.items() if k != "name"}
            canonical = self._validate(KIND_ROTATORS, "sample", definition)
            type_key = canonical["type"]
            rotator = bootstrap.build_rotator("sample", canonical)

        spec = CATALOG[KIND_ROTATORS].get(type_key)
        if spec is None or not spec.sample:
            return {"sample": None, "message": "No example is shown for this type."}
        return {"sample": rotator.generate_new_secret(), "type": type_key}
