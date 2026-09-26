"""Behavioral coverage for the context-guard hook boundary."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "plugins" / "context-guard" / "hooks"
TOOLS = ROOT / "plugins" / "context-guard" / "tools"
PLUGIN_ROOT = ROOT / "plugins" / "context-guard"
README = PLUGIN_ROOT / "README.md"


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

protocol = _load(
    "session_optimizer_checkpoint_protocol", HOOKS / "checkpoint_protocol.py"
)
guard = _load("session_optimizer_stop_guard", HOOKS / "stop-context-guard.py")
usage_core = _load("session_optimizer_usage_core", TOOLS / "subagent_usage.py")
tracker = _load("session_optimizer_subagent_tracker", HOOKS / "subagent-tracker.py")


def test_protocol_detects_project_tool_and_renders_both_contracts(
    tmp_path, monkeypatch
):
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
    assert "MEMORY_AGENT_ID" in scoped_warn
    assert "remember call" in scoped_warn
    assert "latest.md" in generic_hard
    assert "MEMORY_AGENT_ID" in scoped_hard


def test_threshold_config_and_fallback(tmp_path, monkeypatch):
    config = tmp_path / "thresholds.json"
    config.write_text(
        json.dumps(
            {
                "models": [{"match": "mini", "warn": 10, "hard": 20}],
                "default": {"warn": 30, "hard": 40},
            }
        )
    )
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
    line = json.dumps(
        {
            "message": {
                "model": "opus",
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 5,
                },
            }
        }
    )
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
    monkeypatch.setattr(guard, "STATE_DIR", str(tmp_path))
    assert guard._subagent_summary("none") == (0, 0, 0.0)
    state = tmp_path / "zetetic-subagents-s1.json"
    state.write_text(
        json.dumps(
            {
                "totals": {
                    "count": 2,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_tokens": 20,
                    "cost_usd": 1.25,
                }
            }
        )
    )
    assert guard._subagent_summary("s1") == (2, 35, 1.25)
    assert "2 runs" in guard._subagent_line("s1")
    assert guard._subagent_line("none") == ""

    monkeypatch.setattr(
        guard.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=" main \n")
    )
    assert guard._git(str(tmp_path), "status") == "main"
    monkeypatch.setattr(
        guard.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    assert guard._git(str(tmp_path), "status") == ""


def test_stub_and_level_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        guard,
        "_git",
        lambda _cwd, *args: {
            "symbolic-ref": "feature",
            "log": "abc subject",
            "status": " M README.md",
        }.get(args[0], ""),
    )
    monkeypatch.setattr(guard, "_subagent_summary", lambda _sid: (2, 3000, 0.5))
    ev = guard.FireEvent("session-123", str(tmp_path), 190_000, "opus", "warn")
    stub = guard._write_stub(ev)
    text = Path(stub).read_text()
    assert "feature" in text and "README.md" in text and "2 runs" in text
    assert (Path(stub).parent / "latest.md").read_text() == text

    monkeypatch.setattr(guard, "STATE_DIR", str(tmp_path))
    assert guard._load_state("fresh") == {"level": "none"}
    guard._save_state("fresh", {"level": "warn", "initial_ctx": 190_000})
    assert guard._load_state("fresh") == {"level": "warn", "initial_ctx": 190_000}
    (tmp_path / "zetetic-ctxguard-bad.json").write_text("bad")
    assert guard._load_state("bad") == {"level": "none"}
    (tmp_path / "zetetic-ctxguard-legacy.json").write_text('{"nope": true}')
    assert guard._load_state("legacy") == {"level": "none"}


def test_has_activity_since_and_line_has_tool_use(tmp_path):
    tool_use_line = json.dumps(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "name": "Read"},
                ]
            }
        }
    )
    text_only_line = json.dumps(
        {"message": {"content": [{"type": "text", "text": "hi"}]}}
    )
    assert guard._line_has_tool_use(tool_use_line) is True
    assert guard._line_has_tool_use(text_only_line) is False
    assert guard._line_has_tool_use("not json") is False
    assert guard._line_has_tool_use("") is False

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(text_only_line + "\n" + text_only_line + "\n")
    # Nothing but text since offset 0 -> no activity.
    assert guard._has_activity_since(str(transcript), 0) is False
    # Missing file -> fail-open (True), never silently suppress.
    assert guard._has_activity_since(str(tmp_path / "missing.jsonl"), 0) is True
    # since_offset >= size -> nothing appended -> no activity.
    size = transcript.stat().st_size
    assert guard._has_activity_since(str(transcript), size) is False

    with_tool = tmp_path / "with_tool.jsonl"
    with_tool.write_text(text_only_line + "\n" + tool_use_line + "\n")
    assert guard._has_activity_since(str(with_tool), 0) is True
    # Activity only appears before since_offset -> not counted as "since".
    offset_after_tool_use = len(
        (text_only_line + "\n" + tool_use_line + "\n").encode("utf-8")
    )
    with_tool.write_text(
        text_only_line + "\n" + tool_use_line + "\n" + text_only_line + "\n"
    )
    assert guard._has_activity_since(str(with_tool), offset_after_tool_use) is False


def test_has_activity_since_fails_open_when_the_scan_cap_is_hit_before_eof(
    tmp_path, monkeypatch
):
    """The scan-cap ("cap") branch of the eof/cap distinction: no tool_use
    anywhere, but the transcript is larger than what TAIL_MAX_BYTES allows to
    be scanned before EOF -- must fail OPEN (True), never a silent False."""
    monkeypatch.setattr(guard, "TAIL_CHUNK", 8)
    monkeypatch.setattr(guard, "TAIL_MAX_BYTES", 16)
    text_only_line = json.dumps(
        {"message": {"content": [{"type": "text", "text": "hi"}]}}
    )
    transcript = tmp_path / "big.jsonl"
    transcript.write_text(
        (text_only_line + "\n") * 20
    )  # far larger than the 16-byte cap
    assert guard._has_activity_since(str(transcript), 0) is True


def test_has_activity_since_offset_equal_size_is_false_even_with_earlier_tool_use(
    tmp_path,
):
    """Mutation-kill for _normalize_since_offset's `since_offset > size`
    boundary (a `>=` mutant would treat since_offset == size as "stale" and
    rescan from 0). The transcript carries a tool_use line strictly BEFORE
    since_offset, so a wrongly-triggered rescan-from-0 would find it and
    return True; the correct behavior is False, because since_offset == size
    means nothing has been appended since the last fire -- no rescan needed
    regardless of what came before it."""
    tool_use_line = json.dumps(
        {"message": {"content": [{"type": "tool_use", "name": "Read"}]}}
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(tool_use_line + "\n")
    since_offset = transcript.stat().st_size  # == size: exactly caught up
    assert guard._has_activity_since(str(transcript), since_offset) is False


def _run_guard_main(monkeypatch, payload, **session):
    """Run guard.main() on payload against a faked session: ctx (tokens, model),
    prev (last recorded level), scoped (memory tool present), has_activity."""
    ctx = session.pop("ctx", (190_000, "opus"))
    prev = session.pop("prev", "none")
    scoped = session.pop("scoped", False)
    has_activity = session.pop("has_activity", True)
    assert not session, f"unknown session options: {sorted(session)}"
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(guard.sys, "stdin", stdin)
    monkeypatch.setattr(guard.sys, "stdout", stdout)
    monkeypatch.setattr(guard, "_read_last_usage", lambda _path: ctx)
    monkeypatch.setattr(guard, "_thresholds", lambda _model: (180_000, 200_000))
    monkeypatch.setattr(guard, "_load_state", lambda _sid: {"level": prev})
    monkeypatch.setattr(guard, "_save_state", lambda *_: None)
    monkeypatch.setattr(guard, "_has_activity_since", lambda *_: has_activity)
    monkeypatch.setattr(guard, "_write_stub", lambda *_: "/tmp/check.md")
    monkeypatch.setattr(guard, "_subagent_line", lambda _sid: "\nsubagents")
    monkeypatch.setattr(
        guard.checkpoint_protocol,
        "detect_memory_tool",
        lambda _cwd: "/tool" if scoped else None,
    )
    with pytest.raises(SystemExit) as exc:
        guard.main()
    assert exc.value.code == 0
    return stdout.getvalue()


def test_guard_main_fail_open_and_threshold_paths(monkeypatch):
    assert _run_guard_main(monkeypatch, "not json") == ""
    # Valid JSON that is not a dict (e.g. a bare list) must not crash the
    # hook with AttributeError on `.get` -- a Stop hook must never fail hard.
    assert _run_guard_main(monkeypatch, "[1, 2, 3]") == ""
    assert _run_guard_main(monkeypatch, {"stop_hook_active": True}) == ""
    assert _run_guard_main(monkeypatch, {}, ctx=(None, None)) == ""
    assert _run_guard_main(monkeypatch, {}, ctx=(100, "opus")) == ""
    assert _run_guard_main(monkeypatch, {}, prev="warn") == ""


def test_guard_main_skips_silently_when_no_activity_since_last_fire(monkeypatch):
    # The exact reproduced bug: ctx above WARN, but nothing checkpointable
    # happened since session start / last fire -> exit silently, no block.
    assert (
        _run_guard_main(
            monkeypatch, {"session_id": "s", "cwd": "/x"}, has_activity=False
        )
        == ""
    )


def test_guard_main_distrusts_a_stale_offset_from_a_different_transcript(
    tmp_path, monkeypatch
):
    """A WARN fire recorded an offset against transcript A; a later Stop on
    the same session_id but a DIFFERENT transcript_path (rotation/compaction)
    must not compare that offset against the new file -- it must rescan from
    0, or a coincidentally-similar offset could wrongly suppress HARD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(guard, "STATE_DIR", str(tmp_path))
    tool_use_line = json.dumps(
        {"message": {"content": [{"type": "tool_use", "name": "Read"}]}}
    )

    def usage_line(ctx):
        return json.dumps(
            {"message": {"model": "opus", "usage": {"input_tokens": ctx}}}
        )

    transcript_a = tmp_path / "a.jsonl"
    transcript_a.write_text(usage_line(185_000) + "\n" + tool_use_line + "\n")
    guard._save_state(
        "s",
        {
            "level": "warn",
            "last_fire_offset": transcript_a.stat().st_size,
            "transcript_path": str(transcript_a),
        },
    )

    transcript_b = tmp_path / "b.jsonl"
    transcript_b.write_text(tool_use_line + "\n" + usage_line(210_000) + "\n")

    stdin = io.StringIO(
        json.dumps(
            {
                "session_id": "s",
                "cwd": str(tmp_path),
                "transcript_path": str(transcript_b),
            }
        )
    )
    stdout = io.StringIO()
    monkeypatch.setattr(guard.sys, "stdin", stdin)
    monkeypatch.setattr(guard.sys, "stdout", stdout)
    monkeypatch.setattr(guard, "_thresholds", lambda _model: (180_000, 200_000))
    monkeypatch.setattr(
        guard.checkpoint_protocol, "detect_memory_tool", lambda _cwd: None
    )
    with pytest.raises(SystemExit) as exc:
        guard.main()
    assert exc.value.code == 0
    decision = json.loads(stdout.getvalue())
    assert decision["decision"] == "block"


