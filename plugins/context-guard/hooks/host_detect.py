"""host_detect.py — identify which agent host produced a transcript file.

Separate concern from transcript_scan.py (bytes) and transcript_lines.py /
transcript_codex.py (line semantics): this module answers exactly one
question — is the first record of this JSONL transcript a Codex
`session_meta` record, or anything else (a Claude Code transcript, an
unreadable file, an empty file, a malformed line)? Peeking one line is cheap
and does not require the tail scan the caller runs afterward.

Ported pattern: Cortex mcp_server/infrastructure/transcript_activity.py
(ADR-1081, PR cdeust/Cortex#610) peeks the first record the same way and
dispatches on `type == "session_meta"`. Confirmed against a real Codex
rollout: `~/.codex/sessions/2026/09/26/rollout-2026-09-26T01-14-28-*.jsonl`
line 0 is `{"type": "session_meta", "payload": {...}}`; Claude Code
transcript lines never carry a bare top-level "type": "session_meta" (they
are `{"type": "assistant"/"user"/..., "message": {...}}`).
"""

import json


def detect_host(transcript_path: str) -> str:
    """Return "codex" when `transcript_path`'s first line is a Codex
    `session_meta` record, else "claude" (the pre-existing default path).

    Precondition:  transcript_path is a path string or None.
    Postcondition: fail-safe — any missing file, empty file, unreadable
                   first line, or non-session_meta first record returns
                   "claude", never raises.
    """
    if not transcript_path:
        return "claude"
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return "claude"
    first = first.strip()
    if not first:
        return "claude"
    try:
        obj = json.loads(first)
    except json.JSONDecodeError:
        return "claude"
    if isinstance(obj, dict) and obj.get("type") == "session_meta":
        return "codex"
    return "claude"
