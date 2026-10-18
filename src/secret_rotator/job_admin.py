"""
Creating, changing, pausing and deleting jobs from the dashboard.

Changes are saved in the dashboard's own settings file (see
config/overrides.py), never in config.yaml, and applied to the running
engine and scheduler straight away. Another web instance, or a queue
worker, sees them the next time it reloads that file (a worker does before
every sweep; another web instance on restart).

Rules enforced here, before anything is saved:

* a job needs a provider and a rotator that exist, and a secret ID;
* its schedule, if it has one, must be valid;
* two jobs must not rotate the same secret in the same provider (they
  would overwrite each other's work);
* only the known fields are accepted, so a request cannot inject arbitrary
  configuration;
* a job's name cannot change (that would orphan its history); create a new
  job and delete the old one instead.
"""

from typing import Any, Dict, List, Optional

from secret_rotator.audit_log import audit_log as default_audit_log
from secret_rotator.config.overrides import (
    SOURCE_CONFIG,
    SOURCE_DASHBOARD,
    SOURCE_REMOVED,
    OverridesError,
    validate_name,
)
from secret_rotator.config.settings import settings as default_settings
from secret_rotator.schedule_spec import SCHEDULE_CHOICES, parse_schedule
from secret_rotator.utils.logger import logger

ALLOWED_FIELDS = ("name", "provider", "rotator", "secret_id", "schedule", "notification")
MAX_SECRET_ID_LENGTH = 200


class JobNotFoundError(KeyError):
    """No job has this name."""


class JobExistsError(Exception):
    """A job with this name already exists."""