def test_guard_main_warn_and_hard_payloads(monkeypatch):
    warn = json.loads(_run_guard_main(monkeypatch, {"session_id": "s", "cwd": "/x"}))
    assert warn["decision"] == "block"
    assert "memory-writer" in warn["reason"]
    assert "subagents" in warn["systemMessage"]
    hard = json.loads(
        _run_guard_main(
            monkeypatch,
            {"session_id": "s", "cwd": "/x"},
            ctx=(210_000, "opus"),
            prev="warn",
            scoped=True,
        )
    )
    assert hard["decision"] == "block"
    assert "MEMORY_AGENT_ID" in hard["reason"]


def test_tracker_helpers_and_main_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tracker, "_state_path", lambda sid: str(tmp_path / f"{sid}.json")
    )
    assert tracker._load_state("s") == {"session_id": "s", "agents": {}}
    (tmp_path / "s.json").write_text(json.dumps({"session_id": "s", "agents": {}}))
    assert tracker._load_state("s")["session_id"] == "s"

    usage = usage_core.Usage(
        input_tokens=3,
        output_tokens=4,
        cache_write_5m=5,
        cache_write_1h=6,
        cache_read=7,
        tool_uses=2,
        web_search_requests=1,
        web_fetch_requests=2,
        model="opus",
    )
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
    monkeypatch.setattr(
        tracker,
        "discover_subagents",
        lambda _dir: [str(payload_path), str(tmp_path / "agent-b.jsonl")],
    )
    monkeypatch.setattr(
        tracker.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "session_id": "s",
                    "transcript_path": str(payload_path),
                }
            )
        ),
    )
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


