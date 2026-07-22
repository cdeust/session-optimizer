#!/usr/bin/env python3
"""subagent-tracker.py — SubagentStop hook: accumulate per-session subagent spend.

Contract
--------
Claude Code fires SubagentStop when a Task-tool subagent finishes. The hook
payload runs in the subagent's context and carries (at least):
  session_id, transcript_path, cwd, hook_event_name
and, on builds that expose them, agent_id / agent_type. transcript_path points
at the subagent's own `agent-<id>.jsonl` — the cleanest place to read its real
token spend, sidestepping the missing parent->child link of
anthropics/claude-code#32175.

This hook parses that transcript (deduped, billed, per-model priced via the
shared subagent_usage module), reads the sibling `.meta.json` for agentType /
description, and folds the result into a per-session aggregate at:

    /tmp/zetetic-subagents-<session_id>.json

The aggregate is keyed by agentId, so re-firing for the same subagent UPDATES
its entry rather than double-counting. The statusline reads this file to show
live subagent token/cost totals; the stop-context-guard reads it to surface
cumulative session spend (main thread + subagents) at checkpoint time.

Non-fatal by construction: any parse/IO error exits 0. A hook must never wedge
the session, and a tracking miss must never block real work.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Import the shared parsing/pricing core from the sibling tools/ directory.
_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, _TOOLS)
try:
    from subagent_usage import (  # noqa: E402
        subagent_record, discover_subagents, session_dir_for,
    )
except ImportError:
    # Core module unavailable -> nothing to track; never fail the hook.
    def subagent_record(_):  # type: ignore
        return None

    def discover_subagents(_):  # type: ignore
        return []

    def session_dir_for(_):  # type: ignore
        return ""


def _state_path(session_id):
    return os.path.join("/tmp", f"zetetic-subagents-{session_id}.json")


def _load_state(session_id):
    try:
        with open(_state_path(session_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("agents"), dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"session_id": session_id, "agents": {}}


def _agent_entry(rec):
    cache = (rec.usage.cache_write_5m + rec.usage.cache_write_1h
             + rec.usage.cache_read)
    return {
        "agent_type": rec.agent_type,
        "description": rec.description,
        "tool_use_id": rec.tool_use_id,
        "model": rec.usage.model,
        "input_tokens": rec.usage.input_tokens,
        "output_tokens": rec.usage.output_tokens,
        "cache_tokens": cache,
        "tool_uses": rec.usage.tool_uses,
        "web_search_requests": rec.usage.web_search_requests,
        "web_fetch_requests": rec.usage.web_fetch_requests,
        "cost_usd": round(rec.cost_usd, 4),
        "context_tokens": rec.usage.context_tokens,
    }


def _recompute_totals(state):
    """Roll the per-agent entries up into a totals block. Pure over `state`."""
    agents = state.get("agents", {})
    totals = {
        "count": len(agents),
        "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0,
        "tool_uses": 0, "cost_usd": 0.0, "context_tokens": 0,
        "web_search_requests": 0, "web_fetch_requests": 0,
    }
    for entry in agents.values():
        totals["input_tokens"] += entry.get("input_tokens", 0)
        totals["output_tokens"] += entry.get("output_tokens", 0)
        totals["cache_tokens"] += entry.get("cache_tokens", 0)
        totals["tool_uses"] += entry.get("tool_uses", 0)
        totals["cost_usd"] += entry.get("cost_usd", 0.0)
        totals["context_tokens"] += entry.get("context_tokens", 0)
        totals["web_search_requests"] += entry.get("web_search_requests", 0)
        totals["web_fetch_requests"] += entry.get("web_fetch_requests", 0)
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    state["totals"] = totals


def _update_from_transcript(state, transcript_path):
    """Parse one subagent transcript and upsert its entry into `state`."""
    rec = subagent_record(transcript_path)
    if rec is None:
        return
    state["agents"][rec.agent_id] = _agent_entry(rec)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    session_id = data.get("session_id") or "unknown"
    transcript_path = data.get("transcript_path")

    state = _load_state(session_id)

    # Primary: this subagent's own transcript (cleanest, exact attribution).
    if transcript_path and os.path.basename(transcript_path).startswith("agent-"):
        _update_from_transcript(state, transcript_path)
        # Belt-and-suspenders: also sweep every sibling subagent transcript so
        # the aggregate stays complete even if an earlier SubagentStop was
        # missed (e.g. the hook was installed mid-session).
        session_dir = session_dir_for(transcript_path)
        if session_dir:
            for path in discover_subagents(session_dir):
                if path not in (transcript_path,):
                    _update_from_transcript(state, path)

    _recompute_totals(state)
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        with open(_state_path(session_id), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
