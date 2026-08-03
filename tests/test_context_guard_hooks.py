"""Behavioral coverage for the context-guard hook boundary."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "plugins" / "context-guard" / "hooks"
TOOLS = ROOT / "plugins" / "context-guard" / "tools"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

protocol = _load("session_optimizer_checkpoint_protocol", HOOKS / "checkpoint_protocol.py")
guard = _load("session_optimizer_stop_guard", HOOKS / "stop-context-guard.py")
usage_core = _load("session_optimizer_usage_core", TOOLS / "subagent_usage.py")
tracker = _load("session_optimizer_subagent_tracker", HOOKS / "subagent-tracker.py")


def test_protocol_detects_project_tool_and_renders_both_contracts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_tool = tmp_path / "tools" / "memory-tool.sh"
    project_tool.parent.mkdir()
    project_tool.write_text("#!/bin/sh\n")
    assert protocol.detect_memory_tool(str(tmp_path)) == str(project_tool)
    assert protocol.detect_memory_tool(str(tmp_path / "missing")) is None

    generic_warn = protocol.warn_reason(180_000, "/tmp/check.md", 180_000, 200_000)
    scoped_warn = protocol.warn_reason_scoped(180_000, "", 180_000, 200_000)
    generic_hard = protocol.block_reason(200_000, "", 200_000)
    scoped_hard = protocol.block_reason_scoped(200_000, "/tmp/check.md", 200_000)
    assert "memory-writer" in generic_warn
    assert "remember endpoint" in scoped_warn
    assert "latest.md" in generic_hard
    assert "MEMORY_AGENT_ID" in scoped_hard


def test_threshold_config_and_fallback(tmp_path, monkeypatch):
    config = tmp_path / "thresholds.json"
    config.write_text(json.dumps({
        "models": [{"match": "mini", "warn": 10, "hard": 20}],
        "default": {"warn": 30, "hard": 40},
    }))
    monkeypatch.setattr(guard, "CONFIG_PATH", str(config))
    assert guard._thresholds("agent-mini") == (10, 20)
    assert guard._thresholds("other") == (30, 40)

    config.write_text('{"models":[{"match":"broken"}],"default":{"warn":9,"hard":2}}')
    assert guard._thresholds("broken") == (180_000, 200_000)
    config.write_text("not json")
    assert guard._thresholds("haiku-4") == (120_000, 170_000)


@pytest.mark.parametrize("line", ["", "not json", "{}", '{"message":{"usage":{}}}'])
def test_usage_line_rejects_non_usage(line):
    assert guard._usage_from_line(line) is None


def test_usage_line_and_reverse_tail_reader(tmp_path, monkeypatch):
    line = json.dumps({"message": {"model": "opus", "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 5,
    }}})
    assert guard._usage_from_line(line) == (10, "opus")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("noise\n" * 30 + line + "\ntrailing junk\n")
    monkeypatch.setattr(guard, "TAIL_CHUNK", 64)
    monkeypatch.setattr(guard, "TAIL_MAX_BYTES", 512)
    assert guard._read_last_usage(str(transcript)) == (10, "opus")
    assert guard._read_last_usage(str(tmp_path / "missing")) == (None, None)
    empty = tmp_path / "empty"
    empty.write_text("")
    assert guard._read_last_usage(str(empty)) == (None, None)


def test_subagent_summary_line_and_git_fail_open(tmp_path, monkeypatch):
    real_join = guard.os.path.join
    monkeypatch.setattr(
        guard.os.path,
        "join",
        lambda root, leaf: str(tmp_path / leaf) if root == "/tmp" else real_join(root, leaf),
    )
    assert guard._subagent_summary("none") == (0, 0, 0.0)
    state = tmp_path / "zetetic-subagents-s1.json"
    state.write_text(json.dumps({"totals": {
        "count": 2, "input_tokens": 10, "output_tokens": 5,
        "cache_tokens": 20, "cost_usd": 1.25,
    }}))
    assert guard._subagent_summary("s1") == (2, 35, 1.25)
    assert "2 runs" in guard._subagent_line("s1")
    assert guard._subagent_line("none") == ""

    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=" main \n"))
    assert guard._git(str(tmp_path), "status") == "main"
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert guard._git(str(tmp_path), "status") == ""


def test_stub_and_level_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(guard, "_git", lambda _cwd, *args: {
        "symbolic-ref": "feature", "log": "abc subject", "status": " M README.md",
    }.get(args[0], ""))
    monkeypatch.setattr(guard, "_subagent_summary", lambda _sid: (2, 3000, 0.5))
    stub = guard._write_stub("session-123", str(tmp_path), 190_000, "opus", "warn")
    text = Path(stub).read_text()
    assert "feature" in text and "README.md" in text and "2 runs" in text
    assert (Path(stub).parent / "latest.md").read_text() == text

    monkeypatch.setattr(guard, "STATE_DIR", str(tmp_path))
    assert guard._load_level("fresh") == "none"
    guard._save_level("fresh", "warn")
    assert guard._load_level("fresh") == "warn"
    (tmp_path / "zetetic-ctxguard-bad.json").write_text("bad")
    assert guard._load_level("bad") == "none"


def _run_guard_main(monkeypatch, payload, *, ctx=(190_000, "opus"), prev="none", scoped=False):
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(guard.sys, "stdin", stdin)
    monkeypatch.setattr(guard.sys, "stdout", stdout)
    monkeypatch.setattr(guard, "_read_last_usage", lambda _path: ctx)
    monkeypatch.setattr(guard, "_thresholds", lambda _model: (180_000, 200_000))
    monkeypatch.setattr(guard, "_load_level", lambda _sid: prev)
    monkeypatch.setattr(guard, "_save_level", lambda *_: None)
    monkeypatch.setattr(guard, "_write_stub", lambda *_: "/tmp/check.md")
    monkeypatch.setattr(guard, "_subagent_line", lambda _sid: "\nsubagents")
    monkeypatch.setattr(guard.checkpoint_protocol, "detect_memory_tool", lambda _cwd: "/tool" if scoped else None)
    with pytest.raises(SystemExit) as exc:
        guard.main()
    assert exc.value.code == 0
    return stdout.getvalue()


def test_guard_main_fail_open_and_threshold_paths(monkeypatch):
    assert _run_guard_main(monkeypatch, "not json") == ""
    assert _run_guard_main(monkeypatch, {"stop_hook_active": True}) == ""
    assert _run_guard_main(monkeypatch, {}, ctx=(None, None)) == ""
    assert _run_guard_main(monkeypatch, {}, ctx=(100, "opus")) == ""
    assert _run_guard_main(monkeypatch, {}, prev="warn") == ""


def test_guard_main_warn_and_hard_payloads(monkeypatch):
    warn = json.loads(_run_guard_main(monkeypatch, {"session_id": "s", "cwd": "/x"}))
    assert warn["decision"] == "block"
    assert "memory-writer" in warn["reason"]
    assert "subagents" in warn["systemMessage"]
    hard = json.loads(_run_guard_main(
        monkeypatch, {"session_id": "s", "cwd": "/x"},
        ctx=(210_000, "opus"), prev="warn", scoped=True,
    ))
    assert hard["decision"] == "block"
    assert "MEMORY_AGENT_ID" in hard["reason"]


def test_tracker_helpers_and_main_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "_state_path", lambda sid: str(tmp_path / f"{sid}.json"))
    assert tracker._load_state("s") == {"session_id": "s", "agents": {}}
    (tmp_path / "s.json").write_text(json.dumps({"session_id": "s", "agents": {}}))
    assert tracker._load_state("s")["session_id"] == "s"

    usage = usage_core.Usage(input_tokens=3, output_tokens=4, cache_write_5m=5,
                             cache_write_1h=6, cache_read=7, tool_uses=2,
                             web_search_requests=1, web_fetch_requests=2,
                             model="opus")
    rec = usage_core.SubagentRecord("a", "Explore", "look", "t1", usage, 1.23456, "/a")
    entry = tracker._agent_entry(rec)
    assert entry["cache_tokens"] == 18 and entry["cost_usd"] == 1.2346
    state = {"agents": {"a": entry, "b": {"input_tokens": 2, "cost_usd": 0.1}}}
    tracker._recompute_totals(state)
    assert state["totals"]["count"] == 2
    assert state["totals"]["input_tokens"] == 5

    monkeypatch.setattr(tracker, "subagent_record", lambda path: rec if path else None)
    tracker._update_from_transcript({"agents": {}}, "")
    payload_path = tmp_path / "agent-a.jsonl"
    payload_path.write_text("{}\n")
    monkeypatch.setattr(tracker, "session_dir_for", lambda _path: str(tmp_path))
    monkeypatch.setattr(tracker, "discover_subagents", lambda _dir: [str(payload_path), str(tmp_path / "agent-b.jsonl")])
    monkeypatch.setattr(tracker.sys, "stdin", io.StringIO(json.dumps({
        "session_id": "s", "transcript_path": str(payload_path),
    })))
    with pytest.raises(SystemExit) as exc:
        tracker.main()
    assert exc.value.code == 0
    saved = json.loads((tmp_path / "s.json").read_text())
    assert saved["totals"]["count"] == 1
    assert saved["agents"]["a"]["agent_type"] == "Explore"


def test_tracker_malformed_input_is_nonfatal(monkeypatch):
    monkeypatch.setattr(tracker.sys, "stdin", io.StringIO("bad"))
    with pytest.raises(SystemExit) as exc:
        tracker.main()
    assert exc.value.code == 0
