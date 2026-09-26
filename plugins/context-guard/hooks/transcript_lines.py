"""transcript_lines.py — pure predicates over one decoded JSONL transcript line.

Separate concern from transcript_scan.py (which only knows how to walk bytes
in bounded chunks) and from stop-context-guard.py's checkpoint policy: this
module knows what a transcript line's JSON *means* — whether it carries a
positive assistant usage record, or a tool_use content block — but does no
I/O and holds no state.
"""

import json


def usage_from_line(line: str):
    """Parse one JSONL line; return (ctx, model) if it carries a positive
    assistant usage record, else None."""
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


def line_has_tool_use(line: str) -> bool:
    """True if a JSONL transcript line's assistant message content carries a
    tool_use block."""
    line = line.strip()
    if not line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in content
    )
