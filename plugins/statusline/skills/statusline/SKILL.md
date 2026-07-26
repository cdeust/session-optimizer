---
name: statusline
description: Install or update the session-optimizer statusline for Claude Code. Use when the user asks to "install the statusline", "set up the status bar", "configure the statusline", "update the statusline", or wants git/context/cost/rate-limit/telemetry info in the Claude Code status bar.
version: 2.1.0
---

# Statusline install

Installs the multi-line statusline that shows model + git context, a discrete
heat-track context bar tied to per-model checkpoint thresholds, session cost and
duration from one deduplicated ledger, 5h/7d rate-limit gauges with burn-rate
pacing, per-session telemetry (tok/s, compactions, prompt-cache countdown), and
live subagent activity — every line fitted to the terminal width.

The plugin **bundles** its assets under `assets/`:

| File | Role | Update policy |
|---|---|---|
| `statusline-command.sh` | Renderer entry point — the composition root, called by Claude Code every refresh | overwrite on update (with backup) |
| `statusline-lib/*.sh` | The renderer's modules, one concern per file (see below) | overwrite the whole directory on update |
| `costs.sh` | Cost ledger CLI — the single source of every dollar figure | overwrite on update (with backup) |
| `pricing.json` | Per-model token prices `costs.sh` prices with | overwrite on update (with backup) |
| `statusline-transcript.py` | Per-session telemetry, backgrounded on a 15 s TTL | overwrite on update (with backup) |
| `statusline-budget.json` | **Personal** config: display size, cache TTL | copy only if absent — never overwrite |
| `ctxguard-thresholds.json` | Per-model checkpoint thresholds, **shared** with the context-guard plugin | copy only if absent — never overwrite |

The renderer is a composition root plus a module directory. `statusline-lib/`
must be installed **next to** `statusline-command.sh`: the script resolves its
modules relative to its own path, and exits with a message naming the missing
file if any is absent (`$STATUSLINE_LIB` overrides the location).

| Module | Single responsibility |
|---|---|
| `platform.sh` | BSD/GNU spelling differences (`stat`, `date`) |
| `palette.sh` | Colour tokens, the heat-track bar |
| `fit.sh` | Visible-width measurement and trimming |
| `severity.sh` | The one ok/warn/danger scale and its thresholds |
| `format.sh` | Numbers and times as the reader sees them |
| `config.sh` | The two JSON config files |
| `gitctx.sh` | The repository facts |
| `session_state.sh` | Cost ledger, transcript telemetry, subagent tracker |
| `layout.sh` | Terminal width probe, verbosity preset |
| `render.sh` | One function per status line |

A `SessionStart` hook (`hooks/hooks.json`) injects a short maintenance
instruction each session start so Claude reconciles the CODE assets into
`~/.claude` when the plugin updates — the two config files are never touched
automatically.

> **Superseded:** `statusline-costs.py` was removed in 2.1.0. It summed every
> assistant line of every transcript and over-counted spend ~2.2x, because
> Claude Code re-logs one API response 2-3 times (streaming / tool
> continuation). `costs.sh` deduplicates on `message.id:requestId` before
> pricing. An install that still has `~/.claude/statusline-costs.py` should
> delete it — nothing reads it any more.

## Requirements

- `jq` — JSON parsing in the renderer (**required**)
- `python3` — per-session telemetry (**required**; cost aggregation is `costs.sh`, pure bash + jq)
- `git` — repository context (optional; segment degrades gracefully)

## Instructions for Claude

When the user asks to install, update, or set up the statusline, follow
these steps **in order**:

### Step 1 — Pre-flight checks

```bash
preflight_ok=true
for cmd in jq python3; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "BLOCKING: '$cmd' not found"; preflight_ok=false; }
done
command -v git >/dev/null 2>&1 || echo "OPTIONAL: 'git' not found — git segment will stay empty"
mkdir -p ~/.claude 2>/dev/null
if ! touch ~/.claude/.write-test 2>/dev/null; then
  echo "BLOCKING: cannot write to ~/.claude/"; preflight_ok=false
else
  rm -f ~/.claude/.write-test
fi
if [ -f ~/.claude/settings.json ] && [ ! -w ~/.claude/settings.json ]; then
  echo "BLOCKING: ~/.claude/settings.json not writable"; preflight_ok=false
fi
echo "preflight_ok=$preflight_ok"
```

