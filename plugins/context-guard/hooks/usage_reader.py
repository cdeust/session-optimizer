"""usage_reader.py — host-dispatching I/O for the Stop guard's two transcript
reads: "what is the current context size" and "did anything happen since the
last fire". Both need the same bounded backward/forward scan
(transcript_scan.py) but a different line predicate depending on which agent
host produced the transcript (host_detect.py). Extracted from
stop-context-guard.py so that file stays a thin dispatcher: this module owns
the open/scan/first_match I/O, the same separation `_write_stub` already
keeps for git calls and `checkpoint_stub.render` keeps for markdown.
"""

import os

import host_detect
import transcript_codex
import transcript_lines
import transcript_scan


def usage_predicate_for(transcript_path: str):
    """Codex rollouts (first record type == session_meta) use a distinct
    usage predicate; everything else (including an unreadable first line)
    keeps the pre-existing Claude predicate."""
    if host_detect.detect_host(transcript_path) == "codex":
        return transcript_codex.usage_from_line
    return transcript_lines.usage_from_line


def tool_use_predicate_for(transcript_path: str):
    """Same host dispatch as `usage_predicate_for`, for the activity gate."""
    if host_detect.detect_host(transcript_path) == "codex":
        return transcript_codex.line_has_tool_call
    return transcript_lines.line_has_tool_use


def read_last_usage(transcript_path: str, budget: transcript_scan.ScanBudget):
    """Return (context_tokens, model_id) from the most recent usage record,
    or (None, None) if unavailable. See stop-context-guard.py's
    `_read_last_usage` docstring for the full contract (bounded reverse
    scan, fail-closed on cap/EOF and on any read/parse error)."""
    try:
        size = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        return None, None
    if size == 0:
        return None, None

    predicate = usage_predicate_for(transcript_path)
    try:
        with open(transcript_path, "rb") as fh:
            lines = transcript_scan.iter_lines_backward(
                fh, size, transcript_scan.ScanResult(), budget
            )
            hit = transcript_scan.first_match(lines, predicate)
    except (OSError, TypeError, ValueError):
        return None, None
    return hit if hit is not None else (None, None)


def _normalize_since_offset(since_offset: int, size: int) -> int:
    """A stale offset (from a rotated/replaced transcript) cannot be trusted
    as "caught up" -- rescan from 0 instead. Clamp negative input to 0."""
    since_offset = max(0, since_offset or 0)
    return 0 if since_offset > size else since_offset


def has_activity_since(
    transcript_path: str, since_offset: int, budget: transcript_scan.ScanBudget
) -> bool:
    """See stop-context-guard.py's `_has_activity_since` docstring for the
    full fail-open contract; this function is its host-dispatched I/O body.

    Precondition:  transcript_path is a path string (or None); since_offset
                   is a non-negative int (0 scans the whole file).
    Postcondition: True if a tool-use-equivalent block was found, the scan
                   cap was hit before EOF, or the file could not be
                   read/parsed at all (fail-open); False only after a clean
                   scan to EOF within the byte cap found none.
    """
    try:
        size = os.stat(transcript_path).st_size
    except (OSError, TypeError, ValueError):
        return True  # can't tell -> don't silently skip

    since_offset = _normalize_since_offset(since_offset, size)
    if since_offset == size:
        return False  # nothing appended since last fire / session start

    predicate = tool_use_predicate_for(transcript_path)
    try:
        with open(transcript_path, "rb") as fh:
            result = transcript_scan.ScanResult()
            rng = transcript_scan.ByteRange(since_offset, size)
            lines = transcript_scan.iter_lines_forward(fh, rng, result, budget)
            found = transcript_scan.first_match(
                lines, lambda line: True if predicate(line) else None
            )
    except (OSError, TypeError, ValueError):
        return True

    if found:
        return True
    return result.reason == "cap"  # clean EOF with no hit -> False
