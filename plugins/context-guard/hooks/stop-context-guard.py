#!/usr/bin/env python3
"""stop-context-guard.py — Stop hook: enforce the token-budget checkpoint protocol.

Contract:

  | Model              | Checkpoint (warn) | Hard cap (block) | Why                                  |
  |--------------------|-------------------|------------------|--------------------------------------|
  | Fable 5 / Mythos   | ~120K             | 160K             | 2x carrying rent + 2x resume penalty |
  | Opus 4.x           | ~180K             | 200K             | cost discipline (window is 1M)       |
  | Sonnet 4.6         | ~180K             | 200K             | cost discipline (window is 1M)       |
  | Haiku 4.5          | ~120K             | 170K             | 200K IS the window; leave ~30K of    |
  |                    |                   |                  | headroom for the checkpoint turn     |

  Thresholds are loaded from ~/.claude/ctxguard-thresholds.json (shared with
  the statusline so both layers stay on par by construction); the table above
  is the embedded fallback when the config is absent or malformed. First
  substring match against the lowercased model id wins.

  Context tokens are measured exactly as Claude Code's `used_percentage`:
      input_tokens + cache_creation_input_tokens + cache_read_input_tokens
  read from the most recent assistant turn in the transcript.

  Precondition:  invoked as a Stop hook with JSON on stdin containing
                 session_id, transcript_path, cwd, stop_hook_active.
  Postcondition: below WARN -> exit 0, no output, no side effects; WARN..HARD
                 -> write the mechanical checkpoint stub and block the stop
                 exactly once as a reflection pause (session resumes); >=HARD
                 -> write the stub and block exactly once with the
                 checkpoint-then-clear procedure. Full level/action table and
                 the memory-writer handoff: see README.md "The Stop guard".

  Re-entrancy / loop safety: stop_hook_active true -> exit 0 (already inside a
  forced continuation); a per-session state file records the highest level
  already fired, so each level fires at most once per session and the hard
  block cannot loop.

  Non-fatal by construction: any parse/IO error exits 0 (a Stop hook must
  never wedge the session). The statusline already provides the passive
  visual warning; this hook is the active enforcement layer.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

# Five sibling modules in this same hooks/ directory split the concerns this
# file used to carry alone: checkpoint_protocol (protocol text), checkpoint_stub
# (stub-markdown render), thresholds (config-table lookup), transcript_lines
# (line-content predicates), transcript_scan (chunked-line walk). Named
# failure mode: a manual install copied only this script. A Stop hook must
# never fail hard, so degrade to inert rather than erroring every stop.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import checkpoint_protocol
    import checkpoint_stub
    import thresholds as thresholds_mod
    import transcript_lines
    import transcript_scan
except ImportError:
    sys.exit(0)

# --- Thresholds (tokens) -----------------------------------------------------
# Single source of truth shared with statusline-command.sh. First substring
# match against the lowercased model id wins; "default" applies otherwise.
# Table lookup itself lives in the sibling thresholds module; CONFIG_PATH and
# FALLBACK_THRESHOLDS stay here (not in that module) so tests can monkeypatch
# them as guard's own module attributes.
CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "ctxguard-thresholds.json"
)
FALLBACK_THRESHOLDS = {
    "models": [
        {"match": "fable",  "warn": 120_000, "hard": 160_000},
        {"match": "mythos", "warn": 120_000, "hard": 160_000},
        {"match": "haiku",  "warn": 120_000, "hard": 170_000},
        {"match": "sonnet", "warn": 180_000, "hard": 200_000},
        {"match": "opus",   "warn": 180_000, "hard": 200_000},
    ],
    "default": {"warn": 180_000, "hard": 200_000},
}


def _thresholds(model_id: str):
    """Return (warn, hard) for the model. Non-fatal: any config problem or
    malformed matched/default entry falls back to FALLBACK_THRESHOLDS["default"].

    Precondition:  model_id is a string or None.
    Postcondition: warn < hard, both positive ints.
    """
    table = thresholds_mod.load_table(CONFIG_PATH, FALLBACK_THRESHOLDS)
    entry = thresholds_mod.matching_entry(table, model_id, FALLBACK_THRESHOLDS["default"])
    fallback = FALLBACK_THRESHOLDS["default"]
    try:
        warn, hard = int(entry["warn"]), int(entry["hard"])
    except (KeyError, TypeError, ValueError):
        return fallback["warn"], fallback["hard"]
    if 0 < warn < hard:
        return warn, hard
    return fallback["warn"], fallback["hard"]


STATE_DIR = "/tmp"
LEVEL_ORDER = {"none": 0, "warn": 1, "hard": 2}

# --- Bounded chunked-scan parameters (see transcript_scan.py for the walk) --
# Transcripts grow to 100MB-1GB; readlines() is O(file size) in memory, so we
# scan in bounded chunks instead. TAIL_CHUNK=64KiB, measured on a real 24.5MB
# transcript (~/.claude/projects/-Users-cdeust-Developments-Cortex/<uuid>.jsonl):
# last usage record 7,591 bytes from EOF; usage lines min=1,016/median=1,729/
# max=32,769 bytes -- one 64KiB chunk covers both ~2-8.6x over, so one chunk
# suffices in practice. TAIL_MAX_BYTES is a hard safety bound, not a tuning knob.
TAIL_CHUNK = 64 * 1024          # 65536 bytes
TAIL_MAX_BYTES = 4 * 1024 * 1024  # cap total bytes scanned at 4 MiB


def _exit(payload=None):
    """Emit optional JSON to stdout and exit 0. A Stop hook must not fail hard."""
    if payload:
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def _usage_from_line(line: str):
    """Parse one JSONL line; return (ctx, model) if it carries a positive
    assistant usage record, else None. Pure, no I/O. Delegates to the
    sibling transcript_lines module (shared line-content predicates)."""
    return transcript_lines.usage_from_line(line)


def _scan_budget() -> transcript_scan.ScanBudget:
    """Build a ScanBudget from this module's own TAIL_CHUNK/TAIL_MAX_BYTES at
    call time (not import time), so tests that monkeypatch those module
    attributes still control the scan size."""
    return transcript_scan.ScanBudget(TAIL_CHUNK, TAIL_MAX_BYTES)


def _read_last_usage(transcript_path: str):
    """Return (context_tokens, model_id) from the most recent assistant usage,
    or (None, None) if unavailable.

    Precondition:  transcript_path is a path string (or None).
    Postcondition: returns (ctx, model) for the last line carrying a positive
                   usage record within the scanned tail, else (None, None) —
                   fails closed whether the scan hit TAIL_MAX_BYTES or reached
                   the start of the file cleanly; on any missing/unreadable
                   file or non-str path, also (None, None).

    Bounded reverse scan via transcript_scan.iter_lines_backward: peak memory
    is O(TAIL_CHUNK), not O(file size).
    """
    try:
        size = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        return None, None
    if size == 0:
        return None, None

    try:
        with open(transcript_path, "rb") as fh:
            lines = transcript_scan.iter_lines_backward(
                fh, size, transcript_scan.ScanResult(), _scan_budget())
            hit = transcript_scan.first_match(lines, _usage_from_line)
    except (OSError, TypeError, ValueError):
        return None, None
    return hit if hit is not None else (None, None)


def _subagent_summary(session_id: str):
    """Read the per-session subagent aggregate maintained by subagent-tracker.py.

    Returns (count, tokens, cost_usd) for this session's subagent spend, or
    (0, 0, 0.0) if no aggregate exists. This is the cumulative spend the main
    thread's context-window measurement structurally cannot see — surfaced in
    the checkpoint message and stub so the operator sees true session cost.

    Non-fatal: any read/parse problem returns zeros.
    """
    path = os.path.join(STATE_DIR, f"zetetic-subagents-{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            totals = (json.load(fh) or {}).get("totals") or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return 0, 0, 0.0
    count = int(totals.get("count", 0) or 0)
    tokens = (int(totals.get("input_tokens", 0) or 0)
              + int(totals.get("output_tokens", 0) or 0)
              + int(totals.get("cache_tokens", 0) or 0))
    cost = float(totals.get("cost_usd", 0.0) or 0.0)
    return count, tokens, cost


def _subagent_line(session_id: str) -> str:
    """One-line subagent-spend note for checkpoint messages, or '' if none."""
    count, tokens, cost = _subagent_summary(session_id)
    if count <= 0:
        return ""
    return (f"\nSubagent spend this session (not in the main-thread context "
            f"measure above): {count} runs, ~{tokens:,} billed tokens, "
            f"~${cost:.2f}.")


def _normalize_since_offset(since_offset: int, size: int) -> int:
    """A stale offset (from a rotated/replaced transcript) cannot be trusted
    as "caught up" -- rescan from 0 instead. Clamp negative input to 0."""
    since_offset = max(0, since_offset or 0)
    return 0 if since_offset > size else since_offset


def _has_activity_since(transcript_path: str, since_offset: int) -> bool:
    """True if the transcript contains at least one tool_use content block
    at or after byte `since_offset`.

    Fail-open: if the scan cap (TAIL_MAX_BYTES) is hit before reaching EOF,
    or the file cannot be read/parsed at all, return True (preserve the
    previous always-fire behavior rather than risk silently dropping a
    checkpoint we could not fully verify is safe to skip). False only after
    a clean scan to EOF within the byte cap found none. since_offset larger
    than the current file size means the offset is stale (the transcript was
    replaced/rotated between fires) and cannot be trusted as "caught up" --
    rescan from 0 instead of concluding there is nothing new.

    Precondition:  transcript_path is a path string (or None); since_offset
                   is a non-negative int (0 scans the whole file).
    Postcondition: True if a tool_use block was found, the scan cap was hit
                   before EOF, or the file could not be read/parsed at all
                   (fail-open); False only after a clean scan to EOF within
                   the byte cap found none.
    """
    try:
        size = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        return True  # can't tell -> don't silently skip

    since_offset = _normalize_since_offset(since_offset, size)
    if since_offset == size:
        return False  # nothing appended since last fire / session start

    try:
        with open(transcript_path, "rb") as fh:
            result = transcript_scan.ScanResult()
            rng = transcript_scan.ByteRange(since_offset, size)
            lines = transcript_scan.iter_lines_forward(fh, rng, result, _scan_budget())
            found = transcript_scan.first_match(
                lines, lambda line: True if _line_has_tool_use(line) else None)
    except (OSError, TypeError, ValueError):
        return True

    if found:
        return True
    return result.reason == "cap"  # clean EOF with no hit -> False


def _line_has_tool_use(line: str) -> bool:
    """True if a JSONL transcript line's assistant message content carries a
    tool_use block. Pure, no I/O. Delegates to the sibling transcript_lines
    module (shared line-content predicates)."""
    return transcript_lines.line_has_tool_use(line)


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "-c", "core.useBuiltinFSMonitor=false", *args],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return out.stdout.strip()
    except OSError:
        return ""


@dataclass
class FireEvent:
    """The fields a single Stop-guard fire shares across the stub, state, and
    checkpoint-reason builders, so they take one object instead of several
    separate positional parameters. `stub_path` starts empty and is filled
    in once `_write_stub` has run."""

    session_id: str
    cwd: str
    ctx: int
    model_id: str
    level: str
    stub_path: str = ""


def _sub_state_line(session_id: str) -> str:
    """The stub's "subagent spend" bullet, or "" if there was none."""
    sub_count, sub_tokens, sub_cost = _subagent_summary(session_id)
    if sub_count <= 0:
        return ""
    return (f"- subagent spend: {sub_count} runs · ~{sub_tokens:,} billed tokens · "
            f"~${sub_cost:.2f} (separate from the context tokens above)\n")


