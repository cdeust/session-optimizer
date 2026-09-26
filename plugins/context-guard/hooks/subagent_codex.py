"""Codex child usage, from the SubagentStop agent_transcript_path.

Source: Codex 0.157.1 SubagentStop schema and token_usage_record rollouts;
see README.md, Codex support. thread_token_usage is cumulative billed usage;
usage.input_tokens is only the latest request's context occupancy. Cached
input is a subset, so billed_tokens must not add it a second time.
No verified Codex tariff is bundled here: unknown cost stays unknown.
"""

import json


def _counter(usage, name):
    value = usage.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid token counter")
    return value


def _entry(payload, total, context, activity):
    model, tool_calls = activity
    inputs = _counter(total, "input_tokens")
    outputs = _counter(total, "output_tokens")
    return {
        "agent_type": payload.get("agent_type") or "unknown",
        "description": "",
        "tool_use_id": "",
        "model": model,
        "input_tokens": inputs,
        "output_tokens": outputs,
        "cache_tokens": _counter(total, "cached_input_tokens"),
        "billed_tokens": inputs + outputs,
        "tool_uses": len(tool_calls),
        "web_search_requests": 0,
        "web_fetch_requests": 0,
        "cost_usd": None,
        "context_tokens": context,
        "host": "codex",
    }


def read_entry(payload):
    """Return a measured child entry, or None on missing/invalid evidence.

    The first session_meta identifies the child. Forks can contain a later
    inherited parent session_meta; never replace the child's identity with it.
    Repeated Stop events replace this entry in the aggregate, never add it.
    """
    try:
        with open(payload["agent_transcript_path"], encoding="utf-8") as stream:
            return _read(stream, payload)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _read(stream, payload):
    child = None
    total = None
    context = 0
    model = payload.get("model") or "unknown"
    turn = None
    calls_by_turn = {}
    child_turns = set()
    for line in stream:
        record = json.loads(line)
        body = record.get("payload") or {}
        kind = record.get("type")
        if kind == "session_meta" and child is None:
            child = body.get("id")
        elif kind == "turn_context":
            turn = body.get("turn_id")
            model = body.get("model") or model
        elif kind == "response_item" and body.get("type") in (
            "function_call",
            "custom_tool_call",
        ):
            if body.get("call_id"):
                calls_by_turn.setdefault(turn, set()).add(body["call_id"])
        elif kind == "token_usage_record" and body.get("thread_id") == child:
            total = body.get("thread_token_usage")
            context = _counter(body.get("usage") or {}, "input_tokens")
            child_turns.add(body.get("turn_id"))
    if not child or child != payload.get("agent_id") or not isinstance(total, dict):
        return None
    calls = set().union(*(calls_by_turn.get(turn, set()) for turn in child_turns))
    return _entry(payload, total, context, (model, calls))
