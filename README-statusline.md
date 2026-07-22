> Version française : [README-statusline.fr.md](README-statusline.fr.md)

# Zetetic statusline — Claude Code

Multi-line statusline (Catppuccin Mocha) with RGB-gradient bars, monthly
cost tracking, and per-person target gauges.

## Files

| File | Role |
|---|---|
| `statusline-command.sh` | Rendering script (called by Claude Code on every refresh). |
| `statusline-costs.py` | Cost aggregator (scans `~/.claude/projects/**/*.jsonl`, 1 h cache). |
| `statusline-transcript.py` | Per-session telemetry (tok/s, compactions, response age, last_ts) — reverse-tail + incremental scan, short cache (15 s, in the background). |
| `statusline-budget.json` | **Personal** config: monthly targets, cache TTL, display size. |

## Segments

- **Identity**: model, effort, thinking 💡, folder.
- **Git**: branch 🌿 + dirty `✗`, `↑n ↓n` (ahead/behind vs upstream), `⚠n`
  (conflicts), breakdown `!M +A ✘D ?U` (m+). Falls back to `@repo` on the most
  recently touched sub-repo when the cwd is not a repository.
- **Session**: context bar 🧠, tokens, `💰` cost, `⏱` duration, rate limits
  🚀/🌟, churn ✏️.
- **Telemetry** (m+): `⚡ t/s` (throughput of the last turn — wall-clock,
  includes tool latency ⇒ lower bound), `🕑` age of the last response, `❄`
  prompt-cache countdown (red = `cold`), `🗜` context compactions.
- **Quota** (l+): 🎯 gauges `🚀 5h` and `🌟 7d` = % of the Pro/Max rate-limit
  quota consumed (the real "do not exceed" constraint; 100% = lockout), with
  reset time. Colors: green < 50, yellow 50–79, red ≥ 80. At the `m` preset,
  a compact inline version on the session line. Followed by a **cost
  reference** line (informative, not a cap): `💰 $/month · 🤖 $/run`.

## Installation

1. Copy the 4 files into `~/.claude/`.
2. Declare the statusline in `~/.claude/settings.json`:
   ```json
   { "statusLine": { "type": "command", "command": "~/.claude/statusline-command.sh" } }
   ```
3. Adapt `statusline-budget.json` to your own targets.

## Display sizes (presets)

`xs` (1 line) · `s` (2) · `m` (3) · `l` (5, default) · `xl` (5, wide bars + monthly average).

Setting: `STATUSLINE_SIZE` env variable, or the `"size"` field of `statusline-budget.json`.

## Technical notes

- `.rate_limits.{five_hour,seven_day}` (Pro/Max accounts): `used_percentage` is
  already a ratio of the quota → drives the 🎯 gauges directly; `resets_at` =
  epoch in **seconds**. No absolute monthly budget: on a flat-rate plan, the
  constraint is the quota, not a spend in $/tokens.
- Bars: continuous per-cell RGB interpolation (`grad_rgb`) green→yellow→peach→red.
- Context thresholds: `~/.claude/ctxguard-thresholds.json` (shared with the stop-context-guard hook).
- Telemetry: the `.py` runs in the background (lock + 15 s TTL) and writes a
  per-session cache (key = `transcript_path`); `🕑` and `❄` are recomputed live
  on every refresh from `last_ts`, so the countdown stays second-accurate
  between two scans. JSONL is append-only ⇒ the compaction count is incremental
  (scans only the appended bytes `[prev_size, size)`).
- `cache_ttl_min`: 5 (Pro default) or 60 (Max) — source: Anthropic
  prompt-caching docs (5 min TTL by default). Inspirations: `CCometixLine`
  (git ahead/behind + conflicts), `claude-hud` (tok/s, compactions, cache TTL).
