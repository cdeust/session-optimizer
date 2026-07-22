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
  Postcondition:
    - below WARN            -> exit 0, no output, no side effects.
    - WARN <= ctx < HARD    -> write a free mechanical checkpoint stub (summary
                               schema) AND block the stop exactly once: the model
                               is instructed to spawn the `memory-writer` subagent
                               (a normal Claude Code agent, shipped in this
                               plugin under agents/) that persists the model's
                               distilled summary — by default into the stub file
                               itself (vanilla Claude Code, no extra tooling);
                               when a scoped memory layer is detected at runtime
                               (checkpoint_protocol.detect_memory_tool), into
                               that store instead — then RESUME the user's task
                               in-session (reflection, not a stop).
    - ctx >= HARD           -> write the stub AND block the stop exactly once,
                               injecting the checkpoint-finalize procedure so the
                               model persists the semantic checkpoint and signals
                               the user to clear + resume via the checkpoint file.
                               Because WARN already ran the reflection, the hard
                               block is normally a formality, not a scramble.

  Checkpoint schema (summary schema): goals / file references (paths + line
  ranges) / errors and fixes / current state / next steps, <=500 words total,
  any quoted tool output clipped to 2,000 chars. Resume contract: read the
  checkpoint + at most ONE targeted search; do NOT re-read files the checkpoint
  already summarizes.

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

# The protocol text (generic vs scoped-memory-layer variants) lives in the
# sibling checkpoint_protocol module, shipped in the same hooks/ directory.
# Named failure mode: a manual install copied only this script. A Stop hook
# must never fail hard, so degrade to inert rather than erroring every stop.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import checkpoint_protocol
except ImportError:
    sys.exit(0)

# --- Thresholds (tokens) -----------------------------------------------------
# Single source of truth shared with statusline-command.sh. First substring
# match against the lowercased model id wins; "default" applies otherwise.
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
    """Return (warn, hard) for the model. Non-fatal: any config problem falls
    back to FALLBACK_THRESHOLDS; any malformed entry is skipped.

    Precondition:  model_id is a string or None.
    Postcondition: warn < hard, both positive ints.
    """
    table = FALLBACK_THRESHOLDS
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and isinstance(loaded.get("models"), list):
            table = loaded
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    mid = (model_id or "").lower()
    chosen = table.get("default") or FALLBACK_THRESHOLDS["default"]
    for entry in table.get("models", []):
        try:
            if entry["match"] in mid:
                chosen = entry
                break
        except (KeyError, TypeError):
            continue
    try:
        warn, hard = int(chosen["warn"]), int(chosen["hard"])
        if 0 < warn < hard:
            return warn, hard
    except (KeyError, TypeError, ValueError):
        pass
    d = FALLBACK_THRESHOLDS["default"]
    return d["warn"], d["hard"]


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


def _subagent_summary(session_id: str):
    """Read the per-session subagent aggregate maintained by subagent-tracker.py.

    Returns (count, tokens, cost_usd) for this session's subagent spend, or
    (0, 0, 0.0) if no aggregate exists. This is the cumulative spend the main
    thread's context-window measurement structurally cannot see — surfaced in
    the checkpoint message and stub so the operator sees true session cost.

    Non-fatal: any read/parse problem returns zeros.
    """
    path = os.path.join("/tmp", f"zetetic-subagents-{session_id}.json")
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
    sub_count, sub_tokens, sub_cost = _subagent_summary(session_id)
    sub_state = (
        f"- subagent spend: {sub_count} runs · ~{sub_tokens:,} billed tokens · "
        f"~${sub_cost:.2f} (separate from the context tokens above)\n"
        if sub_count > 0 else ""
    )

    stub = f"""---
description: "Auto-checkpoint ({level}) at {ctx:,} tokens — session {session_id[:8]} on {branch or 'unknown branch'}"
---
## Auto-checkpoint stub ({level}) — {iso}

> Mechanical state captured for free by stop-context-guard at {ctx:,} context
> tokens (model: {model_id or 'unknown'}). The semantic fields below follow the
> summary schema. Budget: <=500 words total across all sections; clip any
> quoted tool output to 2,000 chars.

### Goals
<to be filled: what this session is trying to achieve, in priority order>

### File references
(paths + line ranges the resumed session will need; seeded from git status —
replace with the load-bearing files and add `path:start-end` line ranges)
{os.linesep.join('- ' + l.strip() for l in modified.splitlines()) if modified else '- (working tree clean)'}

### Errors and fixes
<to be filled: each error hit this session and how it was fixed or worked around>

### Current state
- session_id: {session_id}
- model: {model_id or 'unknown'} · context tokens at trigger: {ctx:,}
- working dir: {cwd}
- branch: {branch or '(unknown)'} · last commit: {last_commit or '(none)'}
{sub_state}<to be filled: one paragraph — where the work stands right now>

### Next steps
<to be filled: exact ordered actions for the resumed session; first one must be
executable without re-deriving anything>

### Resume contract
Read this checkpoint + at most ONE targeted search. Do NOT re-read files this
checkpoint already summarizes — trust the file references above and verify with
targeted Reads only when editing.
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

    warn, hard = _thresholds(model_id)
    if ctx >= hard:
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
    sub_line = _subagent_line(session_id)

    # Runtime detection: the scoped-memory-layer wording is emitted only when
    # the layer is actually installed; everyone else gets the stub-file
    # protocol, which references vanilla Claude Code tools only.
    scoped = checkpoint_protocol.detect_memory_tool(cwd) is not None
    if scoped:
        warn_reason = checkpoint_protocol.warn_reason_scoped
        block_reason = checkpoint_protocol.block_reason_scoped
    else:
        warn_reason = checkpoint_protocol.warn_reason
        block_reason = checkpoint_protocol.block_reason

    if level == "hard":
        _exit({
            "decision": "block",
            "reason": block_reason(ctx, stub_path, hard) + sub_line,
            "systemMessage": (
                f"[context-guard] {ctx:,} tokens ≥ {hard:,} soft cap "
                f"({model_id or 'model'}) — forcing a checkpoint before the "
                f"session continues." + sub_line
            ),
        })
    else:  # warn — one-time reflection block: persist memory while headroom remains
        _exit({
            "decision": "block",
            "reason": warn_reason(ctx, stub_path, warn, hard) + sub_line,
            "systemMessage": (
                f"[context-guard] {ctx:,} tokens ≥ {warn:,} checkpoint threshold "
                f"({(model_id or 'model')}) — spawning the memory-writer subagent to "
                f"persist the semantic checkpoint, then the session continues. "
                f"Mechanical stub: {stub_path or 'n/a'}. Hard stop at {hard:,}." + sub_line
            ),
        })


if __name__ == "__main__":
    main()
