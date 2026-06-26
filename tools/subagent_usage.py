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
  - pricing that aggregate per model with the correct cache-tier multipliers;
  - discovering every subagent transcript under a session.

It is imported by the SubagentStop hook (live tracking) and exposes a CLI for
retrospective per-agent-type cost reporting.

Sources for every constant are cited at the use site (zetetic source discipline).
"""

import json
import os
import sys
from dataclasses import dataclass, field

# --- Pricing ---------------------------------------------------------------
# Per-million-token rates and cache multipliers. Single source: Anthropic
# Claude API docs (pricing table + prompt-caching economics), captured via the
# claude-api skill on 2026-06-26.
#   - Opus 4.8 : $5 in / $25 out per MTok
#   - Sonnet 4.6: $3 in / $15 out per MTok
#   - Haiku 4.5 : $1 in / $5  out per MTok
#   - Fable 5  : $10 in / $50 out per MTok
# Cache multipliers (relative to the model's input rate), same source:
#   - cache write, 5-minute TTL : 1.25x input
#   - cache write, 1-hour  TTL  : 2.00x input
#   - cache read                : 0.10x input
CACHE_WRITE_5M_MULT = 1.25  # source: Anthropic prompt-caching economics (claude-api skill, 2026-06-26)
CACHE_WRITE_1H_MULT = 2.00  # source: Anthropic prompt-caching economics (claude-api skill, 2026-06-26)
CACHE_READ_MULT = 0.10      # source: Anthropic prompt-caching economics (claude-api skill, 2026-06-26)


@dataclass(frozen=True)
class ModelPrice:
    """Per-MTok input/output USD rates for one model family."""
    input_per_mtok: float
    output_per_mtok: float


# First substring match against the lowercased model id wins; mirrors the
# matching discipline already used by ctxguard-thresholds.json.
PRICING = [
    ("fable",  ModelPrice(10.0, 50.0)),   # source: Anthropic pricing (claude-api skill, 2026-06-26)
    ("mythos", ModelPrice(10.0, 50.0)),   # source: Anthropic pricing — same tier as Fable 5
    ("opus",   ModelPrice(5.0, 25.0)),    # source: Anthropic pricing (claude-api skill, 2026-06-26)
    ("sonnet", ModelPrice(3.0, 15.0)),    # source: Anthropic pricing (claude-api skill, 2026-06-26)
    ("haiku",  ModelPrice(1.0, 5.0)),     # source: Anthropic pricing (claude-api skill, 2026-06-26)
]
_DEFAULT_PRICE = ModelPrice(5.0, 25.0)    # unknown model -> Opus-tier (conservative)


def price_for(model_id):
    """Return the ModelPrice for a model id. Pure; never raises.

    Precondition:  model_id is a string or None.
    Postcondition: returns the first substring-matched ModelPrice, else the
                   Opus-tier default.
    """
    mid = (model_id or "").lower()
    for needle, price in PRICING:
        if needle in mid:
            return price
    return _DEFAULT_PRICE


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
        return (self.input_tokens + self.cache_write_5m
                + self.cache_write_1h + self.cache_read)

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
    """Total USD cost of a Usage, priced at its own model's rates.

    Pure. Uses the per-transcript `model` for the rate; mixed-model transcripts
    (rare for a single subagent) are priced at the first observed model, which
    is the dominant one in practice.
    """
    price = price_for(usage.model)
    in_rate = price.input_per_mtok
    out_rate = price.output_per_mtok
    total = (
        usage.input_tokens * in_rate
        + usage.output_tokens * out_rate
        + usage.cache_write_5m * in_rate * CACHE_WRITE_5M_MULT
        + usage.cache_write_1h * in_rate * CACHE_WRITE_1H_MULT
        + usage.cache_read * in_rate * CACHE_READ_MULT
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


def parse_transcript_usage(path):
    """Parse one transcript JSONL into a deduplicated billed Usage.

    Deduplicates assistant turns by `message.id` (largest-usage-wins) before
    summing — Claude Code re-logs the same assistant turn across forks/resumes,
    and double-counting it inflates tokens (ryoppippi/ccusage#888). Each kept
    message contributes its own billed tokens; we do NOT dedup distinct turns,
    only re-logged copies of one turn.

    Precondition:  path is a path string.
    Postcondition: returns a Usage (zeroed if the file is missing/unreadable).
    """
    by_message = {}
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return Usage()
    try:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            if not msg.get("usage"):
                continue
            # Key by message id; if absent, fall back to the record uuid so the
            # record is still counted exactly once.
            key = msg.get("id") or obj.get("uuid") or id(obj)
            ctx = _ctx_magnitude(msg["usage"])
            prev = by_message.get(key)
            if prev is None or ctx > prev[0]:
                by_message[key] = (ctx, obj)
    finally:
        fh.close()

    total = Usage()
    for _, obj in by_message.values():
        _accumulate_record(obj, total)
    return total


def _ctx_magnitude(u):
    """Cheap comparison key for largest-usage-wins dedup. Pure."""
    return (int(u.get("input_tokens", 0) or 0)
            + int(u.get("cache_creation_input_tokens", 0) or 0)
            + int(u.get("cache_read_input_tokens", 0) or 0)
            + int(u.get("output_tokens", 0) or 0))


# --- Subagent discovery ----------------------------------------------------

@dataclass
class SubagentRecord:
    """One subagent's identity (from its .meta.json) plus parsed usage/cost."""
    agent_id: str
    agent_type: str
    description: str
    tool_use_id: str
    usage: Usage
    cost_usd: float
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
    meta_path = transcript_path[:-len(".jsonl")] + ".meta.json"
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
        return base[len("agent-"):-len(".jsonl")]
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


def build_report(cwd):
    """Aggregate subagent usage across all sessions of a project, grouped by
    agent_type. Returns a dict suitable for JSON or table rendering."""
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
            grand_cost += rec.cost_usd
            bucket = by_type.setdefault(
                rec.agent_type, {"count": 0, "usage": Usage(), "cost_usd": 0.0})
            bucket["count"] += 1
            bucket["usage"].add(rec.usage)
            bucket["cost_usd"] += rec.cost_usd
    return {
        "project_dir": project_dir,
        "subagent_count": count,
        "by_agent_type": {
            t: {
                "count": b["count"],
                "usage": b["usage"].to_dict(),
                "cost_usd": round(b["cost_usd"], 4),
            }
            for t, b in sorted(by_type.items())
        },
        "totals": {
            "usage": grand.to_dict(),
            "cost_usd": round(grand_cost, 4),
        },
    }


def _print_table(report):
    types = report["by_agent_type"]
    print(f"Subagent usage — {report['subagent_count']} subagent runs across "
          f"{report['project_dir']}")
    if not types:
        print("  (no subagent transcripts found)")
        return
    header = f"  {'AGENT TYPE':<24} {'RUNS':>5} {'IN':>7} {'OUT':>7} {'CACHE':>7} {'COST':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t, b in types.items():
        u = b["usage"]
        cache = u["cache_write_5m"] + u["cache_write_1h"] + u["cache_read"]
        print(f"  {t:<24} {b['count']:>5} "
              f"{_fmt_tokens(u['input_tokens']):>7} "
              f"{_fmt_tokens(u['output_tokens']):>7} "
              f"{_fmt_tokens(cache):>7} "
              f"${b['cost_usd']:>8.2f}")
    tot = report["totals"]
    print("  " + "-" * (len(header) - 2))
    print(f"  {'TOTAL':<24} {report['subagent_count']:>5} "
          f"{'':>7} {'':>7} {'':>7} ${tot['cost_usd']:>8.2f}")


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