| Failure | Fix |
|---|---|
| `jq` missing | `brew install jq` (macOS) · `apt install jq` (Linux/WSL) |
| `python3` missing | `brew install python` · `apt install python3` |
| `~/.claude/` not writable | check ownership: `ls -la ~/` |

If any **BLOCKING** check fails, stop and tell the user what to fix.

### Step 2 — Place the assets

Do this with your **Read/Write tools**:

1. Locate the bundled `assets/` dir: `find ~/.claude/plugins -type d -path '*/statusline/*/assets' 2>/dev/null | head -1` (dev checkout of this repo: `plugins/statusline/assets/` directly).
2. **Code assets** (`statusline-command.sh`, `costs.sh`, `pricing.json`, `statusline-transcript.py`): for each, if a copy already exists in `~/.claude` and differs, back it up as `~/.claude/<name>.bak.<timestamp>`, then write the bundled version to `~/.claude/<name>`.
3. **The module directory** (`statusline-lib/`): create `~/.claude/statusline-lib/` and write every bundled `*.sh` into it. Install the whole directory, not a subset — the renderer refuses to start if one module is missing, and a version-skewed module is worse than an absent one. Delete any `~/.claude/statusline-lib/*.sh` the bundle no longer ships.
4. **Config assets** (`statusline-budget.json`, `ctxguard-thresholds.json`): copy to `~/.claude/<name>` **only if the file does not exist yet** — these hold user-tuned values and must never be overwritten.
5. Set the execute bit: `chmod +x ~/.claude/statusline-command.sh ~/.claude/costs.sh`.
6. **Remove the superseded aggregator** if present: `rm -f ~/.claude/statusline-costs.py` (back it up first if it differs from the last shipped copy). It over-counted spend ~2.2x and nothing reads it any more.

### Step 3 — Configure settings

Use the Edit tool to set `statusLine` in `~/.claude/settings.json`:

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

`refreshInterval: 10` keeps the time-based segments (duration, cost)
current while the session is idle.

### Step 4 — Post-install verification

```bash
for f in statusline-command.sh costs.sh pricing.json statusline-transcript.py statusline-budget.json ctxguard-thresholds.json; do
  [ -f ~/.claude/$f ] && echo "OK: $f present" || echo "ERROR: $f missing"
done
for m in platform palette fit severity format config gitctx session_state layout render; do
  [ -r ~/.claude/statusline-lib/$m.sh ] && echo "OK: module $m.sh present" || echo "ERROR: module $m.sh missing"
done
[ -f ~/.claude/statusline-costs.py ] && echo "WARN: superseded statusline-costs.py still present — delete it" || echo "OK: no superseded aggregator"
[ -x ~/.claude/statusline-command.sh ] && echo "OK: renderer executable" || echo "ERROR: renderer not executable"
[ -x ~/.claude/costs.sh ] && echo "OK: ledger executable" || echo "ERROR: costs.sh not executable"
jq -e '.statusLine.command' ~/.claude/settings.json >/dev/null 2>&1 \
  && echo "OK: statusLine registered in settings.json" \
  || echo "ERROR: statusLine not found in settings.json"
echo '{"model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"'"$PWD"'"},"context_window":{"used_percentage":20,"total_input_tokens":200000}}' \
  | bash ~/.claude/statusline-command.sh >/dev/null && echo "OK: renderer runs" || echo "ERROR: renderer failed"
```

If any check fails, diagnose and fix before telling the user to restart.

### Step 5 — Tell the user to restart Claude Code

Summarize what was done (installed/updated, backups created, config files
seeded or preserved) and ask them to restart Claude Code. Mention:

- display size is tunable via `STATUSLINE_SIZE` (`xs`/`s`/`m`/`l`/`xl`) or
  the `"size"` field of `~/.claude/statusline-budget.json`;
- `~/.claude/ctxguard-thresholds.json` is shared with the **context-guard**
  plugin — editing it moves both the bar's color thresholds and the Stop
  guard's checkpoint triggers, so the two layers stay on par by construction;
- future code updates apply automatically via the SessionStart hook when
  the plugin version increases.
