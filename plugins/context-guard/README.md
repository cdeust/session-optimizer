# context-guard

A context-budget guard for long [Claude Code](https://code.claude.com)
sessions: a `Stop` hook that detects when the session crosses a per-model
token threshold and *performs the checkpoint protocol automatically*, plus a
`SubagentStop` tracker that surfaces the subagent spend the main thread's
context measurement structurally cannot see.

## Why

A long Claude Code session degrades in four ways as the context window fills:

| Failure mode | What happens |
|---|---|
| **Context poisoning** | Stale, wrong, or superseded content accumulates and biases later reasoning. |
| **Session poisoning** | The session never resets, so early mistakes compound instead of being dropped at a clean boundary. |
| **Quota poisoning** | Every turn re-sends the whole oversized context, burning your 5-hour / 7-day rate-limit budget fast. |
| **Cost** | Per-turn cost scales with context size; the largest-context turns are the most expensive. |

The fix is a disciplined **checkpoint → clear → recall** cycle at a known
token threshold. This plugin makes that discipline *automatic*.

## Install

```
/plugin marketplace add cdeust/session-optimizer
/plugin install context-guard@session-optimizer-marketplace
```

The plugin wires the `Stop` and `SubagentStop` hooks and ships the
`memory-writer` agent automatically. Requires Python 3.

## Thresholds — shared with the statusline plugin

Thresholds are loaded from `~/.claude/ctxguard-thresholds.json` — a
**shared-file convention** with the [statusline](../statusline) plugin, so
the passive display (bar colors) and the active enforcement (this hook) stay
on par by construction. The statusline install skill seeds the file; without
it, this embedded fallback applies:

```json
{
  "models": [
    { "match": "fable",  "warn": 120000, "hard": 160000 },
    { "match": "mythos", "warn": 120000, "hard": 160000 },
    { "match": "haiku",  "warn": 120000, "hard": 170000 },
    { "match": "sonnet", "warn": 180000, "hard": 200000 },
    { "match": "opus",   "warn": 180000, "hard": 200000 }
  ],
  "default": { "warn": 180000, "hard": 200000 }
}
```

First substring match against the lowercased model id wins. "Context tokens"
are measured exactly as Claude Code's own `used_percentage`:
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
The 200K soft cap is conservative for the 1M-context Opus/Sonnet models — it
keeps sessions focused and checkpointed rather than letting them sprawl.

## The Stop guard

| Level | Trigger | Action |
|---|---|---|
| below WARN | `ctx < threshold` | nothing — silent, no side effects |
| **WARN** | `ctx ≥ warn` | captures **mechanical state for free** (date, model, token count, branch, last commit, modified files) as a summary-schema stub at `~/.claude/memories/checkpoints/{session_id,latest}.md`, and **blocks the stop exactly once as a reflection pause**: the model distills the session and hands the summary to the `memory-writer` subagent, which merges it into the stub — then the session **continues**. |
| **HARD** | `ctx ≥ hard` | captures state **and blocks the stop exactly once**, injecting the checkpoint-finalize procedure so the model completes the checkpoint file (normally a formality — the WARN reflection already wrote it) and signals you to `/clear` + resume from the checkpoint. |

### Safety properties

- **Loop-safe** — honors `stop_hook_active`; each level fires at most once per
  session via a `/tmp` state file, so the hard block can never loop.
- **Non-fatal by construction** — any parse/IO error exits `0`. A `Stop` hook
  must never wedge a session.

## The memory-writer subagent

The checkpoint itself is written by a **normal Claude Code subagent** —
`agents/memory-writer.md` (frontmatter: `name`, `description`, `tools`,
`model`; body = system prompt). The pair follows the
[letta](https://github.com/letta-ai/letta) sleeptime pattern: a primary agent
plus a budgeted memory-manager agent on a cheap model (Haiku). The parent
session distills its own state into the summary schema (goals / file
references / errors and fixes / current state / next steps, ≤500 words) —
spending its expensive context once on distillation, never on persistence
plumbing — and the memory-writer persists it. It writes exactly what it is
given and never invents content.

### Persistence: generic by default, memory layer by detection

- **Default (vanilla Claude Code)** — the agent fills the hook's mechanical
  stub in place under `~/.claude/memories/checkpoints/` and mirrors it to
  `latest.md`. No extra tooling required; every instruction the hook emits
  references only tools that exist in vanilla Claude Code.
- **Scoped memory store (runtime-detected)** — when a scoped memory layer is
  installed (`tools/memory-tool.sh` in the project root or
  `~/.claude/tools/`), the hook detects it per invocation and switches the
  protocol: the checkpoint goes to the store's working-state block
  (`memory-tool.sh rethink /memories/<scope>/checkpoint.md`) and durable
  WHY-level facts (decisions, rejected approaches, lessons) are stored via
  the store's remember endpoint. Users without the layer never see this
  wording — graceful extension, not a dependency.

The message composition lives in `hooks/checkpoint_protocol.py`
(`detect_memory_tool` + one generic and one scoped variant per level).

### Test the guard

```bash
T=$(mktemp); printf '{"message":{"model":"claude-opus-4-8","usage":{"input_tokens":105000,"cache_read_input_tokens":100000,"cache_creation_input_tokens":0}}}\n' > "$T"
echo '{"session_id":"demo","transcript_path":"'"$T"'","cwd":"'"$PWD"'","stop_hook_active":false}' \
  | python3 hooks/stop-context-guard.py | python3 -m json.tool
rm -f "$T"
```

Expected: a `decision: block` payload with the checkpoint procedure.

## Subagent usage tracker

The `Stop` guard (and any statusline) only sees the **main thread**. Work
done by Task-tool subagents is logged as separate per-agent transcripts that
a parent-only reader never sees — the documented gap in
[anthropics/claude-code#32175](https://github.com/anthropics/claude-code/issues/32175)
and [ryoppippi/ccusage#313](https://github.com/ryoppippi/ccusage/issues/313).
On disk (verified layout):

```
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl                 # main thread
~/.claude/projects/<encoded-cwd>/<session-id>/subagents/
    agent-<agentId>.jsonl                                           # subagent turns (isSidechain:true)
    agent-<agentId>.meta.json  -> {agentType, description, toolUseId}
    workflows/wf_*/agent-*.jsonl                                    # workflow subagents
```

Three layers recover that data:

- **`SubagentStop` hook** (`hooks/subagent-tracker.py`) — parses each finishing
  subagent's transcript (deduped by `message.id`, billed per the record's own
  `message.model`, cache split by TTL), reads the sibling `.meta.json` for
  `agentType`/`description`, and folds it into a per-session aggregate at
  `/tmp/zetetic-subagents-<session_id>.json` (keyed by `agentId`, so re-firing
  updates rather than double-counts).
- **The [statusline](../statusline) plugin** — reads that aggregate and shows
  `🤖N · tokens · $cost` so live subagent spend is visible alongside the main
  thread (another shared-file convention between the two plugins).
- **Stop guard** — surfaces cumulative session spend (main + subagents) in the
  checkpoint message and stub. The context-window *decision* stays
  main-thread-only (mixing in subagent tokens would mis-trigger checkpoints);
  only the reported figure is enriched.

### Pricing (sourced)

Token cost uses Anthropic's published per-MTok rates and cache multipliers
(Opus 4.8 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5, Fable 5 $10/$50; cache
write 1.25× input at 5m / 2× at 1h, cache read 0.1×). Every constant cites its
source at the use site in `tools/subagent_usage.py`.

### Retrospective report

`tools/subagent_usage.py` doubles as a CLI that parses **all** subagent
transcripts across a project and reports cost grouped by `agent_type`:

```bash
python3 tools/subagent_usage.py [/path/to/project]   # human table
python3 tools/subagent_usage.py --json               # machine-readable
```

### Test the tracker

```bash
SID="demo-$(date +%s)"
echo '{"session_id":"'"$SID"'","transcript_path":"<a real agent-*.jsonl path>","cwd":"'"$PWD"'"}' \
  | python3 hooks/subagent-tracker.py
cat "/tmp/zetetic-subagents-$SID.json" | python3 -m json.tool
```

## Manual install (without the plugin system)

```bash
mkdir -p ~/.claude/hooks ~/.claude/agents
cp hooks/stop-context-guard.py hooks/checkpoint_protocol.py ~/.claude/hooks/
chmod +x ~/.claude/hooks/stop-context-guard.py
cp agents/memory-writer.md ~/.claude/agents/memory-writer.md
```

`stop-context-guard.py` imports `checkpoint_protocol.py` from its own
directory and `subagent-tracker.py` imports the shared core from the sibling
`tools/` directory — keep each pair together. Then register the `Stop` /
`SubagentStop` entries (see `hooks/hooks.json`) in `~/.claude/settings.json`,
pointing at the installed paths.

## License

[MIT](../../LICENSE) © Clement Deust
