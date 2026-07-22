"""checkpoint_protocol.py — compose the Stop guard's checkpoint instructions.

stop-context-guard.py measures context usage and decides WHEN to act; this
module owns WHAT the model is told. Each level (warn / hard) has two
variants, chosen at runtime by `detect_memory_tool`:

  * generic (default) — references only tools that exist in vanilla
    Claude Code: the memory-writer subagent fills the mechanical stub
    file the hook already wrote.
  * scoped — emitted only when a scoped memory layer is installed
    (`tools/memory-tool.sh` in the project root or ~/.claude/tools/):
    the checkpoint routes through the store's block verbs and durable
    facts through its remember endpoint.

Detection runs per invocation, so an outside user without the memory
layer never sees store-specific wording (memory verbs, MEMORY_AGENT_ID,
cortex:remember).
"""

import os

SCHEMA = (
    "goals / file references (paths + line ranges) / errors and fixes / "
    "current state / next steps — <=500 words total, quoted tool outputs "
    "clipped to 2,000 chars"
)

_MEMORY_TOOL_RELPATH = os.path.join("tools", "memory-tool.sh")


def detect_memory_tool(cwd: str):
    """Return the scoped memory layer's CLI path when installed, else None.

    Same detection the memory-writer agent performs: `tools/memory-tool.sh`
    in the project root, then in ~/.claude/tools/.
    """
    candidates = (
        os.path.join(cwd or "", _MEMORY_TOOL_RELPATH),
        os.path.join(os.path.expanduser("~"), ".claude", _MEMORY_TOOL_RELPATH),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _warn_header(ctx: int, warn: int, hard: int) -> str:
    return (
        f"⚠ CHECKPOINT THRESHOLD — {ctx:,} input tokens "
        f"(≥ {warn:,} for this model; hard stop at {hard:,}).\n"
        f"This is a reflection pause, NOT the end of the session. While you still "
        f"have headroom, persist the semantic checkpoint via a budgeted subagent, "
        f"then resume the user's task:\n\n"
        f"1. Distill the session into the summary schema ({SCHEMA}).\n"
    )


def _warn_footer(hard: int) -> str:
    return (
        f"3. CONTINUE the user's task in this session. The hard cap at {hard:,} "
        f"still applies; thanks to this reflection it should be a formality."
    )


_SPAWN = (
    "2. Spawn the memory-writer subagent (Agent tool, subagent_type "
    "\"memory-writer\", or \"context-guard:memory-writer\" if only the "
    "plugin copy is installed). "
)

_SPAWN_FALLBACK = (
    "Do not write the checkpoint yourself unless the spawn fails (agent not "
    "installed — it ships in the context-guard plugin under "
    "agents/memory-writer.md; install with /agents or copy it to "
    "~/.claude/agents/).\n"
)


def warn_reason(ctx: int, stub_path: str, warn: int, hard: int) -> str:
    """WARN instructions, generic variant: stub-file protocol only."""
    return (
        _warn_header(ctx, warn, hard)
        + _SPAWN
        + f"Pass it: your distilled summary and the checkpoint stub path "
          f"{stub_path or 'n/a'}. It merges the summary into the stub's schema "
          f"sections in place and mirrors it to latest.md in the same "
          f"directory. "
        + _SPAWN_FALLBACK
        + _warn_footer(hard)
    )


def warn_reason_scoped(ctx: int, stub_path: str, warn: int, hard: int) -> str:
    """WARN instructions when a scoped memory layer is installed."""
    return (
        _warn_header(ctx, warn, hard)
        + _SPAWN
        + f"Pass it: your distilled summary, your memory scope + "
          f"MEMORY_AGENT_ID, and the checkpoint stub path {stub_path or 'n/a'}. "
          f"It merges the summary into the schema and persists it to "
          f"/memories/<your-scope>/checkpoint.md plus durable WHY-level facts "
          f"via the store's remember endpoint (agent_topic-scoped); if the "
          f"scoped store is unreachable it falls back to filling the stub file "
          f"in place. "
        + _SPAWN_FALLBACK
        + _warn_footer(hard)
    )


def _block_header(ctx: int, hard: int) -> str:
    return (
        f"⚠ CONTEXT SOFT CAP REACHED — {ctx:,} input tokens "
        f"(≥ {hard:,} session budget for this model).\n"
        f"Continuing in this session now risks context poisoning, quota burn, and "
        f"escalating per-turn cost. Execute the checkpoint protocol before yielding:\n\n"
    )


_BLOCK_FOOTER = (
    "3. End your response with exactly:\n"
    "   CHECKPOINT — context cleared.\n"
    "   Resume from: <the checkpoint path you wrote>\n"
    "   Next action: <exact first thing to do on restart>\n"
    "Then instruct the user to run /clear and resume. Resume contract: the "
    "next session reads the checkpoint + at most ONE targeted search — it "
    "must NOT re-read files the checkpoint already summarizes.\n"
    "Do NOT start new substantive work in this session."
)


def block_reason(ctx: int, stub_path: str, hard: int) -> str:
    """HARD instructions, generic variant: stub-file protocol only."""
    return (
        _block_header(ctx, hard)
        + f"1. Write (or update) your semantic checkpoint ({SCHEMA}) by filling "
          f"the stub file at "
          f"{stub_path or '~/.claude/memories/checkpoints/latest.md'} in place. "
          f"If the WARN-time memory-writer already wrote it, update only what "
          f"changed since.\n"
          f"2. If important decisions are not yet durable, fold them into the "
          f"checkpoint's errors-and-fixes section.\n"
        + _BLOCK_FOOTER
    )


def block_reason_scoped(ctx: int, stub_path: str, hard: int) -> str:
    """HARD instructions when a scoped memory layer is installed."""
    return (
        _block_header(ctx, hard)
        + f"1. Write (or update) your semantic checkpoint ({SCHEMA}): "
          f"MEMORY_AGENT_ID=<your-scope> tools/memory-tool.sh rethink "
          f"/memories/<your-scope>/checkpoint.md — if the scoped store is "
          f"unreachable, fill the stub file at "
          f"{stub_path or '~/.claude/memories/checkpoints/latest.md'} in place. "
          f"If the WARN-time memory-writer already wrote it, update only what "
          f"changed since.\n"
          f"2. If important decisions are not yet durable, persist them now via "
          f"the store's remember endpoint (scoped to your agent_topic); "
          f"otherwise fold them into the checkpoint's errors-and-fixes "
          f"section.\n"
        + _BLOCK_FOOTER
    )
