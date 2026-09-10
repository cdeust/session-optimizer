---
name: statusline
description: Install or update the session-optimizer statusline for Claude Code. Use when the user asks to "install the statusline", "set up the status bar", "configure the statusline", "update the statusline", or wants git/context/cost/rate-limit/telemetry info in the Claude Code status bar.
version: 2.2.0
---

# Statusline install

Installs the multi-line statusline that shows model + git context, a discrete
heat-track context bar tied to per-model checkpoint thresholds, session cost and
duration from one deduplicated ledger, 5h/7d rate-limit gauges with burn-rate
pacing, per-session telemetry (tok/s, compactions, prompt-cache countdown), and
live subagent activity, every line fitted to the terminal width.

The plugin ships **one installer**, `install.sh` at the plugin root. It is the
only thing that writes the statusline's files, the SessionStart hook runs the
same script in `sync` mode after every `plugin update`, and it is deterministic:
no file is compared or copied by hand.

## What ends up on disk

```
~/.claude/statusline/                 the whole install, one directory
  statusline-command.sh               renderer entry point (composition root)
  lib/*.sh                            its modules, one concern per file
  costs.sh  pricing.json              cost ledger CLI and the prices it uses
  transcript.py                       per-session telemetry, backgrounded (15 s TTL)
  README.md                           this plugin's README
  statusline-budget.json              PERSONAL config: seeded once, never overwritten
  state/                              everything written at runtime
    costs.jsonl                       the ledger (+ .lock, .cleanup-stamp while running)
    sessions/<session>.main|.sub      per-session price caches, 30-day retention
    transcript-cache.json             telemetry cache
    backup/<timestamp>/               superseded files, newest 3 runs kept
~/.claude/ctxguard-thresholds.json    SHARED with context-guard: stays at the root
```

`ctxguard-thresholds.json` is the one file kept at the root of `~/.claude`:
the context-guard plugin's Stop hook reads that exact path, and a file with two
readers lives where both find it. Seeded if absent, never overwritten.

An earlier install (everything flat at the root of `~/.claude`) is
migrated once by the same script: files are **moved**, not copied, the ledger
and its per-session caches keep their content and timestamps, and the root is
left to Claude Code.

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
| `pricing.sh` | The ledger's pricing engine (`costs.sh` module): `pricing.json`, the jq programs, the per-session caches |
| `ledger_report.sh` | The ledger's `info`, `debug` and `init` verbs (`costs.sh` module) |

## Requirements

- `jq` (JSON parsing in the renderer, **required**)
- `python3` (per-session telemetry, **required**; the ledger itself is bash + jq)
- `git` (repository context, optional; the segment degrades gracefully)

## Instructions for Claude

When the user asks to install, update, or set up the statusline, follow
these steps **in order**. Use the Bash tool; do not copy files yourself.

### Step 1: locate the installer

```bash
root="${CLAUDE_PLUGIN_ROOT:-}"
[ -f "$root/install.sh" ] || root=$(find ~/.claude/plugins/cache -type f -path '*/statusline/*/install.sh' 2>/dev/null | sort | tail -1 | xargs -I{} dirname {})
[ -f "$root/install.sh" ] || root="$PWD/plugins/statusline"   # dev checkout of this repo
[ -f "$root/install.sh" ] && echo "installer: $root/install.sh" || echo "BLOCKING: install.sh not found"
```

If the installer cannot be found, stop and tell the user to (re)install the
plugin from the marketplace.

### Step 2: run it

```bash
bash "$root/install.sh" install
```

The script runs its own pre-flight (`jq`, `python3`, a writable `~/.claude`
and `settings.json`), migrates a flat install if one is present, places the
code, seeds the two config files only when absent, points
`settings.json` `statusLine.command` at
`bash ~/.claude/statusline/statusline-command.sh` while preserving any
existing `padding` / `refreshInterval`, prunes the backup history to the
newest 3 runs, and ends with the verification below. Every line it prints
starting with `[statusline]` is a change it made; a `BLOCKING:` line names
what to fix.

| Failure | Fix |
|---|---|
| `jq` missing | `brew install jq` (macOS) or `apt install jq` (Linux/WSL) |
| `python3` missing | `brew install python` or `apt install python3` |
| `~/.claude/` not writable | check ownership: `ls -la ~/` |
| `settings.json is not valid JSON` | fix the file by hand, then rerun; the installer never overwrites an unparseable settings file |

### Step 3: read the verification

`install` ends with `bash "$root/install.sh" verify` (rerun it on its own at
any time). Every line is `OK:` or `ERROR:`; the verb exits 1 on the first
error, and the last check renders a synthetic status line through the
installed copy. Diagnose and fix before telling the user to restart.

### Step 4: tell the user to restart Claude Code

Summarize what the installer reported (installed, updated, migrated, seeded,
what went to `state/backup/`) and ask them to restart Claude Code. Mention:

- display size is tunable via `STATUSLINE_SIZE` (`xs`/`s`/`m`/`l`/`xl`) or
  the `"size"` field of `~/.claude/statusline/statusline-budget.json`;
- `~/.claude/ctxguard-thresholds.json` is shared with the **context-guard**
  plugin: editing it moves both the bar's colour thresholds and the Stop
  guard's checkpoint triggers, so the two layers stay on par by construction;
- code updates apply automatically at session start once the plugin version
  increases (`install.sh sync`), and never touch the two config files;
- `~/.claude/statusline/costs.sh debug` shows the ledger, its per-session
  caches and the retention window.
