#!/usr/bin/env python3
"""Fail when requirements-dev.txt and requirements-dev.lock disagree.

Issue #39: CI installs from requirements-dev.lock
(`pip install --require-hashes -r requirements-dev.lock`), so a version
bumped in requirements-dev.txt (by hand or by dependabot) is invisible to CI
until someone remembers to regenerate the lock. This compares the pinned
version of every direct dependency in requirements-dev.txt against its pin
in requirements-dev.lock and fails on any mismatch or omission.

Dependabot cannot regenerate this lock for us: dependabot-core's pip-compile
lockfile matcher requires the lockfile to end in `.txt`
(`return false unless name.end_with?(".txt")` — pip_compile_file_matcher.rb,
https://github.com/dependabot/dependabot-core/blob/main/python/lib/dependabot/python/pip_compile_file_matcher.rb),
and requirements-dev.lock does not. This guard is the substitute: it makes
the drift a merge-blocking CI failure instead of a silent gap.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# name==version, ignoring comments/blank lines/markers (`; sys_platform == ...`).
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+-]*)")


def _parse_pins(text: str) -> dict[str, str]:
    """Extract {package: version} for every top-level (column-0) pin line."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t", "#")):
            continue  # hash/`# via` continuation lines and comments
        match = _PIN_RE.match(line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def find_drift(txt_pins: dict[str, str], lock_pins: dict[str, str]) -> list[str]:
    """Return one message per direct dependency missing or mismatched in the
    lock. Empty list means the lock is current for every declared pin."""
    problems = []
    for name, txt_version in sorted(txt_pins.items()):
        lock_version = lock_pins.get(name)
        if lock_version is None:
            problems.append(f"{name}=={txt_version} declared but absent from the lock")
        elif lock_version != txt_version:
            problems.append(
                f"{name}: requirements-dev.txt pins {txt_version}, "
                f"requirements-dev.lock pins {lock_version}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("txt", type=Path, help="path to requirements-dev.txt")
    parser.add_argument("lock", type=Path, help="path to requirements-dev.lock")
    args = parser.parse_args(argv)

    txt_pins = _parse_pins(args.txt.read_text(encoding="utf-8"))
    lock_pins = _parse_pins(args.lock.read_text(encoding="utf-8"))
    problems = find_drift(txt_pins, lock_pins)

    if problems:
        print(f"{args.lock} is out of date with {args.txt}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Regenerate with: uv pip compile requirements-dev.txt "
            "--generate-hashes --universal --python-version 3.11 "
            f"-o {args.lock}",
            file=sys.stderr,
        )
        return 1

    print(f"{args.lock} matches every pin in {args.txt}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
