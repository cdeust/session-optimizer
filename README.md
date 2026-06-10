# session-optimizer

Two small, dependency-light tools for keeping long [Claude Code](https://code.claude.com)
sessions **readable, cheap, and un-poisoned**:

1. **`statusline-command.sh`** — a persistent, always-visible two-line status bar
   that color-codes context pressure on a green → yellow → red scale tied to a
   real per-model checkpoint threshold.
2. **`hooks/stop-context-guard.py`** — a `Stop` hook that detects when the
   session crosses that threshold and *performs the checkpoint protocol
   automatically*, so a session never silently grows past the point where it
   starts costing more and reasoning worse.

Together they turn the abstract "watch your context window" advice into an
enforced, visible budget.

---

## Why

A long Claude Code session degrades in four ways as the context window fills:

| Failure mode | What happens |
|---|---|
| **Context poisoning** | Stale, wrong, or superseded content accumulates and biases later reasoning. |
| **Session poisoning** | The session never resets, so early mistakes compound instead of being dropped at a clean boundary. |
| **Quota poisoning** | Every turn re-sends the whole oversized context, burning your 5-hour / 7-day rate-limit budget fast. |
| **Cost** | Per-turn cost scales with context size; the largest-context turns are the most expensive. |

The fix is a disciplined **checkpoint → clear → recall** cycle at a known token
threshold. This repo makes that discipline *visible* (status line) and
*automatic* (hook).

---

## Thresholds

Both tools use the same authoritative per-model budget:

| Model | Checkpoint threshold (WARN) | Session soft cap (HARD) |
|---|---|---|
| Claude Opus 4.8 | ~180K | 200K |
| Claude Sonnet 4.6 | ~180K | 200K |
| Claude Haiku 4.5 | ~120K | 200K (= context window) |

"Context tokens" are measured exactly as Claude Code's own `used_percentage`:
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.

The 200K soft cap is conservative for the 1M-context Opus/Sonnet models — it
keeps sessions focused and checkpointed rather than letting them sprawl toward
the physical limit. Edit the constants if your workflow wants a different budget.

---

## 1. Status line

A persistent two-line bar rendered at the bottom of Claude Code (this is *not*
the SessionStart banner, which prints once and scrolls away — the status line is
always visible).

```
[Opus 4.8] [high+think] · my-project · git:(main)✗ · ⎇feature-x · PR#42
▓▓░░░░░░░░ ctx:20% tokens:200k ⚠ save+recall · $3.50 · ⏱30m0s · 5h:24% 7d:41% · +156/-23
```

- **Line 1** — model, reasoning effort (`+think` when extended thinking is on),
  current directory, git branch + dirty flag, worktree, and PR badge
  (colored by review state).
- **Line 2** — a context progress bar + percentage, token count, session cost,
  duration, rate-limit usage (5h / 7d), and lines added/removed.
- The bar, context %, and token count are colored **green → yellow → red** by the
  per-model threshold above, and a `⚠ save+recall` marker appears once you cross
  the 200K soft cap.

Every segment degrades gracefully when its field is absent (early session, no
git, non-subscriber, no PR, etc.). All colors are readable on a black terminal —
no dim/dark-grey text.

### Install

```bash
cp statusline-command.sh ~/.claude/statusline-command.sh
chmod +x ~/.claude/statusline-command.sh
```

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash ~/.claude/statusline-command.sh",
    "padding": 1,
    "refreshInterval": 10
  }
}
```

`refreshInterval: 10` keeps the time-based segments (duration, cost) current even
while the session is idle (e.g. waiting on background subagents). Requires
[`jq`](https://jqlang.github.io/jq/).

### Test

```bash
echo '{"model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"'"$PWD"'"},"context_window":{"used_percentage":20,"total_input_tokens":200000}}' \
  | bash statusline-command.sh
