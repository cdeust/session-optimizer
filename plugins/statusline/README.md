> Version française : [README.fr.md](README.fr.md)

# statusline — Claude Code

Multi-line statusline with a discrete heat-track bar, one cost ledger,
per-session telemetry, and rate-limit gauges with burn-rate pacing.

Every line opens with a word naming its concern, and every value is preceded
by the word for what it measures — no emoji, no glyph-encoded state. A glyph
has to be learned and is ambiguous across terminal fonts (several render
double-width and break column budgets); a word reads the same everywhere.
Status is carried by colour and number only.

## Install

```
/plugin marketplace add cdeust/session-optimizer
/plugin install statusline@session-optimizer-marketplace
```

Then ask Claude to **"install the statusline"** — the bundled `statusline`
skill copies the assets into `~/.claude/` and wires `statusLine` in
`~/.claude/settings.json` (backups included, config files never
overwritten). Restart Claude Code to activate. Requires `jq` and `python3`.

A `SessionStart` hook keeps the code assets current after `plugin update`;
your `statusline-budget.json` and `ctxguard-thresholds.json` are never
touched automatically.

<details>
<summary>Manual install (without the plugin system)</summary>

1. Copy everything in `assets/` into `~/.claude/` — including the whole
   `statusline-lib/` directory, which must sit next to the renderer — then
   `chmod +x ~/.claude/statusline-command.sh ~/.claude/costs.sh`.
2. Declare the statusline in `~/.claude/settings.json`:
   ```json
   { "statusLine": { "type": "command", "command": "bash ~/.claude/statusline-command.sh", "padding": 1, "refreshInterval": 10 } }
   ```
3. Adapt `statusline-budget.json` to your own preferences.

</details>

## Files (bundled under `assets/`)

| File | Role |
|---|---|
| `statusline-command.sh` | Renderer entry point — the composition root, called by Claude Code on every refresh. |
| `statusline-lib/*.sh` | The renderer's modules, one concern per file. Must be installed next to the renderer. |
| `costs.sh` | Cost ledger CLI over `~/.claude/statusline-costs.jsonl` — the single source of every dollar figure. |
| `pricing.json` | Per-model token prices `costs.sh` prices with. |
| `statusline-transcript.py` | Per-session telemetry (tok/s, compactions, response age, last_ts) — reverse-tail + incremental scan, short cache (15 s, in the background). |
| `statusline-budget.json` | **Personal** config: cache TTL, display size. |
| `ctxguard-thresholds.json` | Per-model context thresholds — **shared** with the context-guard plugin (see below). |

The renderer resolves `statusline-lib/` relative to its own path (override with
`$STATUSLINE_LIB`) and exits with a message naming the missing file if a module
is absent, rather than rendering a partial statusline.

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

## Segments

```
model      model NAME | dir NAME | effort LEVEL | thinking on
branch     branch NAME modified | ahead N | behind N | mod N add N del N
context    ███░░░ N% Nk tokens | session $N | elapsed NmNs | edits +N/-N
throughput N tokens/s | idle NmNs | cache warm NmNs | compactions N
quota 5h   ███░░░ N% | pace N.Nx | resets in NhNm
spend      today $N | month $N | average $N/day
```

- **Identity**: model, directory, effort, thinking. The directory comes second
  on purpose — segment order is priority order (see *Fitting* below), and on a
  narrow terminal "which tree am I in" is worth more than the session dials.
- **Git**: branch + `modified`, ahead/behind vs upstream, conflicts, and a
  `mod/add/del/untracked` breakdown (m+). Falls back to `NAME@repo` on the most
  recently touched sub-repo when the cwd is not a repository.
- **Session**: context bar, tokens, session cost, duration, churn.
- **Subagents**: count and token volume for this session, read from the
  aggregate the context-guard plugin's `SubagentStop` tracker maintains
  (segment stays empty when that plugin is not installed). Their **cost** is
  not shown separately — it is already inside the session figure, which the
  ledger prices from the `subagents/` subtree.
- **Telemetry** (m+): throughput of the last turn (wall-clock, includes tool
  latency ⇒ a lower bound), idle time since the last response, prompt-cache
  countdown (`cache cold` in red once it lapses), context compactions.
