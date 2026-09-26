"""subagent_spend.py — read the per-session subagent-spend aggregate and
render it into checkpoint-message fragments.

Separate concern from stop-context-guard.py's checkpoint policy: this module
only knows how to read the aggregate subagent-tracker.py maintains at
`<state_dir>/zetetic-subagents-<session_id>.json` and turn the totals into
the two human-readable fragments the checkpoint message and stub use. No
opinion about WARN/HARD levels or when to call it.
"""

import json
import os


def read_summary(state_dir: str, session_id: str):
    """Return (count, tokens, cost_usd) for this session's subagent spend, or
    (0, 0, 0.0) if no aggregate exists. Non-fatal: any read/parse problem
    returns zeros."""
    path = os.path.join(state_dir, f"zetetic-subagents-{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            totals = (json.load(fh) or {}).get("totals") or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return 0, 0, 0.0
    count = int(totals.get("count", 0) or 0)
    tokens = (
        int(totals.get("input_tokens", 0) or 0)
        + int(totals.get("output_tokens", 0) or 0)
        + int(totals.get("cache_tokens", 0) or 0)
    )
    tokens = int(totals.get("billed_tokens", tokens))
    raw_cost = totals.get("cost_usd", 0.0)
    cost = None if raw_cost is None else float(raw_cost or 0.0)
    return count, tokens, cost


def render_spend_line(count: int, tokens: int, cost: float | None) -> str:
    """One-line subagent-spend note for checkpoint systemMessages, or "" if
    there was no subagent spend."""
    if count <= 0:
        return ""
    return (
        f"\nSubagent spend this session (not in the main-thread context "
        f"measure above): {count} runs, ~{tokens:,} billed tokens, "
        + ("cost unavailable." if cost is None else f"~${cost:.2f}.")
    )


def render_stub_bullet(count: int, tokens: int, cost: float | None) -> str:
    """The checkpoint stub's "subagent spend" bullet, or "" if there was
    none."""
    if count <= 0:
        return ""
    return (
        f"- subagent spend: {count} runs · ~{tokens:,} billed tokens · "
        + ("cost unavailable" if cost is None else f"~${cost:.2f}")
        + " (separate from the context tokens above)\n"
    )