class JobValidationError(Exception):
    """The job definition is not acceptable. ``fields`` maps each field to
    what is wrong with it."""

    def __init__(self, fields: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


class JobAdminService:
    def __init__(self, engine, settings_obj=None, audit=None):
        self.engine = engine
        self.settings = settings_obj if settings_obj is not None else default_settings
        self.audit = audit if audit is not None else default_audit_log

    # ---- reading -----------------------------------------------------------

    @property
    def revision(self) -> int:
        """Revision of the dashboard settings file; send it back with a
        change to be told if someone else changed things meanwhile."""
        return self.settings.overrides.revision

    def _current_jobs(self) -> List[Dict[str, Any]]:
        self.settings.reload_overrides()
        return list(self.settings.get("jobs", []) or [])

    def _find(self, name: str) -> Dict[str, Any]:
        for job in self._current_jobs():
            if isinstance(job, dict) and job.get("name") == name:
                return job
        raise JobNotFoundError(name)

    def hidden_jobs(self) -> List[str]:
        """Jobs defined in config.yaml that were deleted in the dashboard
        (hidden, not removed from config.yaml), so they can be restored."""
        self.settings.reload_overrides()
        store = self.settings.overrides
        return sorted(
            name
            for name in store.names("jobs")
            if store.item_source("jobs", name) == SOURCE_REMOVED
        )

    def options(self) -> Dict[str, Any]:
        """What the job form can offer: providers, rotators and schedules."""
        return {
            "providers": sorted(self.engine.providers),
            "rotators": sorted(self.engine.rotators),
            "schedules": list(SCHEDULE_CHOICES),
            "default_schedule": self.settings.get("rotation.schedule", "daily"),
            "hidden": self.hidden_jobs(),
            "revision": self.revision,
        }

    # ---- validation --------------------------------------------------------

    def _validate(self, definition: Any, name: str, creating: bool) -> Dict[str, Any]:
        """Check a definition and return its canonical form (without name)."""
        if not isinstance(definition, dict):
            raise JobValidationError({"_": "the job must be an object"})

        problems: Dict[str, str] = {}
        for key in definition:
            if key not in ALLOWED_FIELDS:
                problems[str(key)] = "unknown field"

        if "name" in definition and definition["name"] != name:
            problems["name"] = "a job cannot be renamed; create a new one and delete this one"

        provider = definition.get("provider")
        if not isinstance(provider, str) or not provider:
            problems["provider"] = "required"
        elif provider not in self.engine.providers:
            problems["provider"] = (
                f"unknown provider; choose one of: {', '.join(sorted(self.engine.providers)) or 'none configured'}"
            )

        rotator = definition.get("rotator")
        if not isinstance(rotator, str) or not rotator:
            problems["rotator"] = "required"
        elif rotator not in self.engine.rotators:
            problems["rotator"] = (
                f"unknown rotator; choose one of: {', '.join(sorted(self.engine.rotators)) or 'none configured'}"
            )

        secret_id = definition.get("secret_id")
        if not isinstance(secret_id, str) or not secret_id:
            problems["secret_id"] = "required"
        elif (
            len(secret_id) > MAX_SECRET_ID_LENGTH
            or secret_id != secret_id.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in secret_id)
        ):
            problems["secret_id"] = (
                f"must be at most {MAX_SECRET_ID_LENGTH} characters, with no leading or trailing "
                "spaces or control characters"
            )

        canonical: Dict[str, Any] = {}
        schedule_value = definition.get("schedule")
        if schedule_value not in (None, ""):
            try:
                canonical["schedule"] = parse_schedule(schedule_value).name
            except ValueError as e:
                problems["schedule"] = str(e)

        if "notification" in definition and definition["notification"] is not None:
            if not isinstance(definition["notification"], bool):
                problems["notification"] = "must be true or false"
            else:
                canonical["notification"] = definition["notification"]

        if "secret_id" not in problems and "provider" not in problems:
            for other in self._current_jobs():
                if (
                    isinstance(other, dict)
                    and other.get("name") != name
                    and other.get("provider") == provider
                    and other.get("secret_id") == secret_id
                ):
                    problems["secret_id"] = (
                        f"job '{other.get('name')}' already rotates this secret in this provider"
                    )
                    break

        if problems:
            raise JobValidationError(problems)

        canonical.update({"provider": provider, "rotator": rotator, "secret_id": secret_id})
        return canonical

    # ---- applying ----------------------------------------------------------

    def _apply(self) -> None:
        """Make the running engine and scheduler match what was saved."""
        self.settings.reload_overrides()
        self.engine.replace_rotation_jobs(list(self.settings.get("jobs", []) or []))

        scheduler = getattr(self.engine, "scheduler", None)
        if scheduler is not None:
            try:
                scheduler.refresh_rotation_schedules()
            except ValueError as e:  # validated before saving, so unexpected
                logger.error(f"Saved, but the rotation schedules could not be refreshed: {e}")

    def _audit(self, action: str, actor: str, job: Dict[str, Any], **details: Any) -> None:
        self.audit.log(
            action,
            actor,
            secret_id=job.get("secret_id"),
            details={"job": job.get("name"), **details},
        )

    # ---- changes -----------------------------------------------------------

    def create(self, definition: Any, actor: str, revision: Optional[int] = None) -> str:
        """Add a job. Returns its name."""
        if not isinstance(definition, dict):
            raise JobValidationError({"_": "the job must be an object"})
        name = definition.get("name")
        try:
            validate_name(name, "name")
        except OverridesError as e:
            raise JobValidationError({"name": str(e)}) from e
        if any(isinstance(j, dict) and j.get("name") == name for j in self._current_jobs()):
            raise JobExistsError(f"A job named '{name}' already exists")

        canonical = self._validate(definition, name, creating=True)
        self.settings.overrides.set_item("jobs", name, canonical, actor, expected_revision=revision)
        self._apply()
        self._audit("job_create", actor, {"name": name, **canonical})
        return name

    def update(
        self, name: str, definition: Any, actor: str, revision: Optional[int] = None
    ) -> List[str]:
        """Replace a job's settings (everything but its name). Returns the
        fields that changed; if none did, nothing is saved."""
        current = self._find(name)
        canonical = self._validate(definition, name, creating=False)

        tracked = ("provider", "rotator", "secret_id", "schedule", "notification")

        def same(field):
            # An absent notification setting and "off" mean the same thing.
            if field == "notification":
                return bool(current.get(field)) == bool(canonical.get(field))
            return current.get(field) == canonical.get(field)

        changed = [f for f in tracked if not same(f)]
        if not changed:
            return []

        self.settings.overrides.set_item("jobs", name, canonical, actor, expected_revision=revision)
        self._apply()
        self._audit("job_update", actor, {"name": name, **canonical}, changed=changed)
        return changed

    def delete(self, name: str, actor: str, revision: Optional[int] = None) -> str:
        """Delete a dashboard job, or hide one defined in config.yaml.
        Returns where the job came from."""
        job = self._find(name)
        source = self.settings.overrides.item_source("jobs", name) or SOURCE_CONFIG
        self.settings.overrides.remove_item("jobs", name, actor, expected_revision=revision)
        self._apply()
        self._audit("job_delete", actor, job, source=source)
        return source

    def set_paused(
        self, name: str, paused: bool, actor: str, revision: Optional[int] = None
    ) -> bool:
        """Pause or resume scheduled rotation of a job. Returns whether the
        state changed."""
        job = self._find(name)
        if bool(job.get("paused")) == paused:
            return False
        self.settings.overrides.set_paused(name, paused, actor, expected_revision=revision)
        self._apply()
        self._audit("job_pause" if paused else "job_resume", actor, job)
        return True

    def reset(self, name: str, actor: str, revision: Optional[int] = None) -> str:
        """Drop every dashboard change to a job. A job defined only in the
        dashboard is deleted; one from config.yaml is back as written there.
        Returns the job's source before the reset.

        Raises:
            JobValidationError: the job has no dashboard changes to drop.
        """
        source = self.settings.overrides.item_source("jobs", name)
        entry_exists = self.settings.overrides.has_entry("jobs", name)
        if not entry_exists:
            if source is None:
                raise JobNotFoundError(name)
            raise JobValidationError({"_": "this job has no dashboard changes to reset"})
        job = {"name": name}
        try:
            job = self._find(name)
        except JobNotFoundError:
            pass  # hidden config.yaml job: resetting brings it back
        self.settings.overrides.reset_item("jobs", name, actor, expected_revision=revision)
        self._apply()
        self._audit("job_reset", actor, job, source=source)
        return source or SOURCE_DASHBOARD
