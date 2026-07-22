#!/usr/bin/env python3
"""Per-session transcript telemetry for the statusline.

Emits a small JSON cache the statusline reads cheaply each refresh. This is the
slow path (transcript I/O) and is meant to run backgrounded on a short TTL,
never inline with the 10s refresh — exactly like statusline-costs.py.

What it derives, and why each is reliable:

  last_ts        epoch seconds of the most recent assistant `usage` record.
                 The statusline turns this into a live "last response age" and a
                 prompt-cache TTL countdown WITHOUT re-running this script, so
                 those two stay second-accurate between background refreshes.

  tok_per_s      turn throughput of the last assistant turn:
                     output_tokens(last turn) / (T_last - T_trigger)
                 T_trigger is the timestamp of the last non-assistant record
                 before the turn (the user/tool message that triggered it).
                 This is wall-clock turn throughput — it INCLUDES tool-execution
                 latency, so it is a lower bound on raw decode speed, not the
                 API token/s. Labelled accordingly in the statusline. Null when
                 the span is degenerate (single record / zero span).

  compactions    count of context-compaction boundaries in the transcript
                 (records carrying isCompactSummary / type "summary" /
                 subtype "compact"). Computed incrementally: JSONL is append-only
                 with whole lines, so a grown file is scanned only over its
                 appended byte range [prev_size, size); an unchanged file reuses
                 the cached count; a shrunk/rotated file is rescanned in full.

Non-fatal by construction: any parse/IO error prints "{}" and exits 0. The
statusline treats an empty/missing cache as "no telemetry this round".

Usage:  statusline-transcript.py <transcript_path>
"""

import json
import os
import sys
from datetime import datetime

CACHE_PATH = os.path.expanduser("~/.claude/.statusline-transcript-cache.json")

# Tail window for the last-turn scan. The last turn's records live at the end of
# the file; 512 KiB covers a very large multi-tool turn many times over while
# keeping the parse to O(window), not O(file). JSONL lines are whole, so a tail
# read that starts mid-line simply drops that leading partial line.
TAIL_WINDOW = 512 * 1024

# Substring prefilters — cheap screen before JSON parse. A compaction boundary
# carries at least one of these markers across Claude Code versions.
COMPACT_MARKERS = ('"isCompactSummary"', '"subtype":"compact"', '"type":"summary"')


def _epoch(ts):
    """ISO-8601 timestamp -> epoch seconds (float), or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _is_compaction(line: str) -> bool:
    """True if this JSONL line is a context-compaction boundary. Substring
    prefilter, then confirm with a parse so a stray marker inside message text
    cannot inflate the count."""
    if not any(m in line for m in COMPACT_MARKERS):
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    if obj.get("isCompactSummary") is True:
        return True
    if obj.get("type") == "summary":
        return True
    if obj.get("subtype") == "compact":
        return True
    msg = obj.get("message") or {}
    return msg.get("subtype") == "compact"


def _count_compactions(path: str, start: int) -> int:
    """Count compaction markers in path over [start, EOF). start must sit on a
    line boundary (it always does for append-only JSONL)."""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if start > 0:
                fh.seek(start)
            for line in fh:
                if _is_compaction(line):
                    n += 1
    except OSError:
        return 0
    return n


def _tail_records(path: str, size: int):
    """Parse the last TAIL_WINDOW bytes into an ordered list of lightweight
    records: {"ts": epoch|None, "asst": bool, "out": int}. Oldest-first."""
    start = max(0, size - TAIL_WINDOW)
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            blob = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = blob.split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # drop the leading partial line
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        is_asst = obj.get("type") == "assistant" or msg.get("role") == "assistant"
        out.append({
            "ts": _epoch(obj.get("timestamp")),
            "asst": bool(is_asst),
            "out": int(usage.get("output_tokens", 0) or 0),
            "model": msg.get("model") or obj.get("model"),
        })
    return out


def _last_turn(records):
    """(last_ts, tok_per_s, model) for the final assistant turn, from the tail
    records. tok_per_s is None when the span is degenerate."""
    last_i = None
    for i in range(len(records) - 1, -1, -1):
        if records[i]["asst"] and records[i]["ts"] is not None:
            last_i = i
            break
    if last_i is None:
        return None, None, None

    last = records[last_i]
    last_ts = last["ts"]
    model = last["model"]
    out_tok = last["out"]

    # Walk back to the trigger: the last non-assistant record before this turn.
    trigger_ts = None
    for i in range(last_i - 1, -1, -1):
        if not records[i]["asst"]:
            trigger_ts = records[i]["ts"]
            break
        model = model or records[i]["model"]

    tok_per_s = None
    if trigger_ts is not None and out_tok > 0:
        span = last_ts - trigger_ts
        if span > 0:
            tok_per_s = round(out_tok / span, 1)
    return last_ts, tok_per_s, model


def _load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def build(path: str):
    try:
        size = os.stat(path).st_size
    except (OSError, TypeError, ValueError):
        return {}
    if size == 0:
        return {}

    prev = _load_cache()
    same_file = prev.get("path") == path
    prev_size = prev.get("size", 0) if same_file else 0

    # Incremental compaction count: reuse cache when unchanged, scan the appended
    # range when grown, rescan fully when shrunk/rotated/new.
    if same_file and size == prev_size:
        compactions = int(prev.get("compactions", 0) or 0)
    elif same_file and size > prev_size:
        compactions = int(prev.get("compactions", 0) or 0) + _count_compactions(path, prev_size)
    else:
        compactions = _count_compactions(path, 0)

    last_ts, tok_per_s, model = _last_turn(_tail_records(path, size))

    return {
        "path": path,
        "size": size,
        "last_ts": last_ts,
        "tok_per_s": tok_per_s,
        "compactions": compactions,
        "model": model,
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    result = build(path) if path else {}
    tmp = CACHE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
