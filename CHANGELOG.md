# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### disk-hygiene 0.1.1

#### Fixed

- `evidence-preserved` no longer judges durability by the environment of the
  process that runs it. The Claude home, Claude temp directory and Codex home
  are recorded when a worktree or temporary directory is registered, the check
  reads them from the record, and a process running under other roots is
  refused with the differing names. A file under the host's home, which session
  end removes, could be accepted as durable when `CLAUDE_CONFIG_DIR` or
  `CODEX_HOME` differed from the host's. A path registered before roots were
  recorded (by the installed 0.1.1 copy) has none and is refused at
  `evidence-preserved` and at `dispose`, with a message naming the path, until
  its owning session runs `register-worktree` for it again; on a path it
  already owns, that command records the roots and drops the marked evidence
  (`evidence`, `evidence_sha256`, `evidence_head`), keeping the PR link, so
  `evidence-preserved` must mark it again under the recorded roots. Taking the
  roots of the first `evidence-preserved` call instead, or keeping evidence
  marked by 0.1.1, would have trusted a judgement made under the caller's
  roots, the failure this entry fixes: a session checkpoint marked by 0.1.1
  and re-registered was disposed on, although session end removes it.
  Every earlier registration still on disk meets this refusal once the fix is
  installed (two live worktrees in the author's ledger); one whose owner has
  ended stays reported until its worktree and branch are removed by hand. The
  entries of earlier registrations whose directory is already gone (six there)
  hold nothing to protect and are dropped by `dispose` as described below.
  The ledger also refuses a record whose `roots` is not a dict of the three
  recorded roots with string values.
- A worktree whose marked evidence became a dangling symlink, or whose content
  changed, stayed protected for ever once its owner session had ended: only a
  missing file counted as lost evidence, so no later session could mark it
  again. Both states now count, `evidence-preserved` accepts a new file for an
  ended owner, and `dispose --path` runs every existing check afterwards.
  `dispose` names `evidence-preserved` when it stops on unusable evidence
  instead of answering `symlink path` or `[Errno 2]`.
- `evidence-preserved` refuses a file inside the Git admin directory of a
  registered linked worktree (`<main>/.git/worktrees/<name>/`). `git worktree
  remove` deletes that directory, so evidence kept there did not outlive the
  disposal.
- The push-time purge of `--host claude` no longer empties the scratchpad
  `<claude temp>/<project>/<session>/scratchpad/` of the running session. A
  push is not the end of a session's need for its own scratch files: a session
  with five subagents lost their contract and verdict files at a push. The
  scratchpad is removed at session end, as before.
- `dispose --path` on a registered path whose directory is already gone drops
  the ledger entry when Git lists no worktree there and no local branch
  survives, as the README states. It answered `[Errno 2] No such file or
  directory` and kept the entry for ever. A worktree Git still lists, or whose
  local branch survives, stays protected with that reason.
- `evidence-preserved` refuses a file that session cleanup removes: a
  context-guard checkpoint, a transcript, file history, a scratchpad or any
  other session-keyed file of Claude Code or Codex, whatever the transcript
  policy. Such a file disappeared at session end and the worktree then stayed
  protected for ever (`dispose` answered `No such file or directory`). The
  refusal names the purged location and asks for a project file. The file is
  recognised by identity, not by spelling, so another letter case of the same
  path is refused on a file system that ignores case. The existing refusal of
  evidence inside a registered disposable directory now works the same way.
- A worktree or temporary directory whose evidence was lost that way can be
  finished by a later session working in the same main checkout, once the
  owner's session end is recorded: `evidence-preserved` marks a new file and
  `dispose --path` runs every existing check under the ended owner.
  `evidence-preserved` stays refused for evidence that still exists or was
  never marked; both commands stay refused for an active owner and for another
  repository. `dispose --path` does not require lost evidence: like SessionStart
  recovery, it finishes any path of an ended owner of the same main checkout.
- Marking evidence again for a worktree already removed, whose local branch is
  still pending, keeps the verified head instead of failing on the missing
  directory.
- End markers of sessions that own no registered path are dropped at
  SessionStart instead of accumulating in the ledger.

### disk-hygiene 0.1.0

#### Added

- New plugin for Claude Code and Codex: session-owned cleanup of registered
  git worktrees and local branches (removed only when the linked PR head
  contains every local commit and preserved evidence still matches), and of
  ended-session runtime files. Transcripts are kept by default
  (`DISK_HYGIENE_TRANSCRIPTS=delete` opts in); `DISK_HYGIENE_CLEANUP=off`
  disables everything. Moved out of the Cortex plugins, where it was proposed
  as PR 645, with these changes: push detection reads only the command of a
  shell tool (no match on file content, grep patterns, commit messages or
  `--dry-run`); the ledger lock waits up to 10 seconds instead of failing at
  once, and ordinary tool calls take no lock and write nothing; an unreadable
  end-of-session intake file is skipped instead of blocking every hook; the
  context-guard checkpoint follows the transcript policy; a symlinked config
  directory is resolved instead of silently disabling cleanup; the Cortex
  completion receipt for Codex rollouts is optional. Migration: the ledger
  moves to `<CLAUDE_CONFIG_DIR>/disk-hygiene/worktree-cleanup.json`,
  `CORTEX_CLAUDE_DIR` becomes `CLAUDE_CONFIG_DIR` and `CORTEX_CLEANUP_TRANSCRIPTS`
  becomes `DISK_HYGIENE_TRANSCRIPTS`; old ledger records are not carried over.

### context-guard 2.1.0

#### Added

- **Codex support.** The `Stop` guard now runs unmodified on Codex
  (codex-cli 0.157.1): host detection peeks the transcript's first JSONL
  record (`type == "session_meta"` -> Codex reader, anything else -> the
  pre-existing Claude path, byte-identical). Two new sibling modules,
  `transcript_codex.py` (Codex line predicates: `token_usage_record.usage.
  input_tokens` as context size, `function_call`/`custom_tool_call` as the
  activity-gate equivalent of `tool_use`) and `host_detect.py` (the
  session_meta peek), plus `usage_reader.py`, which extracts the two
  transcript reads' I/O orchestration out of `stop-context-guard.py` (host-
  dispatched, shared bounded scan) so that file shrinks instead of growing.
  A new per-model threshold row (`gpt-6-astra`: warn 180K / hard 220K,
  measured window 258,400) and four new `checkpoint_protocol.py` variants
  (`*_codex[_scoped]`) that drop the memory-writer-subagent delegate offer,
  the Codex parent writes the checkpoint directly; Claude's plugin-defined
  memory-writer agent type is not assumed available. The `SubagentStop` tracker now reads Codex child
  transcripts and cumulative usage, with cached input counted once and
  unknown dollar cost reported as unavailable. Every fact this integration relies on
  (hook payload schema, blocking-output contract, token semantics, activity
  predicate, context-window size, `hooks/hooks.json` reuse) is cited at its
  primary source in `plugins/context-guard/README.md`'s "Codex support"
  table, extracted from the `codex` binary's own embedded JSON Schemas and
  a real `~/.codex/sessions/**/*.jsonl` rollout, not from documentation.
  Packaging: `.agents/plugins/marketplace.json` now lists context-guard
  (`session-optimizer-codex` marketplace), and
  `plugins/context-guard/.codex-plugin/plugin.json` carries the Codex
  interface metadata (modeled on refine-gate's).

#### Fixed

- Upgrade the unchanged pre-Codex threshold table to include Astra while
  preserving custom model rows and defaults. Add Astra to the bundled
  statusline table.
- Document hook trust after Codex installation and verify the native
  app-server Stop lifecycle through a blocked stop, model-written
  checkpoint and subsequent clean completion.

- **The README's "Test the guard" smoke test now actually demonstrates a
  block.** Since [20213dc](https://github.com/cdeust/session-optimizer/commit/20213dc)
  (an ancestor of the `v2.3.0` tag below, undocumented until now, see the
  provenance note on that section) the Stop guard's activity gate
  (`_has_activity_since`) requires at least one `tool_use` content block in
  the transcript before it will fire. The documented snippet's transcript
  carried only a `usage` record, so it silently produced no output,
  contradicting its own "Expected: a `decision: block` payload." Fixed by
  adding the missing `tool_use` block, generating a unique `session_id` per
  run with its fire-once state file removed afterward (a fixed `"demo"`
  session_id went silent on every run after the first), and sandboxing
  `HOME` for the duration of the command with cleanup afterward (the
  snippet was overwriting the user's real
  `~/.claude/memories/checkpoints/latest.md`). Verified with a subprocess
  test that extracts the exact fenced block from the README and runs it
  twice. The neighboring "Test the tracker" snippet's equivalent
  cleanup gap (a stray `/tmp/zetetic-subagents-<SID>.json`) was fixed the
  same way.

#### Changed

- **`stop-context-guard.py` and `subagent-tracker.py` refactored,
  behavior-preserving**, to bring every function under the project's size
  limits (50 lines / 4 params / nesting depth 3) and the file itself under
  500 lines (580 lines pre-existing, a violation that predates this
  release). Three new sibling modules split out reusable concerns:
  `thresholds.py` (per-model config-table lookup), `transcript_lines.py`
  (JSONL line-content predicates), `transcript_scan.py` (the bounded
  chunked-line walk, shared by the transcript-usage reader and the
  activity-gate scanner). No behavior change; the existing hook contract,
  stdout JSON shape, and exit codes are unchanged. Also fixed: a valid but
  non-dict JSON Stop-hook payload (e.g. a bare list) previously raised an
  unhandled `AttributeError`, contradicting the hook's own "never fail
  hard" contract.

### statusline 2.3.0

#### Added

- **Verbatim per-session JSON snapshot for external readers.**
  `state/sessions/<session_id>.snapshot.json` now holds the exact statusLine
  stdin payload from the renderer's last refresh, byte for byte, so a reader
  outside this renderer (a Stream Deck plugin, a second dashboard) can read
  `context_window.used_percentage`, `rate_limits.five_hour`/`seven_day`,
  `cost.total_cost_usd`, `model.display_name`, etc. without re-deriving them
  or invoking the script itself. `write_session_snapshot`
  (`lib/session_state.sh`) writes only when `session_id` is non-empty and
  matches `[A-Za-z0-9_-]+` (rejects any id carrying `/` or `..`; every Claude
  Code session id, a UUID, qualifies), atomically (same-directory temp file,
  then `mv`), and never blocks or slows the render on a bad id or a disk
  error. `statusline-command.sh` now captures stdin with the trailing bytes
  preserved (`input=$(cat)` alone strips trailing newlines) so "verbatim"
  is literal. Tests: `tests/statusline/test_session_snapshot.sh` (byte
  identity, trailing-newline preservation, atomicity, traversal-id
  rejection, and that a write failure never changes the renderer's stdout).

## [2.3.0] - 2026-09-10

Statusline only for what shipped under this tag intentionally, but this
section's original text ("No change to context-guard or refine-gate") was
wrong: [20213dc](https://github.com/cdeust/session-optimizer/commit/20213dc)
(the Stop guard's activity gate) is an ancestor of the `v2.3.0` tag and was
never given its own version bump or changelog entry at the time. It is
documented retroactively under `context-guard 2.0.2` above, the first
version bump to carry it.

### Changed

- **Every file the statusline leaves on disk now lives under
  `~/.claude/statusline/`** ([#33](https://github.com/cdeust/session-optimizer/issues/33)).
  Measured 2026-09-10: 324 entries at the root of `~/.claude`, 238 of them the
  ledger's per-session price caches (`statusline-costs.jsonl.main.<uuid>` and
  `.sub.<uuid>`), the rest of the runtime spread flat beside them with the
  auto-update hook's `*.bak.<timestamp>` copies. The code (`statusline-command.sh`,
  `lib/`, `costs.sh`, `transcript.py`, `pricing.json`, `README.md`) sits at the
  top of that directory next to the user's `statusline-budget.json`; everything
  written at runtime goes under `state/`: the ledger as `costs.jsonl` with its
  lock and stamp, the per-session caches as `sessions/<session>.main|.sub`
  (still expired after 30 days), the telemetry cache as `transcript-cache.json`,
  and backups as `backup/<timestamp>/` pruned to the newest three runs.
  `STATUSLINE_COST_LOG` still relocates the ledger, and its caches now follow
  it into a `sessions/` directory beside it; `STATUSLINE_STATE_DIR` and
  `STATUSLINE_DIR` relocate the state and the install. The bundle mirrors the
  installed layout: `assets/statusline-lib/` is now `assets/lib/` and
  `assets/statusline-transcript.py` is `assets/transcript.py`.
- **One deterministic installer, `install.sh`, replaces the prose-driven
  install and update paths.** `install.sh install` (what the `/statusline`
  skill runs) and `install.sh sync` (what the `SessionStart` hook runs) migrate
  a flat install once by moving files, not copying them, so the old root files
  disappear with their content and timestamps intact; place only the code
  files that differ from the bundle; seed `statusline-budget.json` and
  `ctxguard-thresholds.json` only when absent; point `settings.json`
  `statusLine.command` at `~/.claude/statusline/statusline-command.sh` while
  preserving `padding` and `refreshInterval`; and refuse to rewrite an
  unparseable `settings.json`. `sync` is idempotent (an identical install is
  left untouched and prints nothing) and does nothing where no statusline is
  installed. `install.sh verify` replaces the skill's hand-run checks and
  fails on any flat-layout file left at the root.
- `ctxguard-thresholds.json` stays at `~/.claude/ctxguard-thresholds.json`:
  the context-guard plugin's Stop hook reads that exact path, and a file with
  two readers lives where both find it.
- `costs.sh init` writes its ledger backup under `backup/` beside the ledger
  instead of a `*.bak.*` file next to it; `costs.sh debug` reports the cache
  directory and how many per-session caches it holds.

## [2.2.1] - 2026-08-10

Statusline only. No change to context-guard or refine-gate.

### Fixed

- **Cost figures are no longer locale-dependent.** Under a comma-decimal
  locale (`LC_NUMERIC=fr_FR.UTF-8`) the ledger reported `$0`: `costs.sh` wrote
  comma-decimal amounts into its cache with no locale guard, and the render
  path then interpolated an unguarded value into `awk` program text, where
  the comma became an argument separator and truncated the amount. `costs.sh`
  and `statusline-command.sh` now force `LC_NUMERIC=C` (unsetting any
  inherited `LC_ALL` first, since it outranks `LC_NUMERIC`); `fmt_usd` and
  `cost_fmt` pass the value via `awk -v` instead of interpolating it, so a
  stray comma can never become syntax. An earlier fix in this cycle forced
  `LC_ALL=C` outright, which also pinned `LC_CTYPE` and broke `vislen()`'s
  UTF-8 width measurement (a 3-byte glyph read as width 3); only `LC_NUMERIC`
  is forced now. Verified on a `fr_FR.UTF-8` host: `costs.sh today` `$0` to
  the correct amount; `tests/statusline/test_fit_and_pace.sh` green under
  both `LC_ALL=fr_FR.UTF-8` and `LC_ALL=C`. Existing caches written with
  comma decimals regenerate with dot decimals on the next refresh.

## [2.2.0] - 2026-08-03

Provenance note: this release also carries every change recorded under the
**[2.1.0]** and **[2.1.1]** sections below, including the `statusline-costs.py`
removal and renderer-directory **BREAKING** change from [2.1.0]. Those two
sections were written in-tree with a date but were never tagged or published
as a release, no `v2.1.0` or `v2.1.1` git tag exists. `v2.0.0` (2026-07-23)
was the release preceding this one; `v2.2.0` (2026-08-03) is where those
changes first actually shipped.

### Added

- A skills-only Codex package for `refine-gate`, exposed through a repository
  marketplace at `.agents/plugins/marketplace.json`.
- Gemini CLI installation through the existing portable Agent Skill.
- OpenSSF Scorecard, CodeQL, Dependabot, and a hash-locked development
  dependency set.
- OpenSSF Best Practices Passing evidence and its repository badge.
- Security, contribution, conduct, governance, architecture, assurance-case,
  Scorecard, and twelve-month roadmap documentation.
- A release workflow that tests and self-verifies a source bundle, publishes
  SHA-256 checksums, an executable manifest and CycloneDX SBOM, and creates
  Sigstore build-provenance attestations.
- Regression tests for context-guard hooks, prompt-refinement measurement,
  statusline transcript handling, and release integrity.

### Changed

- The `refine` skill now uses the portable Agent Skills frontmatter and
  host-neutral wording. Claude's `UserPromptSubmit` hook and plugin manifests
  are unchanged.
- CI actions are pinned to full commit SHAs, workflow permissions are read-only
  by default, ShellCheck is checksum-verified, and Python dependencies are
  installed from the hashed lock file.
- CI measures the shipped Python surface with coverage.py's subprocess support
  and enforces an 80% floor; the initial complete measurement is 94%.
- Python contributions now name PEP 8 plus Ruff's selected rules as the coding
  standard, and CI enforces those rules from the hash-locked toolchain.
- The repository introduction now leads with its portable Codex, Gemini CLI,
  Claude, and Agent Skills surface while preserving explicit labels on
  Claude-only integrations.

### Fixed

- The statusline transcript scanner now recognizes valid compaction records
  whose JSON contains insignificant whitespace before confirming the parsed
  marker values.

## [2.1.1] - 2026-07-26

**Never tagged.** This date records when the work landed in-tree; no `v2.1.1`
git tag or release artifact was ever cut. The changes below first shipped
under `v2.2.0`, see the provenance note on that section.

Statusline only. No change to context-guard or refine-gate.

### Fixed

- **The line-width budget is now the host's actual reserve, not a 15% haircut.**
  `FIT_RATIO=85` held every line to 85% of the terminal, a figure inherited from
  an unrelated plugin and never verified here. The reserve Claude Code takes is
  additive, and it is now read from the host itself (2.1.220): 4 columns of
  container padding (`<Box paddingLeft={2} paddingRight={2}>`) plus
  `statusLine.padding` on both sides. On a 200-column terminal a line may now use
  196 columns instead of 170; narrow terminals reserve less than before.
- **Preset selection no longer contradicts the budget.** The verbosity preset is
  chosen on the fitted budget rather than the raw width. Previously `l` was
  selected from 90 columns up but could not render untrimmed below 104, so
  between those widths it was selected and then trimmed on every refresh.
- **`$COLUMNS` is consulted before the controlling tty.** The host sets it to the
  width it renders into, which is the authority; the tty probe stays as the
  fallback for hand-run invocations. Under the host the tty probe answers nothing
  at all, there is no controlling terminal in the hook environment.

### Notes

- The claim that an over-wide first line drops the second status line is false
  for 2.1.220: each line is rendered `<Text wrap="truncate">` independently.

### Internal

- `test_perf_heat_rgb_20run_avg` was flaky (~4 runs in 10) and asserted nothing:
  it compared one sample of `heat_rgb` against the mean of twenty more of the
  same call, so the delta was noise around zero, and per-sample `date +%s%N`
  forks vary by ~19 ms on the measurement host, four times the 5 ms budget
  being asserted. It now times a block of 2000 calls against an equally-sized
  no-op control, amortizing the fork: `heat_rgb` measures 0.13 ms above the
  control against the same 5 ms budget.

## [2.1.0] - 2026-07-26

**Never tagged.** This date records when the work landed in-tree; no `v2.1.0`
git tag or release artifact was ever cut. The BREAKING change below first
shipped under `v2.2.0`, see the provenance note on that section.

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
  agents are all billed, none of them appear in Claude Code's own
  `.cost.total_cost_usd`. Installs carrying a stale `~/.claude/statusline-costs.py`
  should delete it; the SessionStart hook now does so.
- **The renderer ships as a directory.** `statusline-command.sh` is a
  composition root that sources `statusline-lib/*.sh`, which must be installed
  next to it. A missing module is a hard failure that names the file rather
  than a partial statusline. `$STATUSLINE_LIB` overrides the location.

### Added

- `costs.sh` + `pricing.json` as bundled assets (the ledger and its prices).
- Terminal-width fitting: each line is held to 85% of the probed width, and
  `fit_line` drops whole trailing segments, lowest priority first, rather
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
- Tests: `tests/statusline/test_fit_and_pace.sh` (46 tests, fitting, pace,
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
  the host captures the renderer, it is now consulted only when stdout is a
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
  - **context-guard**, `Stop`-hook context budget with a per-model
    checkpoint protocol, budgeted `memory-writer` checkpoint subagent, and
    a `SubagentStop` spend tracker.
  - **refine-gate**, `UserPromptSubmit` prompt-binding gate + `/refine`
    skill.
  - **statusline**, multi-line status bar with RGB-gradient context bars,
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
   statusline config are untouched, the new plugins read the same paths.
4. If you had installed the `memory-writer` agent manually into
   `~/.claude/agents/`, you can remove it; context-guard ships its own
   copy (`context-guard:memory-writer`).

## [1.4.3] and earlier

Releases up to `v1.4.3` shipped the monolithic `session-optimizer` plugin.
See the git tags (`v1.0.0` … `v1.4.3`) for their history.