```

---

## 2. Stop context guard

A `Stop` hook that reads the latest assistant turn's token usage from the
transcript and enforces the budget:

| Level | Trigger | Action |
|---|---|---|
| below WARN | `ctx < threshold` | nothing — silent, no side effects |
| **WARN** | `ctx ≥ 180K` (120K Haiku) | captures **mechanical state for free** (date, model, token count, branch, last commit, modified files) as a summary-schema stub at `~/.claude/memories/checkpoints/{session_id,latest}.md`, and **blocks the stop exactly once as a reflection pause**: the model distills the session and hands the summary to the `memory-writer` subagent, which merges it into the stub — then the session **continues**. |
| **HARD** | `ctx ≥ 200K` | captures state **and blocks the stop exactly once**, injecting the checkpoint-finalize procedure so the model completes the checkpoint file (normally a formality — the WARN reflection already wrote it) and signals you to `/clear` + resume from the checkpoint. |

### Safety properties

- **Loop-safe** — honors `stop_hook_active`; each level fires at most once per
  session via a `/tmp` state file, so the hard block can never loop.
- **Non-fatal by construction** — any parse/IO error exits `0`. A `Stop` hook
  must never wedge a session.
- The status line is the passive visual warning; this hook is the active
  enforcement layer.

### The memory-writer subagent

The checkpoint itself is written by a **normal Claude Code subagent** —
`agents/memory-writer.md`, in the standard format produced by the `/agents`
create flow (frontmatter: `name`, `description`, `tools`, `model`; body =
system prompt). The pair follows the
[letta](https://github.com/letta-ai/letta) sleeptime pattern: a primary agent
plus a budgeted memory-manager agent that operates **on the memory store**
with memory verbs, on a cheap model (Haiku). The parent session distills its
own state into the summary schema (goals / file references / errors and fixes
/ current state / next steps, ≤500 words) — spending its expensive context
once on distillation, never on persistence plumbing — and the memory-writer
persists it. It writes exactly what it is given and never invents content.

Persistence is layered, primary first:

1. **Scoped memory store** (the [`zetetic-team-subagents`](https://github.com/cdeust/zetetic-team-subagents)
   memory layer): `memory-tool.sh rethink /memories/<scope>/checkpoint.md`
   for the checkpoint, plus one `cortex:remember` entry per durable WHY-level
   fact (decisions, rejected approaches, lessons), agent_topic-scoped.
2. **Stub-file fallback**: with no memory layer installed, the agent fills
   the hook's mechanical stub in place under
   `~/.claude/memories/checkpoints/` and mirrors it to `latest.md`. The hook
   never blocks on the memory layer's absence — graceful degradation, not a
   dependency.

### Install

```bash
mkdir -p ~/.claude/hooks ~/.claude/agents
cp hooks/stop-context-guard.py ~/.claude/hooks/stop-context-guard.py
chmod +x ~/.claude/hooks/stop-context-guard.py
cp agents/memory-writer.md ~/.claude/agents/memory-writer.md
```

(Equivalently, create the `memory-writer` agent interactively with `/agents`
— new agent, name `memory-writer`, tools `Read, Write, Edit`, model Haiku —
and paste the body of `agents/memory-writer.md` as its system prompt. The
hook only needs an agent with that name to exist; it spawns it through the
native Agent tool like any other subagent.)

Register the `Stop` hook (see `hooks/hooks.example.json`) in your plugin's
`hooks/hooks.json` or in `~/.claude/settings.json`, pointing at the installed
path. Requires Python 3.

**Or install as a plugin.** This repo ships a `.claude-plugin/plugin.json`
(v1.0.0) that wires the `Stop` guard automatically — add the repo as a
marketplace and install it, no manual hook registration needed.

### Test

```bash
T=$(mktemp); printf '{"message":{"model":"claude-opus-4-8","usage":{"input_tokens":105000,"cache_read_input_tokens":100000,"cache_creation_input_tokens":0}}}\n' > "$T"
echo '{"session_id":"demo","transcript_path":"'"$T"'","cwd":"'"$PWD"'","stop_hook_active":false}' \
  | python3 hooks/stop-context-guard.py | python3 -m json.tool
rm -f "$T"
```

Expected: a `decision: block` payload with the checkpoint procedure.

---

## License

[MIT](LICENSE) © Clement Deust