def _extract_fenced_bash_block(heading: str) -> str:
    """Extract the exact ```bash fenced block that follows `heading` in the
    context-guard README. Reads the README fresh each call so the test always
    exercises the documented command, never a copy of it."""
    text = README.read_text(encoding="utf-8")
    heading_at = text.index(heading)
    fence_start = text.index("```bash", heading_at)
    body_start = text.index("\n", fence_start) + 1
    fence_end = text.index("```", body_start)
    return text[body_start:fence_end]


def _run_readme_snippet(script: str, fake_home: Path) -> str:
    """Run a README bash snippet as a real subprocess from the plugin root,
    with HOME sandboxed to `fake_home`. Returns captured stdout.

    The snippet's own trailing cleanup commands (rm -f/-rf) always exit 0, so
    the process return code cannot distinguish a working guard from a guard
    that emitted nothing on stdin -- only the captured stdout can.
    """
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=str(PLUGIN_ROOT),
        env={**os.environ, "HOME": str(fake_home)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout


def test_readme_test_the_guard_snippet_fires_block_on_first_and_second_run(tmp_path):
    """External gate for the Stop-guard smoke test documented in README.md
    under '### Test the guard'. Reproduces the documented command exactly
    (parsed from the README file, not duplicated as a literal string) and
    asserts it actually produces `decision: block`, twice in a row -- proving
    the fire-once state file does not silence the second demonstration."""
    script = _extract_fenced_bash_block("### Test the guard")

    # The snippet is self-sandboxing (it creates and later removes its own
    # throwaway HOME internally); the outer HOME below is a second, unrelated
    # safety net in case the snippet regresses and stops sandboxing itself.
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    real_checkpoint = (
        Path(os.path.expanduser("~"))
        / ".claude"
        / "memories"
        / "checkpoints"
        / "latest.md"
    )
    before_mtime = real_checkpoint.stat().st_mtime if real_checkpoint.exists() else None

    first_stdout = _run_readme_snippet(script, fake_home)
    assert first_stdout.strip(), (
        "README 'Test the guard' snippet produced no output on its first run "
        "(the activity gate silently ate it) -- expected a decision:block payload"
    )
    first = json.loads(first_stdout)
    assert first["decision"] == "block"

    second_stdout = _run_readme_snippet(script, fake_home)
    assert second_stdout.strip(), (
        "README 'Test the guard' snippet produced no output on its second run "
        "-- the fire-once state file silenced a repeat demonstration"
    )
    second = json.loads(second_stdout)
    assert second["decision"] == "block"

    # The snippet must not have touched the user's real checkpoint file.
    after_mtime = real_checkpoint.stat().st_mtime if real_checkpoint.exists() else None
    assert after_mtime == before_mtime, (
        "README 'Test the guard' snippet wrote to the user's real "
        "~/.claude/memories/checkpoints/latest.md instead of a sandboxed HOME"
    )
    assert "FAKE_HOME" in script and "HOME=" in script, (
        "README snippet must sandbox HOME so it cannot overwrite the user's "
        "real checkpoint file"
    )
    assert re.search(r"rm\s+-f\s+.*zetetic-ctxguard-", script), (
        "README snippet must clean up its fire-once state file so a repeat "
        "run of the exact same snippet (fixed session_id) still fires"
    )


def test_readme_test_the_tracker_snippet_cleans_up_its_state_file():
    """The tracker smoke test writes /tmp/zetetic-subagents-<SID>.json; the
    documented snippet must remove it afterward instead of leaving stray
    state files in a shared temp directory."""
    script = _extract_fenced_bash_block("### Test the tracker")
    assert re.search(r"rm\s+-f\s+.*zetetic-subagents-", script), (
        "README 'Test the tracker' snippet does not clean up its state file"
    )


GUARD_SIBLING_MODULES = [
    "checkpoint_protocol",
    "checkpoint_stub",
    "subagent_spend",
    "thresholds",
    "transcript_lines",
    "transcript_scan",
]


@pytest.mark.parametrize("missing_module", GUARD_SIBLING_MODULES)
def test_guard_degrades_to_inert_when_a_sibling_module_is_missing(
    tmp_path, missing_module
):
    """A manual install that copies only the entry-point script (the named
    failure mode in stop-context-guard.py's own module docstring) must
    degrade to inert -- exit 0, no stdout -- for EACH of its five sibling
    modules, never crash with an ImportError traceback that would contradict
    the Stop hook's own 'never fail hard' contract. Exercised as a real
    subprocess against a copy of hooks/ with exactly one module deleted, so
    the import actually fails at process start (not a monkeypatched stand-in)."""
    dest = tmp_path / "hooks"
    shutil.copytree(HOOKS, dest)
    (dest / f"{missing_module}.py").unlink()

    payload = {
        "session_id": "s",
        "cwd": str(tmp_path),
        "transcript_path": str(tmp_path / "missing.jsonl"),
        "stop_hook_active": False,
    }
    proc = subprocess.run(
        [sys.executable, str(dest / "stop-context-guard.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_tracker_degrades_to_inert_when_shared_core_is_missing(tmp_path):
    """Equivalent 'never fail hard' contract for subagent-tracker.py: with
    tools/subagent_usage.py absent, the import falls back to its own no-op
    stub functions (subagent_record/discover_subagents/session_dir_for all
    returning empty results) rather than exiting early -- main() still runs
    to completion and the hook still exits 0 with no stdout."""
    dest_hooks = tmp_path / "hooks"
    shutil.copytree(HOOKS, dest_hooks)
    (tmp_path / "tools").mkdir()  # sibling tools/ dir present but empty

    payload = {
        "session_id": "s",
        "cwd": str(tmp_path),
        "transcript_path": str(tmp_path / "agent-x.jsonl"),
    }
    proc = subprocess.run(
        [sys.executable, str(dest_hooks / "subagent-tracker.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
