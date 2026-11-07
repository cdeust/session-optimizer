#!/usr/bin/env python3
"""subagent_usage.py — parse and price Claude Code subagent (Task tool) activity.

Why this exists
---------------
Claude Code logs subagent work as separate per-agent transcripts that a
parent-only reader never sees. The local on-disk layout (verified 2026-06-26)
is:

    ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl          # main thread
    ~/.claude/projects/<encoded-cwd>/<session-id>/subagents/
        agent-<agentId>.jsonl                                    # subagent turns
        agent-<agentId>.meta.json   -> {agentType, description, toolUseId}
        workflows/wf_*/agent-*.jsonl                             # workflow subagents

Subagent assistant records carry `isSidechain: true`, `agentId`, a per-record
`message.model` (a subagent may run a cheaper model than the parent), and a
full `message.usage` object. A naive token/cost reader that only scans the main
`.jsonl` misses all of it — the documented gap in anthropics/claude-code#32175
and ryoppippi/ccusage#313.

This module is the single source of truth for:
  - parsing one transcript into a deduplicated, billed-token aggregate;
  - pricing that aggregate per model version at its published cache-tier rates;
  - discovering every subagent transcript under a session.

It is imported by the SubagentStop hook (live tracking) and exposes a CLI for
retrospective per-agent-type cost reporting.

Sources for every constant are cited at the use site (zetetic source discipline).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

# --- Pricing ---------------------------------------------------------------
# Per-million-token USD rates, one row per model version. Every rate is read
# from the official table, not derived from a multiplier: the cache-read rate
# is 0.10x input for most models but 0.05x for Opus 5.5 and Sonnet 5.5 and
# 0.025x for Fable 5.1 and Mythos 5.1, so a shared multiplier misprices them.
_PRICING_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/pricing, read 2026-11-07"
)


@dataclass(frozen=True)
class ModelPrice:
    """Per-MTok USD rates for one model version.

    cache_write_5m / cache_write_1h are the prompt-caching write rates for the
    5-minute and 1-hour TTL; cache_read is the "hits and refreshes" rate.
    """

    input_per_mtok: float
    output_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float
    cache_read_per_mtok: float


def _version_rx(family_version):
    """Regex matching one exact model version inside a model id.

    The version must be followed by the end of the id, a `-YYYYMMDD` snapshot
    date, or a non-version character (`[1m]`, `@`, `:`), so `opus-5` does not
    match `claude-opus-5-5` and `opus-4-8` does not match `opus-4-80`.
    """
    return re.compile(
        r"(?:^|[^a-z0-9])" + re.escape(family_version) + r"(?=$|-\d{8}(?!\d)|[^-0-9])"
    )


# (model version, price). Order is irrelevant: each pattern matches one
# version only. A model absent from this table is unpriced (price_for returns
# None) rather than priced at a guess.
# source: _PRICING_SOURCE for every row.
_PRICE_ROWS = (
    ("fable-5-1", ModelPrice(10.0, 50.0, 12.5, 20.0, 0.25)),
    ("mythos-5-1", ModelPrice(10.0, 50.0, 12.5, 20.0, 0.25)),
    ("fable-5", ModelPrice(10.0, 50.0, 12.5, 20.0, 1.0)),
    ("mythos-5", ModelPrice(10.0, 50.0, 12.5, 20.0, 1.0)),
    ("opus-5-5", ModelPrice(4.0, 20.0, 5.0, 8.0, 0.20)),
    ("opus-5", ModelPrice(5.0, 25.0, 6.25, 10.0, 0.50)),
    ("opus-4-8", ModelPrice(5.0, 25.0, 6.25, 10.0, 0.50)),
    ("opus-4-7", ModelPrice(5.0, 25.0, 6.25, 10.0, 0.50)),
    ("opus-4-6", ModelPrice(5.0, 25.0, 6.25, 10.0, 0.50)),
    ("opus-4-5", ModelPrice(5.0, 25.0, 6.25, 10.0, 0.50)),
    ("opus-4-1", ModelPrice(15.0, 75.0, 18.75, 30.0, 1.50)),
    ("opus-4", ModelPrice(15.0, 75.0, 18.75, 30.0, 1.50)),
    ("sonnet-5-5", ModelPrice(2.0, 10.0, 2.5, 4.0, 0.10)),
    ("sonnet-5", ModelPrice(2.0, 10.0, 2.5, 4.0, 0.20)),
    ("sonnet-4-6", ModelPrice(3.0, 15.0, 3.75, 6.0, 0.30)),
    ("sonnet-4-5", ModelPrice(3.0, 15.0, 3.75, 6.0, 0.30)),
    ("sonnet-4", ModelPrice(3.0, 15.0, 3.75, 6.0, 0.30)),
    ("haiku-4-5", ModelPrice(1.0, 5.0, 1.25, 2.0, 0.10)),
    # Haiku 5.5 is deliberately absent: its rates depend on the size of each
    # prompt (up to / over 100,000 tokens), and this module prices a whole
    # transcript's summed tokens, so any single rate would be a guess.
)
PRICING = tuple((_version_rx(v), price) for v, price in _PRICE_ROWS)


def price_for(model_id):
    """Return the ModelPrice for a model id, or None if it is unpriced. Pure.

    Precondition:  model_id is a string or None.
    Postcondition: returns the price of the one table row whose version the
                   id names, else None (unknown, tiered or missing model).
    """
    mid = (model_id or "").lower()
    for rx, price in PRICING:
        if rx.search(mid):
            return price
    return None


# --- Usage aggregate -------------------------------------------------------


@dataclass
class Usage:
    """Billed-token totals for one transcript, plus activity counters.

    All token fields are summed across the transcript's deduplicated assistant
    turns. cache_write_5m / cache_write_1h split cache_creation by TTL when the
    record carries the `cache_creation` breakdown; otherwise the whole
    cache_creation total is attributed to the 5-minute tier (its default).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0
    tool_uses: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    model: str = ""
    models: set = field(default_factory=set)

    def add(self, other):
        """Accumulate another Usage in place (for session-level totals)."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_5m += other.cache_write_5m
        self.cache_write_1h += other.cache_write_1h
        self.cache_read += other.cache_read
        self.tool_uses += other.tool_uses
        self.web_search_requests += other.web_search_requests
        self.web_fetch_requests += other.web_fetch_requests
        self.models |= other.models
        if other.model and not self.model:
            self.model = other.model

    @property
    def context_tokens(self):
        """Tokens that occupy the model's context window on the last turn's
        scale — input + both cache tiers + read. (Sum-of-turns here, used only
        for display magnitude, not for context-window enforcement.)"""
        return (
            self.input_tokens
            + self.cache_write_5m
            + self.cache_write_1h
            + self.cache_read
        )

    def to_dict(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_5m": self.cache_write_5m,
            "cache_write_1h": self.cache_write_1h,
            "cache_read": self.cache_read,
            "tool_uses": self.tool_uses,
            "web_search_requests": self.web_search_requests,
            "web_fetch_requests": self.web_fetch_requests,
            "model": self.model,
            "models": sorted(self.models),
        }


def cost_usd(usage):
    """Total USD cost of a Usage at its own model's rates, or None if tokens
    were billed on an unpriced model.

    Pure. Uses the per-transcript `model` for the rate; mixed-model transcripts
    (rare for a single subagent) are priced at the first observed model, which
    is the dominant one in practice.
    """
    billed = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_write_5m,
        usage.cache_write_1h,
        usage.cache_read,
    )
    if not any(billed):
        return 0.0  # nothing billed costs nothing, whatever the model
    price = price_for(usage.model)
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input_per_mtok
        + usage.output_tokens * price.output_per_mtok
        + usage.cache_write_5m * price.cache_write_5m_per_mtok
        + usage.cache_write_1h * price.cache_write_1h_per_mtok
        + usage.cache_read * price.cache_read_per_mtok
    )
    return total / 1_000_000.0


def _accumulate_record(obj, usage):
    """Fold one parsed JSONL record's usage into `usage`. Pure; no I/O."""
    msg = obj.get("message") or {}
    u = msg.get("usage")
    if not u:
        return
    usage.input_tokens += int(u.get("input_tokens", 0) or 0)
    usage.output_tokens += int(u.get("output_tokens", 0) or 0)
    cache_create = int(u.get("cache_creation_input_tokens", 0) or 0)
    breakdown = u.get("cache_creation") or {}
    t5 = int(breakdown.get("ephemeral_5m_input_tokens", 0) or 0)
    t1 = int(breakdown.get("ephemeral_1h_input_tokens", 0) or 0)
    if t5 or t1:
        usage.cache_write_5m += t5
        usage.cache_write_1h += t1
    else:
        # No TTL breakdown: attribute the whole cache-creation total to 5m,
        # the default ephemeral TTL (source: Anthropic prompt-caching docs).
        usage.cache_write_5m += cache_create
    usage.cache_read += int(u.get("cache_read_input_tokens", 0) or 0)
    server = u.get("server_tool_use") or {}
    usage.web_search_requests += int(server.get("web_search_requests", 0) or 0)
    usage.web_fetch_requests += int(server.get("web_fetch_requests", 0) or 0)
    model = msg.get("model") or obj.get("model")
    if model:
        usage.models.add(model)
        if not usage.model:
            usage.model = model
    for block in msg.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            usage.tool_uses += 1


