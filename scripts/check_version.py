#!/usr/bin/env python3
"""
Verify that the package version is consistent across every place it's
declared, before release.yml is allowed to publish.

This project currently declares its version in two places
(pyproject.toml's [project] version, and src/secret_rotator/version.py's
__version__) plus the git tag used to trigger a release - three sources
that must all agree. They have drifted before: as of this script being
written, pyproject.toml and version.py are both still "1.2.0" while git
tags already go up to v1.2.3, meaning at least one prior release was
tagged without the package version ever being bumped. This script is
the release-blocking guard against that happening again.

Usage:
    python scripts/check_version.py v1.3.0      # or "1.3.0" - the
                                                  # leading "v" is
                                                  # optional
"""

import re
import sys
from pathlib import Path


def read_pyproject_version(repo_root: Path) -> str:
    text = (repo_root / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ValueError("Could not find `version = \"...\"` in pyproject.toml")
    return match.group(1)


def read_version_py(repo_root: Path) -> str:
    text = (repo_root / "src" / "secret_rotator" / "version.py").read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ValueError("Could not find `__version__ = \"...\"` in version.py")
    return match.group(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_version.py <git-tag-or-version>", file=sys.stderr)
        return 2

    tag_version = sys.argv[1].lstrip("v")
    repo_root = Path(__file__).resolve().parent.parent

    try:
        sources = {
            "git tag": tag_version,
            "pyproject.toml": read_pyproject_version(repo_root),
            "src/secret_rotator/version.py": read_version_py(repo_root),
        }
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    distinct_versions = set(sources.values())
    if len(distinct_versions) > 1:
        print("Version mismatch - refusing to release:", file=sys.stderr)
        for source, version in sources.items():
            print(f"  {source}: {version}", file=sys.stderr)
        print(
            "\nBump every source to the same version before tagging a "
            "release (see CONTRIBUTING.md's release checklist).",
            file=sys.stderr,
        )
        return 1

    print(f"All version sources agree: {tag_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())