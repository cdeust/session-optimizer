# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-07-26

Statusline only. No change to context-guard or refine-gate.

### BREAKING

- **`statusline-costs.py` is removed.** Every dollar figure now comes from one
  ledger, `costs.sh` over `~/.claude/statusline-costs.jsonl`. The Python
  aggregator summed every assistant line of every transcript and over-counted
  ~2.2x, because Claude Code re-logs one API response 2-3 times (streaming /
  tool continuation); a correct total must deduplicate on
  `message.id:requestId` before pricing. Measured over 172 local transcripts:
  $3438.84 deduped vs $7645.04 raw. `costs.sh` also prices the full recursive
  `<transcript>/subagents/` subtree, so Task, worktree-isolated and workflow
  agents are all billed — none of them appear in Claude Code's own
  `.cost.total_cost_usd`. Installs carrying a stale `~/.claude/statusline-costs.py`
  should delete it; the SessionStart hook now does so.
- **The renderer ships as a directory.** `statusline-command.sh` is a
  composition root that sources `statusline-lib/*.sh`, which must be installed
  next to it. A missing module is a hard failure that names the file rather
  than a partial statusline. `$STATUSLINE_LIB` overrides the location.

### Added

- `costs.sh` + `pricing.json` as bundled assets (the ledger and its prices).
- Terminal-width fitting: each line is held to 85% of the probed width, and
  `fit_line` drops whole trailing segments — lowest priority first — rather
  than letting the host truncate mid-word and cost the block a row. Width is
  probed from the controlling tty, `$COLUMNS`, then `tput cols` (only when
  stdout is a terminal); `$STATUSLINE_COLS` overrides.
- Verbosity preset now falls back to `s` below 90 columns. The config's
  `"size"` is a preference capped by width; `$STATUSLINE_SIZE` remains a hard
  pin honoured at any width.
- **Pace** on both rate-limit windows: used% over the share of the window
  elapsed, which is the linear projection of usage at reset (`1.0x` lands
  exactly on the cap). The percentage carries the worse of the absolute and
  pace severities; the pace figure carries its own. Nothing is printed below
  10% of the window elapsed, where the extrapolation is not informative.
- Tests: `tests/statusline/test_fit_and_pace.sh` (46 tests — fitting, pace,
  severity, the width probe, preset resolution, the module loader's failure
  path, and the §4.1 size cap), `tests/statusline/measure_widths.sh`
  (per-preset width measurement).
- CI now shellchecks `tests/statusline/*.sh` as well as the assets. A checker
  that skips the suites lets the code guarding the renderer rot unwatched.

### Changed

- The identity line renders `model | dir | effort | thinking`. Segment order is
  priority order under width fitting, and the previous order put `dir` last,
  which made the working directory the first thing dropped on a narrow
  terminal.
- The renderer is split into ten modules, one concern per file, none over 500
  lines (`rules/coding-standards.md` §4.1; the single file had reached 1022).
  Behaviour-preserving: verified byte-identical across a 34-configuration
  preset x width golden render.
- Docs: both READMEs described an emoji-based layout that the word-based
  design-system rendering had already replaced.

### Fixed

- `stat -f` is BSD-only; every mtime read now goes through `file_mtime`, which
  tries the BSD and GNU spellings. On Linux the bare call silently read 0,
  forcing a cache refresh on every invocation.
- A non-numeric `used_percentage` (`"n/a"`) rendered as a healthy 0%: awk reads
  an unquoted non-numeric token as an uninitialised variable. Values are now
  validated before awk sees them.
- `tput cols` returns terminfo's blind 80 when stdout is a pipe, which is how
  the host captures the renderer — it is now consulted only when stdout is a
  terminal, so IDE and web sessions no longer silently downgrade.
- A non-numeric `$STATUSLINE_COLS` was printed straight through, breaking every
  arithmetic width comparison downstream. The override is an escape hatch, not
  an exemption from `probe_cols`'s postcondition: an invalid value now falls
  through to the probes.
- The terminal-size probe leaked `Device not configured` on every refresh with
  no controlling tty: the failing redirection is reported by the shell itself,
  so the whole group is now redirected, not just the command.

## [2.0.0] - 2026-07-22

### BREAKING

- The monolithic `session-optimizer` plugin is split into three
  independently installable plugins, shipped from the same marketplace:
  - **context-guard** — `Stop`-hook context budget with a per-model
    checkpoint protocol, budgeted `memory-writer` checkpoint subagent, and
    a `SubagentStop` spend tracker.
  - **refine-gate** — `UserPromptSubmit` prompt-binding gate + `/refine`
    skill.
  - **statusline** — multi-line status bar with RGB-gradient context bars,
    cost tracking, telemetry, and rate-limit gauges.
- The root `session-optimizer` plugin remains **only as a deprecation
  shim**: it registers no functional hooks and just announces the
  migration at session start.

### Added

- Runtime Cortex detection: the checkpoint protocol uses a generic,
  vanilla-Claude-Code wording by default and switches to the scoped memory
  layer only when it is detected as installed.
- Statusline install skill (`/plugin install statusline@...`, then ask
  Claude to "install the statusline") plus an auto-update hook that keeps
  the installed copy in sync with the plugin's bundled assets.
- CI (`.github/workflows/ci.yml`): runs all three test suites, shellchecks
  the statusline renderer, and validates every plugin/hook/marketplace
  JSON.
- Privacy policy (`PRIVACY.md`), as required by the plugin Directory
  Policy.

### Changed

- Hook registration is single-sourced in each plugin's `hooks/hooks.json`
  (no duplicate definitions in `plugin.json`).
- Statusline documentation translated to English.
- Statusline renderer is shellcheck-clean at full severity; remaining
  suppressions are justified inline.

### Migration from 1.x

1. Install the plugins you actually use (any subset): `context-guard`,
   `refine-gate`, `statusline`.
2. Uninstall the old plugin: `/plugin uninstall session-optimizer`.
3. Your `~/.claude/ctxguard-thresholds.json`, checkpoint files, and
   statusline config are untouched — the new plugins read the same paths.
4. If you had installed the `memory-writer` agent manually into
   `~/.claude/agents/`, you can remove it; context-guard ships its own
   copy (`context-guard:memory-writer`).

## [1.4.3] and earlier

Releases up to `v1.4.3` shipped the monolithic `session-optimizer` plugin.
See the git tags (`v1.0.0` … `v1.4.3`) for their history.