def _keyed_usage_record(line: str):
    """Parse one JSONL line into a (key, ctx_magnitude, record) triple, or
    None if the line is blank, malformed, or carries no usage payload.

    Key is the message id, falling back to the record uuid (or the record's
    own identity) so the record is still counted exactly once.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    msg = obj.get("message") or {}
    if not msg.get("usage"):
        return None
    key = msg.get("id") or obj.get("uuid") or id(obj)
    return key, _ctx_magnitude(msg["usage"]), obj


def _dedupe_by_message(fh) -> dict:
    """Largest-usage-wins fold of an open transcript file's records by key."""
    by_message: dict = {}
    records = (r for line in fh if (r := _keyed_usage_record(line)) is not None)
    for key, ctx, obj in records:
        prev = by_message.get(key)
        if prev is None or ctx > prev[0]:
            by_message[key] = (ctx, obj)
    return by_message


def _open_transcript(path):
    """Open path for reading, or None if it's missing/unreadable.

    Exception scope is deliberately narrow: this guards ONLY the open()
    call. A ValueError/TypeError raised later, while reading a malformed
    record, must NOT be caught here — see parse_transcript_usage.
    """
    try:
        return open(path, "r", encoding="utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return None


def parse_transcript_usage(path):
    """Parse one transcript JSONL into a deduplicated billed Usage.

    Deduplicates assistant turns by `message.id` (largest-usage-wins) before
    summing — Claude Code re-logs the same assistant turn across forks/resumes,
    and double-counting it inflates tokens (ryoppippi/ccusage#888). Each kept
    message contributes its own billed tokens; we do NOT dedup distinct turns,
    only re-logged copies of one turn.

    Precondition:  path is a path string.
    Postcondition: returns a Usage (zeroed if the file is missing/unreadable).
    A malformed per-record usage payload (e.g. a non-numeric token count) is
    NOT swallowed here: it propagates as ValueError/TypeError, since that is
    a data-quality bug in the transcript, not a missing/unreadable file.
    """
    fh = _open_transcript(path)
    if fh is None:
        return Usage()
    with fh:
        by_message = _dedupe_by_message(fh)

    total = Usage()
    for _, obj in by_message.values():
        _accumulate_record(obj, total)
    return total


def _ctx_magnitude(u):
    """Cheap comparison key for largest-usage-wins dedup. Pure."""
    return (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0)
        + int(u.get("output_tokens", 0) or 0)
    )


