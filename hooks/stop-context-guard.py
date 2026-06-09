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

# --- Bounded reverse-tail read parameters ------------------------------------
# Claude Code transcripts grow to 100MB–1GB in long sessions, and this hook
# fires on every Stop. Reading the whole file (readlines) is O(file size) in
# memory; we instead seek to the tail and scan backward.
#
# TAIL_CHUNK = 64 KiB (a power-of-two block multiple). Justification, measured
# on a real 24.5MB transcript at
#   ~/.claude/projects/-Users-cdeust-Developments-Cortex/<uuid>.jsonl :
#   - the last assistant `usage` record was 7,591 bytes from EOF;
#   - usage JSONL lines were min=1,016 / median=1,729 / max=32,769 bytes.
# A single 64 KiB tail read covers the last-usage offset ~8.6x over and the
# largest single usage line ~2x over, so one chunk suffices in practice.
# The usage record is rewritten on every assistant turn, so it is always near
# the end. Chunk-stepping with TAIL_MAX_BYTES is a hard safety bound, not a
# tuning knob.
TAIL_CHUNK = 64 * 1024          # 65536 bytes
TAIL_MAX_BYTES = 4 * 1024 * 1024  # cap total bytes scanned at 4 MiB


def _exit(payload=None):
    """Emit optional JSON to stdout and exit 0. A Stop hook must not fail hard."""
    if payload:
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def _warn_threshold(model_id: str) -> int:
    return WARN_HAIKU if "haiku" in (model_id or "").lower() else WARN_DEFAULT


def _usage_from_line(line: str):
    """Parse one JSONL line; return (ctx, model) if it carries a positive
    assistant usage record, else None. Pure, no I/O."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    msg = obj.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    ctx = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
    )
    if ctx <= 0:
        return None
    return ctx, msg.get("model") or obj.get("model")


def _read_last_usage(transcript_path: str):
    """Return (context_tokens, model_id) from the most recent assistant usage,
    or (None, None) if unavailable.

    Precondition:  transcript_path is a path string (or None).
    Postcondition: returns (ctx, model) for the last line carrying a positive
                   usage record within the scanned tail, else (None, None).
                   On any missing/unreadable file or non-str path, returns
                   (None, None) — identical to the previous readlines() contract.

    Bounded reverse-tail read: seeks to max(0, size - TAIL_CHUNK) and scans the
    tail backward, stepping back chunk by chunk until a usage record is found
    or TAIL_MAX_BYTES have been scanned. Peak memory is O(TAIL_CHUNK), not
    O(file size). Decoded as UTF-8 with errors='replace' so a chunk boundary
    that splits a multi-byte sequence cannot raise.
    """
    try:
        size = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        return None, None
    if size == 0:
        return None, None

    try:
        fh = open(transcript_path, "rb")
    except (OSError, TypeError, ValueError):
        return None, None

    try:
        carry = ""          # bytes of a line split across the chunk boundary
        pos = size          # exclusive high-water mark of bytes not yet read
        scanned = 0
        while pos > 0 and scanned < TAIL_MAX_BYTES:
            read_size = min(TAIL_CHUNK, pos)
            pos -= read_size
            scanned += read_size
            fh.seek(pos)
            chunk = fh.read(read_size).decode("utf-8", errors="replace")
            # Prepend; carry holds the partial line that started inside this chunk.
            buf = chunk + carry
            # If we have not reached the start of the file, the first segment of
            # buf is a partial line (its true beginning is in an earlier chunk).
            # Hold it back as carry and scan only the complete lines after it.
            if pos > 0:
                nl = buf.find("\n")
                if nl == -1:
                    # No newline in the whole window yet: keep accumulating,
                    # but bound carry growth by the scan cap (handled by loop).
                    carry = buf
                    continue
                carry = buf[:nl]
                lines = buf[nl + 1:].split("\n")
            else:
                # Reached file start: buf begins at a real line boundary.
                carry = ""
                lines = buf.split("\n")
            for line in reversed(lines):
                hit = _usage_from_line(line)
                if hit is not None:
                    return hit
        # Cap reached or whole file consumed; check any final carried line.
        if carry:
            hit = _usage_from_line(carry)
            if hit is not None:
                return hit
        return None, None
    except OSError:
        return None, None
    finally:
        fh.close()


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