- **Quota** (l+): `quota 5h` and `quota 7d` = % of the Pro/Max rate-limit
  quota consumed (the real "do not exceed" constraint; 100% = lockout), each
  with its **pace** and its reset time. At the `m` preset, a compact inline
  version on the session line — same resolver, so the two renderings of one
  window can never disagree.
- **Spend** (l+): today / month-to-date / average per day, every figure from
  the one ledger and including subagent spend. Informational, not a cap.

## Pace

A quota reading alone cannot say whether it is a problem: 60% used is fine four
hours into a five-hour window and alarming ten minutes in. **Pace** is the burn
rate against the window's own clock — used% divided by the share of the window
elapsed — which is also the linear projection of usage at reset: `1.0x` projects
landing exactly on the cap.

The percentage carries the **worse** of the absolute and pace readings; the pace
figure carries its own. Colours: green below 50% used (or under 0.8x), yellow
from 50%, red from 80% used or a projection that reaches the cap. Below 10% of
the window elapsed no pace is printed at all — the extrapolation would swing
wildly on a single burst.

## Fitting

Every line is fitted to the terminal. The verbosity preset is the coarse
adjustment (how many **lines**), and `fit_line` is the fine one: it drops each
line's lowest-priority trailing segments until the line fits. Lines are built
most-important-first, so **the tail is what goes**.

The budget is the terminal width minus what the host keeps for itself: 4 columns
of container padding, plus `statusLine.padding` twice over (the host applies it
on both sides). Both figures are read from Claude Code 2.1.220's own renderer,
which wraps the block in `<Box paddingLeft={2} paddingRight={2}>` and each line
in `<Text wrap="truncate">` — so an over-wide line is truncated **on its own**
and never costs the block another row.

Width is probed from `$COLUMNS` first — the host sets it to the width it is
rendering into — then the controlling tty, then `tput cols` (only when stdout is
a terminal; on a pipe it returns terminfo's blind 80). When nothing can answer,
the fallback is deliberately wide. Override with `$STATUSLINE_COLS`.

## Shared thresholds with context-guard

The context bar's green → yellow → red scale is driven by
`~/.claude/ctxguard-thresholds.json` — a **shared-file convention** with the
[context-guard](../context-guard) plugin's Stop hook. One file, two
consumers: the statusline is the passive visual warning, the Stop guard the
active enforcement, and editing the file moves both at once so they stay on
par by construction. The install skill seeds the file if absent and never
overwrites an existing copy.

## Display sizes (presets)

`xs` (1 line) · `s` (2) · `m` (3) · `l` (5, default) · `xl` (5, wide bars + monthly average).

Setting: `STATUSLINE_SIZE` env variable, or the `"size"` field of `statusline-budget.json`.

## Technical notes

- `.rate_limits.{five_hour,seven_day}` (Pro/Max accounts): `used_percentage` is
  already a ratio of the quota → drives the gauges directly; `resets_at` =
  epoch in **seconds**. No absolute monthly budget: on a flat-rate plan, the
  constraint is the quota, not a spend in $/tokens.
- Bars: a 4-palier **discrete** heat track, each cell coloured by its position
  along the full width — no interpolation, so a fuller bar visibly accumulates
  paliers left to right.
- Costs: one ledger. `costs.sh` prices each session from its own transcript plus
  the full recursive `subagents/` subtree, deduplicating on
  `message.id:requestId` first — Claude Code re-logs one API response 2-3 times
  (streaming / tool continuation), and summing raw assistant lines over-counts
  ~2.2x (measured over 172 local transcripts: $3438.84 deduped vs $7645.04 raw).
  Claude Code's own `.cost.total_cost_usd` covers the main thread only and
  carries prior spend forward on a resumed session, so it is used only as a
  labelled `session main` fallback.
- Telemetry: the `.py` runs in the background (lock + 15 s TTL) and writes a
  per-session cache (key = `transcript_path`); idle and cache countdowns are
  recomputed live on every refresh from `last_ts`, so they stay second-accurate
  between two scans. JSONL is append-only ⇒ the compaction count is incremental
  (scans only the appended bytes `[prev_size, size)`).
- `cache_ttl_min`: 5 (Pro default) or 60 (Max) — source: Anthropic
  prompt-caching docs (5 min TTL by default). Inspirations: `CCometixLine`
  (git ahead/behind + conflicts), `claude-hud` (tok/s, compactions, cache TTL).
