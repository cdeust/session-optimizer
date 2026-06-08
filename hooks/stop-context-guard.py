#!/usr/bin/env python3
"""stop-context-guard.py — Stop hook: enforce the token-budget checkpoint protocol.

Contract (source of truth: agents/orchestrator.md <token-budget>):

  | Model            | Checkpoint threshold | Session soft cap |
  |------------------|----------------------|------------------|
  | Opus 4.8         | ~180K                | 200K             |
  | Sonnet 4.6       | ~180K                | 200K             |
  | Haiku 4.5        | ~120K                | 200K (= window)  |

  Context tokens are measured exactly as Claude Code's `used_percentage`:
      input_tokens + cache_creation_input_tokens + cache_read_input_tokens
  read from the most recent assistant turn in the transcript.

  Precondition:  invoked as a Stop hook with JSON on stdin containing
                 session_id, transcript_path, cwd, stop_hook_active.
  Postcondition:
    - below WARN            -> exit 0, no output, no side effects.
    - WARN <= ctx < HARD    -> write a free mechanical checkpoint stub; emit a
                               one-time non-blocking systemMessage. Never blocks.
    - ctx >= HARD           -> write the stub AND block the stop exactly once,
                               injecting the checkpoint-finalize procedure so the
                               model persists a scoped semantic checkpoint and
                               signals the user to clear + resume via recall.

  Re-entrancy / loop safety:
    - if stop_hook_active is true, exit 0 (we are already in a forced continuation).
    - per-session state file records the highest level already fired; each level
      fires at most once per session, so the hard block cannot loop.

  Non-fatal by construction: any parse/IO error exits 0 (a Stop hook must never
  wedge the session). The statusline already provides the passive visual warning;
  this hook is the active enforcement layer.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# --- Thresholds (tokens) -----------------------------------------------------
HARD_CAP = 200_000
WARN_DEFAULT = 180_000   # Opus 4.8 / Sonnet 4.6
WARN_HAIKU = 120_000     # Haiku 4.5 (200K context window — tighter checkpoint)

STATE_DIR = "/tmp"
LEVEL_ORDER = {"none": 0, "warn": 1, "hard": 2}


def _exit(payload=None):
    """Emit optional JSON to stdout and exit 0. A Stop hook must not fail hard."""
    if payload:
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def _warn_threshold(model_id: str) -> int:
    return WARN_HAIKU if "haiku" in (model_id or "").lower() else WARN_DEFAULT


def _read_last_usage(transcript_path: str):
    """Return (context_tokens, model_id) from the most recent assistant usage,
    or (None, None) if unavailable. Scans from the end for efficiency."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, TypeError):
        return None, None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        ctx = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("cache_creation_input_tokens", 0) or 0)
            + int(usage.get("cache_read_input_tokens", 0) or 0)
        )
        if ctx <= 0:
            continue
        return ctx, msg.get("model") or obj.get("model")
    return None, None


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "-c", "core.useBuiltinFSMonitor=false", *args],
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _write_stub(session_id: str, cwd: str, ctx: int, model_id: str, level: str) -> str:
    """Capture mechanical session state for free. Returns the stub path (or '')."""
    root = os.path.join(os.path.expanduser("~"), ".claude", "memories", "checkpoints")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        return ""

    branch = _git(cwd, "symbolic-ref", "--short", "HEAD") or _git(cwd, "rev-parse", "--short", "HEAD")
    last_commit = _git(cwd, "log", "-1", "--oneline")
    modified = _git(cwd, "status", "--porcelain")
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stub = f"""## Auto-checkpoint stub ({level}) — {iso}

> Mechanical state captured by stop-context-guard at {ctx:,} context tokens
> (model: {model_id or 'unknown'}). The semantic checkpoint (task / decisions /
> next action) must be filled in by the agent — this stub only records what the
> hook can know without spending model tokens.

### Session
- session_id: {session_id}
- model: {model_id or 'unknown'}
- context tokens at trigger: {ctx:,}
- working dir: {cwd}

### Git state
- branch: {branch or '(unknown)'}
- last commit: {last_commit or '(none)'}
- modified files:
{os.linesep.join('  - ' + l for l in modified.splitlines()) if modified else '  (clean)'}

### Task / Decisions / Next action
<to be filled by the agent on checkpoint>
"""
    per_session = os.path.join(root, f"{session_id}.md")
    latest = os.path.join(root, "latest.md")
    try:
        with open(per_session, "w", encoding="utf-8") as fh:
            fh.write(stub)
        with open(latest, "w", encoding="utf-8") as fh:
            fh.write(stub)
    except OSError:
        return ""
    return per_session


