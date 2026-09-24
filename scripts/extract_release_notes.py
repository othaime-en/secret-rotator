#!/usr/bin/env python3
"""
Extract a single version's section from CHANGELOG.md, for use as GitHub
Release notes.

Usage:
    python scripts/extract_release_notes.py 1.3.0

Looks for a heading of the form `## [1.3.0] - ...` (the "Keep a
Changelog" format this project already uses) and prints everything
between that heading and the next `## [` heading (or end of file).

Exits non-zero with a clear message if the version has no changelog
entry - this is used as a release-blocking check in release.yml, on
the theory that a release with no changelog entry is a release nobody
remembered to document, which is exactly the kind of drift that led to
this pipeline existing in the first place (see CHANGELOG.md's gap
between 1.2.0 and the several tags that followed it before this).
"""

import re
import sys
from pathlib import Path


def extract_section(changelog_text: str, version: str) -> str:
    # Matches "## [1.3.0]" with anything (date, etc.) after it on the
    # same line, case-sensitive on the version string itself.
    heading_pattern = re.compile(
        r"^## \[" + re.escape(version) + r"\].*$", re.MULTILINE
    )
    match = heading_pattern.search(changelog_text)
    if not match:
        raise ValueError(f"No CHANGELOG.md entry found for version {version}")

    start = match.end()
    next_heading = re.search(r"^## \[", changelog_text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(changelog_text)

    section = changelog_text[start:end].strip()
    if not section:
        raise ValueError(
            f"CHANGELOG.md has a heading for {version} but no content under it"
        )
    return section


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_release_notes.py <version>", file=sys.stderr)
        return 2

    version = sys.argv[1].lstrip("v")  # tolerate being passed "v1.3.0" or "1.3.0"
    changelog_path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

    try:
        changelog_text = changelog_path.read_text()
        section = extract_section(changelog_text, version)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        print(
            "Add a changelog entry for this version before releasing "
            "(see CONTRIBUTING.md).",
            file=sys.stderr,
        )
        return 1

    print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())