# --- Subagent discovery ----------------------------------------------------


@dataclass
class SubagentRecord:
    """One subagent's identity (from its .meta.json) plus parsed usage/cost."""

    agent_id: str
    agent_type: str
    description: str
    tool_use_id: str
    usage: Usage
    cost_usd: float | None  # None: the model is unpriced
    transcript_path: str


def _read_meta(meta_path):
    """Return (agentType, description, toolUseId) from a subagent .meta.json,
    or empty strings if absent/malformed. Pure-ish (one read)."""
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return "", "", ""
    return (
        str(meta.get("agentType", "") or ""),
        str(meta.get("description", "") or ""),
        str(meta.get("toolUseId", "") or ""),
    )


def subagent_record(transcript_path):
    """Build a SubagentRecord from one subagent transcript path.

    Reads the sibling `<name>.meta.json` for agentType/description/toolUseId
    (the cleanest attribution source — no need to parse the parent transcript).
    """
    agent_id = _agent_id_from_path(transcript_path)
    meta_path = transcript_path[: -len(".jsonl")] + ".meta.json"
    agent_type, description, tool_use_id = _read_meta(meta_path)
    usage = parse_transcript_usage(transcript_path)
    return SubagentRecord(
        agent_id=agent_id,
        agent_type=agent_type or "unknown",
        description=description,
        tool_use_id=tool_use_id,
        usage=usage,
        cost_usd=cost_usd(usage),
        transcript_path=transcript_path,
    )


def _agent_id_from_path(path):
    """Extract the agentId from an `agent-<id>.jsonl` filename. Pure."""
    base = os.path.basename(path)
    if base.startswith("agent-") and base.endswith(".jsonl"):
        return base[len("agent-") : -len(".jsonl")]
    return base