def _write_stub(ev: FireEvent) -> str:
    """Capture mechanical session state for free. Returns the stub path, or
    "" if the checkpoints directory or either file could not be written.
    Rendering itself is pure and lives in the sibling checkpoint_stub module;
    this function owns the I/O (git subprocess calls, subagent-aggregate
    read, file writes)."""
    root = os.path.join(os.path.expanduser("~"), ".claude", "memories", "checkpoints")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        return ""

    git_info = checkpoint_stub.GitInfo(
        branch=_git(ev.cwd, "symbolic-ref", "--short", "HEAD") or _git(ev.cwd, "rev-parse", "--short", "HEAD"),
        last_commit=_git(ev.cwd, "log", "-1", "--oneline"),
        modified=_git(ev.cwd, "status", "--porcelain"),
    )
    stub = checkpoint_stub.render(ev, git_info, _sub_state_line(ev.session_id))

    per_session = os.path.join(root, f"{ev.session_id}.md")
    latest = os.path.join(root, "latest.md")
    try:
        with open(per_session, "w", encoding="utf-8") as fh:
            fh.write(stub)
        with open(latest, "w", encoding="utf-8") as fh:
            fh.write(stub)
    except OSError:
        return ""
    return per_session


def _load_state(session_id: str) -> dict:
    """Return the persisted state dict for this session, defaulting to
    {"level": "none"} on any missing/malformed/legacy file. Additive schema:
    level (str), initial_ctx/last_fire_ctx (int), last_fire_offset (int,
    transcript byte offset at the most recent real fire -- the baseline for
    the next _has_activity_since check), fired_at (ISO str). Telemetry
    fields are for future replay/ablation only; they gate nothing yet."""
    path = os.path.join(STATE_DIR, f"zetetic-ctxguard-{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "level" in data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"level": "none"}


def _save_state(session_id: str, state: dict) -> None:
    path = os.path.join(STATE_DIR, f"zetetic-ctxguard-{session_id}.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        return


def _parse_stop_input():
    """Read and validate the Stop-hook stdin payload.

    Postcondition: returns the parsed dict when it is JSON, a dict, and not
    already inside a forced continuation; else None (unparsable JSON, a
    non-dict JSON value, or stop_hook_active is true) -- every None case
    means "the caller should _exit() immediately".
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("stop_hook_active"):
        return None
    return data


def _classify_level(ctx: int, warn: int, hard: int):
    """Return "hard", "warn", or None (below warn -> nothing to do)."""
    if ctx >= hard:
        return "hard"
    if ctx >= warn:
        return "warn"
    return None


def _next_scan_offset(state: dict, transcript_path: str) -> int:
    """Byte offset to resume the activity scan from: the last recorded fire
    offset IF it was recorded against this same transcript_path, else 0 (a
    stale offset from a different/rotated transcript cannot be trusted)."""
    if state.get("transcript_path") == transcript_path:
        return state.get("last_fire_offset", 0)
    return 0


def _should_fire(state: dict, level: str, transcript_path: str) -> bool:
    """True if this Stop should actually fire: the level crosses UP into a
    not-yet-fired level for this session, AND real activity (a tool_use
    block) happened since the last fire or session start."""
    prev = state.get("level", "none")
    if LEVEL_ORDER[level] <= LEVEL_ORDER[prev]:
        return False
    since_offset = _next_scan_offset(state, transcript_path)
    return _has_activity_since(transcript_path, since_offset)


def _next_state(state: dict, ev: FireEvent, transcript_path: str) -> dict:
    """The state dict to persist after firing at `ev.level`."""
    try:
        new_offset = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        new_offset = _next_scan_offset(state, transcript_path)
    return {
        "level": ev.level,
        "initial_ctx": state.get("initial_ctx") or ev.ctx,
        "last_fire_ctx": ev.ctx,
        "last_fire_offset": new_offset,
        "transcript_path": transcript_path,
        "fired_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@dataclass(frozen=True)
class CheckpointReasons:
    """The threshold values plus the (generic or scoped) reason-builder pair
    a fire payload needs — bundled so `_fire_payload` takes one object
    instead of four separate parameters."""

    warn: int
    hard: int
    warn_reason: object
    block_reason: object


def _checkpoint_reasons(cwd: str, warn: int, hard: int) -> CheckpointReasons:
    """Build a CheckpointReasons, choosing the generic or scoped wording at
    runtime by whether a scoped memory layer is detected for `cwd`. Runtime
    detection means the scoped wording is emitted only when the layer is
    actually installed; everyone else gets the stub-file protocol, which
    references vanilla Claude Code tools only."""
    if checkpoint_protocol.detect_memory_tool(cwd) is not None:
        return CheckpointReasons(warn, hard, checkpoint_protocol.warn_reason_scoped,
                                  checkpoint_protocol.block_reason_scoped)
    return CheckpointReasons(warn, hard, checkpoint_protocol.warn_reason,
                              checkpoint_protocol.block_reason)


def _fire_payload(ev: FireEvent, reasons: CheckpointReasons, sub_line: str) -> dict:
    """Build the hook's stdout JSON payload for this fire event."""
    if ev.level == "hard":
        return {
            "decision": "block",
            "reason": reasons.block_reason(ev.ctx, ev.stub_path, reasons.hard) + sub_line,
            "systemMessage": (
                f"[context-guard] {ev.ctx:,} tokens ≥ {reasons.hard:,} soft cap "
                f"({ev.model_id or 'model'}) — forcing a checkpoint before the "
                f"session continues." + sub_line
            ),
        }
    return {
        "decision": "block",
        "reason": reasons.warn_reason(ev.ctx, ev.stub_path, reasons.warn, reasons.hard) + sub_line,
        "systemMessage": (
            f"[context-guard] {ev.ctx:,} tokens ≥ {reasons.warn:,} checkpoint threshold "
            f"({(ev.model_id or 'model')}) — spawning the memory-writer subagent to "
            f"persist the semantic checkpoint, then the session continues. "
            f"Mechanical stub: {ev.stub_path or 'n/a'}. Hard stop at {reasons.hard:,}." + sub_line
        ),
    }


def main():
    data = _parse_stop_input()
    if data is None:
        _exit()

    session_id = data.get("session_id") or "unknown"
    transcript_path = data.get("transcript_path")
    cwd = data.get("cwd") or os.getcwd()

    ctx, model_id = _read_last_usage(transcript_path)
    if ctx is None:
        _exit()

    warn, hard = _thresholds(model_id)
    level = _classify_level(ctx, warn, hard)
    if level is None:
        _exit()

    state = _load_state(session_id)
    if not _should_fire(state, level, transcript_path):
        _exit()

    ev = FireEvent(session_id, cwd, ctx, model_id, level)
    ev.stub_path = _write_stub(ev)
    _save_state(session_id, _next_state(state, ev, transcript_path))

    reasons = _checkpoint_reasons(cwd, warn, hard)
    _exit(_fire_payload(ev, reasons, _subagent_line(session_id)))


if __name__ == "__main__":
    main()
