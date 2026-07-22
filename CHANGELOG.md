# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