def _load_level(session_id: str) -> str:
    path = os.path.join(STATE_DIR, f"zetetic-ctxguard-{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("level", "none")
    except (OSError, json.JSONDecodeError):
        return "none"


def _save_level(session_id: str, level: str) -> None:
    path = os.path.join(STATE_DIR, f"zetetic-ctxguard-{session_id}.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"level": level}, fh)
    except OSError:
        pass


def _block_reason(ctx: int, stub_path: str) -> str:
    return (
        f"⚠ CONTEXT SOFT CAP REACHED — {ctx:,} input tokens (≥ 200K session budget).\n"
        f"Continuing in this session now risks context poisoning, quota burn, and "
        f"escalating per-turn cost. Execute the checkpoint protocol before yielding:\n\n"
        f"1. Write your scoped semantic checkpoint with the memory tool, e.g.:\n"
        f"   MEMORY_AGENT_ID=<your-scope> tools/memory-tool.sh create "
        f"/memories/<your-scope>/checkpoint.md \"<task · completed · in-progress · "
        f"remaining · key decisions · files modified · exact next action>\"\n"
        f"   (Mechanical state was already captured for free at: {stub_path or 'n/a'} — "
        f"copy its Git state in; you only need to add the semantic fields.)\n"
        f"2. If important decisions are not yet durable, persist them now "
        f"(cortex:remember, scoped to your agent_topic).\n"
        f"3. End your response with exactly:\n"
        f"   CHECKPOINT — context cleared.\n"
        f"   Resume from: /memories/<your-scope>/checkpoint.md\n"
        f"   Next action: <exact first thing to do on restart>\n"
        f"Then instruct the user to run /clear and resume — the next session must "
        f"recall the checkpoint before touching any file or tool.\n"
        f"Do NOT start new substantive work in this session."
    )


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _exit()

    # Loop guard: if we already forced a continuation, do not act again.
    if data.get("stop_hook_active"):
        _exit()

    session_id = data.get("session_id") or "unknown"
    transcript_path = data.get("transcript_path")
    cwd = data.get("cwd") or os.getcwd()

    ctx, model_id = _read_last_usage(transcript_path)
    if ctx is None:
        _exit()

    warn = _warn_threshold(model_id)
    if ctx >= HARD_CAP:
        level = "hard"
    elif ctx >= warn:
        level = "warn"
    else:
        _exit()

    prev = _load_level(session_id)
    # Only act when crossing UP into a not-yet-fired level.
    if LEVEL_ORDER[level] <= LEVEL_ORDER[prev]:
        _exit()

    stub_path = _write_stub(session_id, cwd, ctx, model_id, level)
    _save_level(session_id, level)

    if level == "hard":
        _exit({
            "decision": "block",
            "reason": _block_reason(ctx, stub_path),
            "systemMessage": (
                f"[context-guard] {ctx:,} tokens ≥ 200K soft cap — forcing a "
                f"checkpoint before the session continues."
            ),
        })
    else:  # warn — non-blocking nudge, no model tokens spent
        _exit({
            "systemMessage": (
                f"[context-guard] {ctx:,} tokens ≥ {warn:,} checkpoint threshold "
                f"({(model_id or 'model')}). Mechanical state saved to {stub_path or 'n/a'}. "
                f"Plan to checkpoint and start a fresh session soon; hard stop at 200K."
            )
        })


if __name__ == "__main__":
    main()
