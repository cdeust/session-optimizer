#!/usr/bin/env python3
"""Fail when requirements-dev.in and requirements-dev.txt disagree.

Issue #39: CI used to install from a hand-named requirements-dev.lock that
drifted from requirements-dev.txt silently. The fix moved the pair to the
naming dependabot-core's uv ecosystem recognizes for a compiled lockfile:
requirements-dev.in is the human-edited manifest, requirements-dev.txt is
the uv-compiled, hash-locked file CI actually installs
(uv/lib/dependabot/uv/requirements_file_matcher.rb, pinned at
https://github.com/dependabot/dependabot-core/blob/f3a79fa0711aca171b2174b855d9f21b16237961/uv/lib/dependabot/uv/requirements_file_matcher.rb:
a `.txt` lockfile matches its manifest when either it carries a
`--output-file=<name>` pragma naming itself, or a same-basename `.in` file
exists; requirements-dev.txt has both). Dependabot can now regenerate
requirements-dev.txt itself. This guard is the belt-and-suspenders check
for the window between a hand edit and the next dependabot run, and for
any regeneration that used the wrong command.

Compares the pinned version of every direct dependency in
requirements-dev.in against its pin in requirements-dev.txt and fails on
any mismatch or omission. One-directional by design: a compiled
`--universal` lock legitimately carries transitive dependencies (e.g.
iniconfig, packaging, pluggy, pygments) that never appear in the manifest,
so checking the reverse direction (lock -> manifest) would fail on every
compile. This guard only ever asks "is every *direct* pin honored?".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# name[extras]==version, ignoring comments/blank lines/options and a
# trailing environment marker (`; sys_platform == ...`) or continuation
# backslash -- match() only needs a valid prefix, so neither has to be
# stripped first.
_PIN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)"  # package name
    r"(?:\[[^\]]*\])?"  # optional extras, e.g. coverage[toml]
    r"=="
    r"([A-Za-z0-9][A-Za-z0-9._+-]*)"  # version
)


def _normalize(name: str) -> str:
    """PEP 503 normalized distribution name.

    https://peps.python.org/pep-0503/#normalized-names -- runs of `-`, `_`,
    `.` collapse to a single `-`, then lowercase. Without this, `Foo-Bar`
    and `foo_bar` compare as different packages and the guard reports a
    false "absent from the lock" for a pin that is actually present.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_pins(text: str) -> dict[str, str]:
    """Extract {normalized_name: version} for every top-level (column-0)
    pin line. Lenient: used for requirements-dev.txt, the compiled lock,
    which legitimately carries hash-continuation and `# via` comment lines
    this function is expected to skip."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t", "#")):
            continue  # hash/`# via` continuation lines and comments
        match = _PIN_RE.match(line)
        if match:
            pins[_normalize(match.group(1))] = match.group(2)
    return pins


def _parse_manifest_pins(text: str) -> dict[str, str]:
    """Strict parse for requirements-dev.in.

    Every non-blank, non-comment, non-option line MUST be a parseable pin,
    or this raises ValueError naming the exact line. A manifest line the
    parser can't classify must never be silently skipped: that is the
    "guard silently passes a real drift" failure mode this tool exists to
    prevent, not a shape it should also be capable of.
    """
    pins: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", "-")):
            continue  # blank, comment, or a pip option line (-e, -r, ...)
        match = _PIN_RE.match(line)
        if not match:
            raise ValueError(
                f"unparseable requirements-dev.in line {lineno}: {raw_line!r}"
            )
        pins[_normalize(match.group(1))] = match.group(2)
    return pins


def find_drift(manifest_pins: dict[str, str], lock_pins: dict[str, str]) -> list[str]:
    """Return one message per direct dependency missing or mismatched in the
    lock. Empty list means the lock is current for every declared pin."""
    problems = []
    for name, manifest_version in sorted(manifest_pins.items()):
        lock_version = lock_pins.get(name)
        if lock_version is None:
            problems.append(
                f"{name}=={manifest_version} declared but absent from the lock"
            )
        elif lock_version != manifest_version:
            problems.append(
                f"{name}: requirements-dev.in pins {manifest_version}, "
                f"requirements-dev.txt pins {lock_version}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="path to requirements-dev.in")
    parser.add_argument("lock", type=Path, help="path to requirements-dev.txt")
    args = parser.parse_args(argv)

    try:
        manifest_pins = _parse_manifest_pins(args.manifest.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    lock_pins = _parse_pins(args.lock.read_text(encoding="utf-8"))
    problems = find_drift(manifest_pins, lock_pins)

    if problems:
        print(f"{args.lock} is out of date with {args.manifest}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Regenerate with: uv pip compile requirements-dev.in "
            "--generate-hashes --universal --python-version 3.11 "
            f"--output-file={args.lock}",
            file=sys.stderr,
        )
        return 1

    print(f"{args.lock} matches every pin in {args.manifest}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
