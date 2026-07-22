# session-optimizer

<p align="center"><img src="assets/banner.svg" alt="session-optimizer — a visible, enforced context budget for long Claude Code sessions" width="100%"/></p>

Three small, dependency-light, **independently installable** plugins for
keeping long [Claude Code](https://code.claude.com) sessions **readable,
cheap, and un-poisoned** — shipped from one marketplace. Install exactly the
ones you want; none requires the others.

```
/plugin marketplace add cdeust/session-optimizer
```

| Plugin | One-line install | What it does |
|---|---|---|
| [**context-guard**](plugins/context-guard) | `/plugin install context-guard@session-optimizer-marketplace` | A `Stop` hook enforces a per-model context budget: at the WARN threshold it writes a mechanical checkpoint stub and delegates persistence to a budgeted `memory-writer` subagent as a reflection pause; at the hard cap it forces checkpoint → `/clear` → resume. A `SubagentStop` tracker surfaces true session spend (main thread + subagents). |
| [**refine-gate**](plugins/refine-gate) | `/plugin install refine-gate@session-optimizer-marketplace` | A `UserPromptSubmit` hook + `/refine` skill that bind vague prompt references ("the SSE solution", "like before", "still broken") to concrete artifacts with evidence, then select an execution strategy from a research-backed table — before any code is touched. |
| [**statusline**](plugins/statusline) | `/plugin install statusline@session-optimizer-marketplace` | A multi-line status bar: RGB-gradient context bar tied to per-model checkpoint thresholds, cost, telemetry (tok/s, compactions, cache countdown), rate-limit gauges, live subagent spend. Ships an install skill — after installing, ask Claude to "install the statusline" and it wires everything. |

## Why

A long Claude Code session degrades in four ways as the context window fills:

| Failure mode | What happens |
|---|---|
| **Context poisoning** | Stale, wrong, or superseded content accumulates and biases later reasoning. |
| **Session poisoning** | The session never resets, so early mistakes compound instead of being dropped at a clean boundary. |
| **Quota poisoning** | Every turn re-sends the whole oversized context, burning your 5-hour / 7-day rate-limit budget fast. |
| **Cost** | Per-turn cost scales with context size; the largest-context turns are the most expensive. |

The fix is a disciplined **checkpoint → clear → recall** cycle at a known
token threshold, plus prompts whose references are bound before work starts.
These plugins make that discipline *visible* (statusline), *automatic*
(context-guard), and *cheap to get right* (refine-gate).

## How the plugins cooperate (without depending on each other)

- **Shared thresholds** — context-guard's Stop hook and the statusline's bar
  colors both read `~/.claude/ctxguard-thresholds.json` (first substring
  match on the model id wins; each has an embedded fallback). One file, two
  consumers: passive display and active enforcement stay on par by
  construction. Documented in both plugins' READMEs.
- **Subagent spend** — context-guard's `SubagentStop` tracker maintains a
  per-session aggregate in `/tmp`; the statusline shows it live when both
  are installed, and stays silent otherwise.
- **No hard dependencies** — every integration degrades gracefully when the
  other plugin (or an optional memory layer) is absent. The checkpoint
  protocol's default wording references only tools that exist in vanilla
  Claude Code; a scoped memory store is detected at runtime and used only
  when installed.

## Repository layout

```
.claude-plugin/marketplace.json   # the marketplace (three plugins + deprecated meta shim)
plugins/
  context-guard/                  # Stop guard + memory-writer agent + SubagentStop tracker
  refine-gate/                    # UserPromptSubmit gate + /refine skill
  statusline/                     # renderer + helpers under assets/, install skill, auto-update hook
tests/                            # the three suites, run from the repo root
```

Each plugin carries its own `.claude-plugin/plugin.json`, `hooks/hooks.json`,
and README.

## Migrating from session-optimizer v1.x

Up to v1.4.3 this repo shipped one monolithic `session-optimizer` plugin. In
v2.0.0 it split into the three plugins above; the root `session-optimizer`
plugin remains **only as a deprecation shim** — it registers no functional
hooks and just announces the migration at session start.

1. Install the plugins you actually use (any subset):
   `context-guard`, `refine-gate`, `statusline`.
2. Uninstall the old plugin: `/plugin uninstall session-optimizer`.
3. Your `~/.claude/ctxguard-thresholds.json`, checkpoint files, and
   statusline config are untouched — the new plugins read the same paths.
4. If you had installed the `memory-writer` agent manually into
   `~/.claude/agents/`, you can remove it; the context-guard plugin ships
   its own copy (`context-guard:memory-writer`).

## Tests

```bash
pytest tests/test_refine_gate.py tests/test_subagent_usage.py
bash tests/statusline/test_heat_rgb.sh
```

CI (`.github/workflows/ci.yml`) runs all three suites, shellchecks the
statusline renderer, and validates every plugin/hook/marketplace JSON.

## License

[MIT](LICENSE) © Clement Deust
