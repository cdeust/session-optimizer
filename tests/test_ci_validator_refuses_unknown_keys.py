"""The ci.json validator of both Pipeline workflows refuses a key it does not know.

Cortex's tests under scripts/tests hold what the validator requires of the keys
it knows. This one holds the other half of the contract in this repository: a
misspelt key is a fault, not a silently ignored entry. The validator is run as
the `contract` job runs it: `python3 - <workspace> --github-output`, its source
on standard input.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("pipeline.yml", "pipeline-post-merge.yml")
# source: the heredoc that opens the validator in both workflows.
OPENER = "          python3 - \"$GITHUB_WORKSPACE\" --github-output <<'PY'\n"
CLOSER = "\n          PY"


def validator_source(workflow: str) -> str:
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert text.count(OPENER) == 1, workflow
    return textwrap.dedent(text.split(OPENER, 1)[1].split(CLOSER, 1)[0])


def validate(workflow: str, workspace: Path, **extra) -> subprocess.CompletedProcess:
    """Run the validator on a copy of this repository's ci.json plus `extra`."""
    document = json.loads((ROOT / "ci.json").read_text(encoding="utf-8"))
    document.update(extra)
    (workspace / "ci.json").write_text(json.dumps(document), encoding="utf-8")
    version_file = workspace / document["version_file"]
    version_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / document["version_file"], version_file)
    output = workspace / "github-output"
    return subprocess.run(
        [sys.executable, "-", str(workspace), "--github-output"],
        input=validator_source(workflow),
        cwd=workspace,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_this_repository_s_ci_json_is_valid(workflow, tmp_path):
    done = validate(workflow, tmp_path)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_a_key_the_validator_does_not_know_is_refused(workflow, tmp_path):
    done = validate(workflow, tmp_path, low_risk_file=["requirements-dev.txt"])
    assert done.returncode == 1
    assert "low_risk_file: unknown key" in done.stderr
