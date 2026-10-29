"""Permanent guard: no check may compare a PR's workflow or baseline with main's.

Owner rule 2026-10-29: no gate makes a decision depend on the identity of a
hash, the tip or the content of main; behaviour is observed instead. The check
that compared the pipeline.yml executed by a pull request with the one on main
(and refused the autonomous merge with "workflow proof definition is not
trusted") was removed. Any reintroduction under one of the markers below, in
.github/workflows/, scripts/, tools/ or hooks/, fails this test. This file is
excluded from its own scan.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
SCANNED = (".github/workflows", "scripts", "tools", "hooks")
FORBIDDEN = (
    "authorize_workflow",
    "workflow proof definition",
    "WORKFLOW_PROOF",
    "ci_workflow_proof",
    "workflow_pr_perime",
    "update-branch",
    "update_branch",
    "merge-base HEAD origin/main",
)
PATTERN = re.compile("|".join(re.escape(m) for m in FORBIDDEN), re.IGNORECASE)
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}


def _candidate_files(base: Path, skip: Path | None):
    for path in sorted(base.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.parts):
            continue
        if skip is not None and path.resolve() == skip:
            continue
        yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _file_hits(path: Path, root: Path) -> list[str]:
    hits = []
    for number, line in enumerate(_read_text(path).splitlines(), 1):
        match = PATTERN.search(line)
        if match:
            hits.append(f"{path.relative_to(root)}:{number}: {match.group(0)}")
    return hits


def find_violations(
    root: Path, scanned: tuple[str, ...] = SCANNED, skip: Path | None = SELF
) -> list[str]:
    hits: list[str] = []
    for rel in scanned:
        base = root / rel
        if base.is_dir():
            for path in _candidate_files(base, skip):
                hits.extend(_file_hits(path, root))
    return hits


def test_repository_has_no_comparison_against_main():
    assert (ROOT / ".github" / "workflows").is_dir(), f"wrong root: {ROOT}"
    assert find_violations(ROOT) == []


def test_guard_detects_every_forbidden_marker(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for index, marker in enumerate(FORBIDDEN):
        (workflows / f"w{index}.yml").write_text(f"run: {marker}\n", encoding="utf-8")
    hits = find_violations(tmp_path, skip=None)
    assert len(hits) == len(FORBIDDEN), hits
