#!/usr/bin/env python3
"""Aggregate token costs from Claude Code transcripts for the statusline.

costUSD is null in transcripts, so cost is derived from token counts x per-model
rates. Output is a small JSON cache the statusline reads cheaply; this script is
the slow path (full scan of ~/.claude/projects/**/*.jsonl) and is meant to run in
the background on a TTL, never inline with the 10s statusline refresh.

Pricing (USD per 1M tokens):
  - base input/output: source ~/.claude/rules/agent-reference/effort-calibration.md
    (Opus 4.8 5/25, Sonnet 4.6 3/15, Haiku 4.5 1/5). Fable 5 ~= 2x Opus per
    ~/.claude/rules/agent-reference/token-budget.md ("pays ~2x Opus rates").
  - cache multipliers: standard Anthropic prompt-cache pricing —
    read = 0.1x input, 5m write = 1.25x input, 1h write = 2.0x input.
    source: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
"""

import json
import os
import sys
from datetime import datetime, timezone
from glob import glob

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
CACHE_PATH = os.path.expanduser("~/.claude/.statusline-cost-cache.json")

# (input, output) USD per 1M tokens, matched by substring of the model id.
BASE_RATES = (
    ("opus", (5.0, 25.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
    ("fable", (10.0, 50.0)),  # ~2x Opus, source: token-budget.md
)
DEFAULT_RATE = (5.0, 25.0)  # unknown -> Opus, the conservative high tier

CACHE_READ_MULT = 0.1
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0


def rate_for(model):
    m = (model or "").lower()
    for needle, rate in BASE_RATES:
        if needle in m:
            return rate
    return DEFAULT_RATE


def message_cost_tokens(model, usage):
    """(USD cost, total tokens) of one assistant message from its usage block.

    Total tokens sums every counter (input + output + cache read + cache
    creation) — the raw volume processed, the figure a monthly token quota is
    measured against."""
    rate_in, rate_out = rate_for(model)
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    read_tok = usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation", {}) or {}
    w5 = creation.get("ephemeral_5m_input_tokens", 0) or 0
    w1 = creation.get("ephemeral_1h_input_tokens", 0) or 0
    # Older records only carry the flat cache_creation_input_tokens (treat as 5m).
    if not creation:
        w5 = usage.get("cache_creation_input_tokens", 0) or 0
    cost = (
        in_tok * rate_in
        + out_tok * rate_out
        + read_tok * rate_in * CACHE_READ_MULT
        + w5 * rate_in * CACHE_WRITE_5M_MULT
        + w1 * rate_in * CACHE_WRITE_1H_MULT
    )
    return cost / 1_000_000.0, in_tok + out_tok + read_tok + w5 + w1


def month_key(ts):
    """YYYY-MM from an ISO timestamp, or None if unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return f"{dt.year:04d}-{dt.month:02d}"
    except (ValueError, AttributeError):
        return None


def scan_file(path, per_month, per_month_tok, agent_acc):
    """Add this transcript's cost/tokens into per_month and per_month_tok; if it
    is a subagent file, accumulate its cost and count into
    agent_acc=[total_cost, invocations]."""
    # Subagent transcripts are named agent-*.jsonl, whether spawned via Task
    # (<session>/subagents/) or by a workflow (<session>/subagents/workflows/wf_*/).
    is_agent = os.path.basename(path).startswith("agent-")
    file_cost = 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                c, t = message_cost_tokens(msg.get("model"), usage)
                file_cost += c
                mk = month_key(rec.get("timestamp"))
                if mk:
                    per_month[mk] = per_month.get(mk, 0.0) + c
                    per_month_tok[mk] = per_month_tok.get(mk, 0) + t
    except OSError:
        return
    if is_agent:
        agent_acc[0] += file_cost
        agent_acc[1] += 1


def build():
    per_month = {}
    per_month_tok = {}
    agent_acc = [0.0, 0]  # [total_cost, invocations]
    for path in glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
        scan_file(path, per_month, per_month_tok, agent_acc)

    now = datetime.now(timezone.utc)
    current_key = f"{now.year:04d}-{now.month:02d}"
    current_month = per_month.get(current_key, 0.0)
    current_month_tok = per_month_tok.get(current_key, 0)
    n_months = len(per_month)
    total = sum(per_month.values())
    total_tok = sum(per_month_tok.values())
    avg_month = total / n_months if n_months else 0.0
    avg_month_tok = total_tok / n_months if n_months else 0.0
    agent_total, agent_n = agent_acc
    avg_agent = agent_total / agent_n if agent_n else 0.0

    return {
        "generated_at": now.isoformat(),
        "current_month": round(current_month, 2),
        "avg_month": round(avg_month, 2),
        "current_month_tokens": current_month_tok,
        "avg_month_tokens": round(avg_month_tok),
        "n_months": n_months,
        "avg_per_agent": round(avg_agent, 4),
        "agent_invocations": agent_n,
        "total_all_time": round(total, 2),
    }


def main():
    result = build()
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    os.replace(tmp, CACHE_PATH)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