def discover_subagents(session_dir):
    """Yield every subagent transcript path under a session directory.

    Covers both `subagents/agent-*.jsonl` and nested
    `subagents/workflows/wf_*/agent-*.jsonl`. Returns [] if the directory is
    absent. Sorted for deterministic output.
    """
    root = os.path.join(session_dir, "subagents")
    found = []
    if not os.path.isdir(root):
        return found
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith("agent-") and name.endswith(".jsonl"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def session_dir_for(transcript_path):
    """Given a subagent transcript path, return the owning session directory
    (the `<session-id>/` dir that contains `subagents/`). Pure."""
    # .../<session-id>/subagents[/workflows/wf_*]/agent-*.jsonl
    parts = transcript_path.split(os.sep)
    try:
        idx = len(parts) - 1 - parts[::-1].index("subagents")
    except ValueError:
        return os.path.dirname(transcript_path)
    return os.sep.join(parts[:idx])


# --- CLI: retrospective per-agent-type report ------------------------------


def _fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _encoded_project_dir(cwd):
    """Map an absolute cwd to its ~/.claude/projects/<encoded> directory.
    Claude Code replaces both '/' and '.' with '-' in the encoded name."""
    encoded = cwd.replace("/", "-").replace(".", "-")
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", encoded)


def _iter_session_dirs(project_dir):
    """Yield session directories (the `<session-id>/` dirs that hold subagents)
    under a project dir."""
    if not os.path.isdir(project_dir):
        return
    for name in os.listdir(project_dir):
        full = os.path.join(project_dir, name)
        if os.path.isdir(full) and os.path.isdir(os.path.join(full, "subagents")):
            yield full


def _add_cost(total, cost):
    """Sum two costs where None (unpriced) is absorbing. Pure."""
    if total is None or cost is None:
        return None
    return total + cost


def _round_cost(cost):
    return None if cost is None else round(cost, 4)


def _fmt_cost(cost):
    return f"{'unpriced':>9}" if cost is None else f"${cost:>8.2f}"


def build_report(cwd):
    """Aggregate subagent usage across all sessions of a project, grouped by
    agent_type. Returns a dict suitable for JSON or table rendering.

    A cost is None when any run it covers used an unpriced model: a partial
    sum would be reported as if it were the whole spend.
    """
    project_dir = _encoded_project_dir(cwd)
    by_type = {}
    grand = Usage()
    grand_cost = 0.0
    count = 0
    for session_dir in _iter_session_dirs(project_dir):
        for path in discover_subagents(session_dir):
            rec = subagent_record(path)
            count += 1
            grand.add(rec.usage)
            grand_cost = _add_cost(grand_cost, rec.cost_usd)
            bucket = by_type.setdefault(
                rec.agent_type, {"count": 0, "usage": Usage(), "cost_usd": 0.0}
            )
            bucket["count"] += 1
            bucket["usage"].add(rec.usage)
            bucket["cost_usd"] = _add_cost(bucket["cost_usd"], rec.cost_usd)
    return {
        "project_dir": project_dir,
        "subagent_count": count,
        "by_agent_type": {
            t: {
                "count": b["count"],
                "usage": b["usage"].to_dict(),
                "cost_usd": _round_cost(b["cost_usd"]),
            }
            for t, b in sorted(by_type.items())
        },
        "totals": {
            "usage": grand.to_dict(),
            "cost_usd": _round_cost(grand_cost),
        },
    }


def _print_table(report):
    types = report["by_agent_type"]
    print(
        f"Subagent usage — {report['subagent_count']} subagent runs across "
        f"{report['project_dir']}"
    )
    if not types:
        print("  (no subagent transcripts found)")
        return
    header = f"  {'AGENT TYPE':<24} {'RUNS':>5} {'IN':>7} {'OUT':>7} {'CACHE':>7} {'COST':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t, b in types.items():
        u = b["usage"]
        cache = u["cache_write_5m"] + u["cache_write_1h"] + u["cache_read"]
        print(
            f"  {t:<24} {b['count']:>5} "
            f"{_fmt_tokens(u['input_tokens']):>7} "
            f"{_fmt_tokens(u['output_tokens']):>7} "
            f"{_fmt_tokens(cache):>7} "
            f"{_fmt_cost(b['cost_usd'])}"
        )
    tot = report["totals"]
    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':<24} {report['subagent_count']:>5} "
        f"{'':>7} {'':>7} {'':>7} {_fmt_cost(tot['cost_usd'])}"
    )


def main(argv):
    """CLI entry point. Usage:
        subagent_usage.py [--json] [cwd]
    Defaults cwd to the current working directory."""
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    cwd = args[0] if args else os.getcwd()
    cwd = os.path.abspath(cwd)
    report = build_report(cwd)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
