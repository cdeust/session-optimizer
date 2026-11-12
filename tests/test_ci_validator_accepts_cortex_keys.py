"""The ci.json validator accepts the two keys Cortex's workflows read.

Cortex ADR-1134, decision 4: a repository that takes Cortex's workflows first
teaches its own validator to accept the keys it lacks. The Pipeline after merge
validates the merged ci.json with the validator of the commit before the merge,
so the key cannot arrive with the validator that accepts it.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = (
    ".github/workflows/pipeline.yml",
    ".github/workflows/pipeline-post-merge.yml",
)
# source: the heredoc of the step "Validate ci.json" in both workflows.
OPENER = "          python3 - \"$GITHUB_WORKSPACE\" --github-output <<'PY'\n"
CLOSER = "\n          PY"


def validator_source(workflow: str) -> str:
    text = (ROOT / workflow).read_text(encoding="utf-8")
    assert text.count(OPENER) == 1, f"{workflow}: the validator heredoc moved"
    return textwrap.dedent(text.split(OPENER, 1)[1].split(CLOSER, 1)[0])


def faults(workflow: str, tmp_path: Path, **extra) -> list:
    namespace = {"__name__": "embedded_validator"}
    exec(compile(validator_source(workflow), workflow, "exec"), namespace)
    document = {key: "true" for key in namespace["KEYS"]}
    document["version_file"] = "v.json"
    document.update(extra)
    (tmp_path / "v.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    (tmp_path / "ci.json").write_text(json.dumps(document), encoding="utf-8")
    return namespace["faults"](tmp_path)


def test_the_two_workflows_embed_the_same_validator():
    first, second = (validator_source(workflow) for workflow in WORKFLOWS)
    assert first == second


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_the_keys_of_cortex_are_accepted(workflow, tmp_path):
    assert faults(workflow, tmp_path) == []
    assert faults(workflow, tmp_path, low_risk_files=["uv.lock"]) == []
    assert faults(workflow, tmp_path, docs_paths=["docs/"]) == []


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_any_other_key_is_still_refused(workflow, tmp_path):
    found = faults(workflow, tmp_path, low_risk_file=["uv.lock"])
    assert len(found) == 1
    assert found[0].startswith("low_risk_file: unknown key (allowed: ")
