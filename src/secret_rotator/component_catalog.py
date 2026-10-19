"""
What the dashboard may configure for providers and rotators.

Every provider and rotator type the dashboard can create is described here
by the fields it accepts, with their kinds and limits. Three things follow
from keeping this list explicit:

* Nothing else can be stored. A request with an unknown field is rejected,
  so the dashboard's settings file never ends up holding arbitrary data,
  and in particular never credentials. (None of these types needs any: the
  file provider uses the master key, and the rotators only generate
  values.)
* The forms in the UI are built from the same descriptions the server
  validates with, so they cannot drift apart.
* The file provider's path is confined to the data directory, so a
  dashboard user cannot make the daemon overwrite arbitrary files.

Types that exist in the code base but that the daemon does not build at
startup (database, SSH key, certificate and OAuth2 rotators, the AWS
provider) are deliberately absent: creating one in the dashboard would
silently do nothing.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

KIND_PROVIDERS = "providers"
KIND_ROTATORS = "rotators"

_PREFIX_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")


@dataclass(frozen=True)
class Field:
    """One setting of a provider or rotator type."""

    name: str
    label: str
    kind: str  # "int", "bool", "choice", "text", "path"
    default: Any = None
    required: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    choices: Tuple[str, ...] = ()
    help: str = ""

    def describe(self) -> Dict[str, Any]:
        """The field as the form builder needs it."""
        data: Dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "default": self.default,
            "help": self.help,
        }
        if self.kind == "int":
            data["min"], data["max"] = self.minimum, self.maximum
        if self.kind == "text":
            data["max"] = self.maximum
        if self.kind == "choice":
            data["choices"] = list(self.choices)
        return data


@dataclass(frozen=True)
class TypeSpec:
    key: str  # what config.yaml calls it: "password", "file", ...
    label: str
    fields: Tuple[Field, ...] = field(default_factory=tuple)
    sample: bool = False  # a harmless example value can be generated

    def field(self, name: str) -> Optional[Field]:
        return next((f for f in self.fields if f.name == name), None)

    def describe(self) -> Dict[str, Any]:
        return {
            "type": self.key,
            "label": self.label,
            "fields": [f.describe() for f in self.fields],
            "sample": self.sample,
        }


PROVIDER_TYPES: Dict[str, TypeSpec] = {
    "file": TypeSpec(
        key="file",
        label="File on disk",
        fields=(
            Field(
                "file_path",
                "File path",
                "path",
                default="data/secrets.json",
                required=True,
                help="A .json file inside the data directory. Secrets are stored encrypted.",
            ),
        ),
    )
}

ROTATOR_TYPES: Dict[str, TypeSpec] = {
    "password": TypeSpec(
        key="password",
        label="Password",
        sample=True,
        fields=(
            Field("length", "Length", "int", default=16, minimum=8, maximum=256),
            Field("use_lowercase", "Lowercase letters", "bool", default=True),
            Field("use_uppercase", "Uppercase letters", "bool", default=True),
            Field("use_numbers", "Numbers", "bool", default=True),
            Field("use_symbols", "Symbols", "bool", default=True),
            Field(
                "exclude_ambiguous",
                "Skip look-alikes",
                "bool",
                default=False,
                help="Leave out characters that are easy to confuse, like l, 1, O and 0.",
            ),
        ),
    ),
    "api_key": TypeSpec(
        key="api_key",
        label="API key",
        sample=True,
        fields=(
            Field("length", "Length", "int", default=32, minimum=16, maximum=256),
            Field(
                "format",
                "Format",
                "choice",
                default="hex",
                choices=("hex", "base64", "alphanumeric"),
            ),
            Field(
                "prefix",
                "Prefix",
                "text",
                default="",
                maximum=32,
                help="Letters, numbers, underscore, dash and dot only, e.g. sk_live_",
            ),
            Field("include_checksum", "Checksum", "bool", default=False),
        ),
    ),
    "jwt_secret": TypeSpec(
        key="jwt_secret",
        label="JWT secret",
        sample=True,
        fields=(
            Field(
                "algorithm",
                "Algorithm",
                "choice",
                default="HS256",
                choices=("HS256", "HS384", "HS512"),
            ),
        ),
    ),
}

CATALOG: Dict[str, Dict[str, TypeSpec]] = {
    KIND_PROVIDERS: PROVIDER_TYPES,
    KIND_ROTATORS: ROTATOR_TYPES,
}


def type_label(kind: str, type_key: Any) -> str:
    spec = CATALOG[kind].get(type_key)
    return spec.label if spec else (str(type_key) if type_key else "Unknown")


def defaults(kind: str, type_key: str) -> Dict[str, Any]:
    """Every field of a type with its default (or None) filled in."""
    spec = CATALOG[kind][type_key]
    return {f.name: f.default for f in spec.fields}


def validate_file_path(
    value: Any, root: Path, protected: List[Path]
) -> Tuple[Optional[str], Optional[str]]:
    """Check a file provider's path.

    It must name a ``.json`` file that resolves (symlinks followed) to a
    place inside ``root``, and must not be one of the ``protected`` files
    the daemon itself relies on (master key, settings, audit log, backups).

    Returns:
        ``(path_as_given, None)`` if acceptable, else ``(None, message)``.
    """
    if not isinstance(value, str) or not value:
        return None, "required"
    if len(value) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None, "must be at most 255 characters, with no control characters"
    if not value.endswith(".json"):
        return None, "must be a .json file"

    resolved = Path(value).resolve()
    root = root.resolve()
    if not resolved.is_relative_to(root):
        return None, f"must be inside the data directory ({root})"
    if resolved.is_dir():
        return None, "is a directory"
    for path in protected:
        path = path.resolve()
        if resolved == path or path in resolved.parents:
            return None, "is used by the daemon itself and cannot be a provider file"
    return value, None


def validate_fields(
    spec: TypeSpec,
    definition: Dict[str, Any],
    problems: Dict[str, str],
    root: Optional[Path] = None,
    protected: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Check the fields of a definition against a type and return the
    canonical (provided, valid) fields. Problems are added to ``problems``."""
    canonical: Dict[str, Any] = {}

    for key in definition:
        if key not in ("name", "type") and spec.field(key) is None:
            problems[str(key)] = "unknown field"

    for item in spec.fields:
        if item.name not in definition or definition[item.name] is None:
            if item.required:
                problems[item.name] = "required"
            continue
        value = definition[item.name]

        if item.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                problems[item.name] = "must be a whole number"
            elif not item.minimum <= value <= item.maximum:
                problems[item.name] = f"must be between {item.minimum} and {item.maximum}"
            else:
                canonical[item.name] = value
        elif item.kind == "bool":
            if not isinstance(value, bool):
                problems[item.name] = "must be true or false"
            else:
                canonical[item.name] = value
        elif item.kind == "choice":
            if value not in item.choices:
                problems[item.name] = f"must be one of: {', '.join(item.choices)}"
            else:
                canonical[item.name] = value
        elif item.kind == "text":
            if not isinstance(value, str):
                problems[item.name] = "must be text"
            elif len(value) > item.maximum:
                problems[item.name] = f"must be at most {item.maximum} characters"
            elif not set(value) <= _PREFIX_CHARS:
                problems[item.name] = "may only contain letters, numbers, underscore, dash and dot"
            else:
                canonical[item.name] = value
        elif item.kind == "path":
            path, problem = validate_file_path(value, root or Path("data"), protected or [])
            if problem:
                problems[item.name] = problem
            else:
                canonical[item.name] = path
    return canonical
