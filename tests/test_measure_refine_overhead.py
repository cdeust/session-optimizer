"""Tests for the opt-in local refine-gate overhead measurement."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugins" / "refine-gate" / "tools" / "measure_refine_overhead.py"
SPEC = importlib.util.spec_from_file_location("session_optimizer_measure_refine", SCRIPT)
measure = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(measure)


def test_collect_prompts_filters_non_user_and_harness_records(tmp_path, monkeypatch):
    project = tmp_path / ".claude" / "projects" / "p"
    project.mkdir(parents=True)
    transcript = project / "s.jsonl"
    records = [
        {"type": "user", "message": {"content": " fix the cache "}},
        {"type": "user", "message": {"content": "<tool-result>"}},
        {"type": "user", "message": {"content": "Caveat: wrapper"}},
        {"type": "user", "message": {"content": ["tool result"]}},
        {"type": "assistant", "message": {"content": "ignored"}},
    ]
    transcript.write_text("bad json\n" + "".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setattr(measure.Path, "home", staticmethod(lambda: tmp_path))
    assert measure.collect_prompts() == ["fix the cache"]


def test_main_reports_tier_mix(monkeypatch, capsys):
    monkeypatch.setattr(measure, "collect_prompts", lambda: ["tier1", "tier2", "silent"])

    def fake_run(args, *, input, **kwargs):
        if "tier1" in input:
            ctx = "matched: prior solution"
        elif "tier2" in input:
            ctx = "names no concrete artifact"
        else:
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout=json.dumps({
            "hookSpecificOutput": {"additionalContext": ctx},
        }))

    monkeypatch.setattr(measure.subprocess, "run", fake_run)
    measure.main()
    out = capsys.readouterr().out
    assert "corpus: 3" in out
    assert "tier1 1, tier2 1, silent 1" in out
    assert "mean per prompt" in out
