"""transcript_codex.py — pure predicates over one decoded Codex rollout line.

Codex rollout JSONL records (codex-cli 0.157.1). Mirrors transcript_lines.py's
Claude predicates: no I/O, no state. Facts below are sourced, not assumed —
see plugins/context-guard/README.md "Codex support" for the full citation
table; summary:

- Record shape (`{"type": ..., "payload": {...}}`) verified against a real
  rollout: ~/.codex/sessions/2026/09/26/rollout-2026-09-26T01-14-28-*.jsonl.
- `token_usage_record.payload.usage.input_tokens` is the correct "current
  context size" measure (Claude-equivalent of `input_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`): `cached_input_
  tokens` is a SUBSET of `input_tokens`, not additive (verified arithmetic:
  input_tokens=28425, output_tokens=178, total_tokens=28603 = input+output,
  cached_input_tokens=6912 never added). `turn_token_usage` /
  `thread_token_usage` on the same record are CUMULATIVE cost counters
  across many turns / the whole thread (observed values up to 16,124,463 —
  15x the model's own 258,400-token context window) and must NOT be used as
  context size. Confirmed `usage.input_tokens` tracks compaction correctly:
  dropped from 228,390 to 36,520 across the `compacted` record in the same
  rollout (source: rollout ordinals 783/793).
- `response_item` records whose `payload.type` is `function_call` or
  `custom_tool_call` are Codex's analog of Claude's `tool_use` content block
  (verified: every tool invocation observed in the sampled rollout was one
  of these two types; `function_call_output`/`custom_tool_call_output` are
  their results, not new activity).
- Codex's `model` field is NOT present on `token_usage_record` (only on
  `turn_context`); the Stop-hook payload provides `model` directly instead
  (verified against the `stop.command.input` JSON Schema embedded in the
  `codex` binary — a required field), so `usage_from_line` below returns a
  None model and the caller (stop-context-guard.py's main()) falls back to
  the hook payload's own `model` field, never scanning for it here.
"""

import json


def usage_from_line(line: str):
    """Parse one JSONL line; return (ctx, None) if it carries a positive
    Codex `token_usage_record`, else None."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "token_usage_record":
        return None
    usage = (obj.get("payload") or {}).get("usage") or {}
    ctx = int(usage.get("input_tokens", 0) or 0)
    if ctx <= 0:
        return None
    return ctx, None


def line_has_tool_call(line: str) -> bool:
    """True if a Codex rollout line is a `response_item` whose payload type
    is a tool invocation (`function_call` or `custom_tool_call`)."""
    line = line.strip()
    if not line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    if obj.get("type") != "response_item":
        return False
    payload = obj.get("payload") or {}
    return payload.get("type") in ("function_call", "custom_tool_call")
