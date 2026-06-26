#!/usr/bin/env python3
"""Assert that the new version is greater than the max version published on PyPI.

Usage: check_pypi_version.py <new_version> <pypi_json_file>

The JSON file is the body of https://pypi.org/pypi/robotops-trace/json. If no
valid published versions are found, the first publish is allowed. Exits non-zero
(with a GitHub Actions ::error:: annotation) when new_version <= the published max.
"""

from __future__ import annotations

import json
import sys

from packaging.version import InvalidVersion, Version


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <new_version> <pypi_json_file>", file=sys.stderr)
        return 2

    new = Version(argv[1])

    with open(argv[2]) as fh:
        data = json.load(fh)

    published: list[Version] = []
    for raw in data.get("releases", {}):
        try:
            published.append(Version(raw))
        except InvalidVersion:
            continue

    if not published:
        print("No published versions found on PyPI. First publish allowed.")
        return 0

    latest = max(published)
    print(f"Latest published version: {latest}")
    print(f"New version: {new}")

    if new <= latest:
        print(f"::error::New version ({new}) must be greater than published version ({latest})")
        return 1

    print(f"Version check passed: {new} > {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
