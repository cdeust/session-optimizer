#!/usr/bin/env bash
# shellcheck disable=SC2034  # The facts extracted from the statusLine JSON below
# are consumed by statusline-lib/render.sh, which shellcheck does not follow
# across the source boundary; it therefore reads every one of them as unused.
# The golden render diff and tests/statusline are what prove they are live.
# Claude Code statusLine — zetetic partner view (persistent, multi-line)
#
# This file is the composition root: it loads the modules, reads the statusLine
# JSON, asks each collector for its facts, asks each renderer for its line, and
# emits what fits. It holds no display logic and no thresholds of its own — see
# statusline-lib/ for those, one concern per file:
#
#   platform.sh       BSD/GNU spelling differences (stat, date)
#   palette.sh        colour tokens, the heat-track bar
#   fit.sh            visible-width measurement and trimming
#   severity.sh       the one ok/warn/danger scale and its thresholds
#   format.sh         numbers and times as the reader sees them
#   config.sh         the two JSON config files
#   gitctx.sh         the repository facts
#   session_state.sh  cost ledger, transcript telemetry, subagent tracker
#   layout.sh         terminal width probe, verbosity preset
#   render.sh         one function per status line
#
# Every line opens with a word naming its concern, and every value is preceded
# by the word for what it measures. No emoji, no pictographs, no glyph-encoded
# state: a word is unambiguous across terminal fonts and needs no legend.
# Status is carried by colour and number only.
#
#   model      model NAME | dir NAME | effort LEVEL | thinking on
#   branch     branch NAME modified | ahead N | behind N | mod N add N del N
#              | worktree NAME | pr N approved
#              (branch falls back to the most-recently-modified sub-repo of
#               cwd, shown as branch NAME@repo, when cwd is not a git repo)
#   context    ███░░░ N% Nk tokens | session $N | elapsed NmNs | edits +N/-N
#              | subagents N Nk tokens
#   throughput N tokens/s | idle NmNs | cache warm NmNs | compactions N
#   quota 5h   ███░░░ N% | pace N.Nx | resets in NhNm
#   quota 7d   ███░░░ N% | pace N.Nx | resets Day HH:MM
#   spend      today $N | month $N | average $N/day
#
# Every line is fitted to the terminal: the verbosity preset chooses how many
# lines render (and drops to "s" on a terminal too narrow for the default), and
# fit_line then drops each line's lowest-priority trailing segments until it
# fits. Lines are built most-important-first, so the tail is what goes. Override
# the probed width with $STATUSLINE_COLS and the preset with $STATUSLINE_SIZE.
#
# "pace" is the burn rate against the window's own clock: used% divided by the
# share of the window elapsed, which is also the linear projection of usage at
# reset. 1.0x projects landing exactly on the cap. The percentage carries the
# worse of the absolute and pace readings; the pace figure carries its own.
#
# Every dollar figure on this statusline comes from ONE ledger,
# ~/.claude/costs.sh over ~/.claude/statusline-costs.jsonl, and every one of
# them includes subagent spend (Task, worktree-isolated, and workflow agents).
#
# Context/token colour tracks the per-model checkpoint thresholds shared with
# stop-context-guard.py via ~/.claude/ctxguard-thresholds.json (see config.sh):
# green below the warn threshold, yellow at it, red at the save threshold.

# --- Modules ---------------------------------------------------------------
# Resolved relative to this file, so the renderer works from a plugin directory,
# a checkout or ~/.claude without a hardcoded path. $STATUSLINE_LIB overrides it
# (test harnesses pointing at a working copy).
# Load order is dependency order: platform and palette own no dependencies,
# everything else builds on them.
# Numeric formatting must be locale-independent. Under a comma-decimal locale
# (e.g. fr_FR) awk/printf emit "19,78"; interpolated into a downstream awk
# program that value is read as TWO arguments and the amount is truncated.
# Only the numeric category may be forced: LC_ALL=C would also pin LC_CTYPE,
# and vislen() measures with ${#s}, which then counts UTF-8 bytes instead of
# columns (a 3-byte glyph reads as width 3). LC_ALL must be unset first —
# it outranks LC_NUMERIC when the environment sets it.
unset LC_ALL
export LC_NUMERIC=C

STATUSLINE_LIB="${STATUSLINE_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/statusline-lib}"
for _mod in platform palette fit severity format config gitctx session_state layout render; do
  if [ -r "${STATUSLINE_LIB}/${_mod}.sh" ]; then
    # shellcheck source=/dev/null
    . "${STATUSLINE_LIB}/${_mod}.sh"
  else
    # A missing module cannot be rendered around: without palette.sh there are
    # no colours to print an error in, and without render.sh there is nothing to
    # print. Say which file is missing, on the statusline itself — a silently
    # blank status bar would read as "nothing is happening".
    printf 'statusline: cannot read %s\n' "${STATUSLINE_LIB}/${_mod}.sh" >&2
    printf 'statusline: module %s.sh missing from %s\n' "$_mod" "$STATUSLINE_LIB"
    exit 1
  fi
done

# Test-sourcing guard: allows `source statusline-command.sh` to load every
# module above (the pure functions and constants: heat_rgb, make_bar,
# token_color, vislen, fit_line, quota_reading, file_mtime, the palette …)
# without reading stdin or rendering. Set by test harnesses only; unset/0 in
# normal invocation. Everything below this line depends on stdin ($input) or on
# values derived from it.
[ "${STATUSLINE_SOURCE_ONLY:-0}" = "1" ] && return 0 2>/dev/null

input=$(cat)
j() { echo "$input" | jq -r "$1"; }
now_epoch=$(date +%s)

# --- Facts from the statusLine JSON ---
model=$(j '.model.display_name // ""')
effort=$(j '.effort.level // ""')
thinking=$(j '.thinking.enabled // false')
used_pct=$(j '.context_window.used_percentage // empty')
in_tokens=$(j '.context_window.total_input_tokens // empty')
transcript_path=$(j '.transcript_path // empty')
session_id=$(j '.session_id // empty')

cost=$(j '.cost.total_cost_usd // empty')
dur_ms=$(j '.cost.total_duration_ms // empty')
adds=$(j '.cost.total_lines_added // 0')
dels=$(j '.cost.total_lines_removed // 0')

# Rate limits (Pro/Max only)
rl_5h=$(j '.rate_limits.five_hour.used_percentage // empty')
rl_7d=$(j '.rate_limits.seven_day.used_percentage // empty')
rl_5h_at=$(j '.rate_limits.five_hour.resets_at // empty')
rl_7d_at=$(j '.rate_limits.seven_day.resets_at // empty')

pr_num=$(j '.pr.number // empty')
pr_state=$(j '.pr.review_state // empty')
wt_name=$(j '.worktree.name // .workspace.git_worktree // empty')

cwd=$(j '.workspace.current_dir // .cwd // ""')
dir=$(basename "$cwd")

# --- Facts from everywhere else ---
resolve_ctx_thresholds "$model"          # -> WARN_TOKENS SAVE_TOKENS
cache_ttl_min=$(read_cache_ttl_min)
collect_git "$cwd"                       # -> git_* n*
read_cost_ledger "$session_id" "$input"  # -> cost_session cost_today cost_month cost_avg_day
read_transcript_telemetry "$transcript_path" "$now_epoch"   # -> txt_*
read_subagent_totals "$session_id"       # -> sub_count sub_tokens

COLS=$(probe_cols)
FIT_W=$(fit_budget "$COLS" "$(read_statusline_padding)")
# The preset is chosen on the BUDGET, not the raw width: what decides whether a
# preset fits is the room its lines actually get, which is the width less the
# host's chrome (fit.sh). Choosing on the raw width would select a preset the
# budget then trims on every refresh.
resolve_preset "$FIT_W"                  # -> SIZE RANK CTX_W BW

# --- Emit (%b interprets ANSI escapes; data is in args, not the format) ---
# One concern per line; empty lines are skipped so the block stays compact.
# Line count grows with the verbosity preset (RANK): xs collapses identity +
# context onto a single line; git appears at s+; quota and spend at l+.
#
# Every line goes through fit_line, which drops its lowest-priority trailing
# segments when the terminal is too narrow for the whole line. The preset is the
# coarse adjustment (how many LINES) and this is the fine one (how many SEGMENTS
# per line) — the preset cannot know how long this session's branch name or
# directory happens to be.
emit() {
  local fitted
  fitted=$(fit_line "$1" "$FIT_W")
  printf '%b\n' "$fitted"
}

l_id=$(render_identity)
l_session=$(render_session)

if [ "$RANK" -le 0 ]; then
  emit "${l_id}${l_session:+ ${SEP} ${l_session}}"
  exit 0
fi

l_git=$(render_git)
l_tele=$(render_telemetry)

emit "$l_id"
[ -n "$l_git" ]     && emit "$l_git"
[ -n "$l_session" ] && emit "$l_session"
[ -n "$l_tele" ]    && emit "$l_tele"

if [ "$RANK" -ge 3 ]; then
  l_quota5h=$(render_quota "5h" "$rl_5h" "$rl_5h_at" "$RL_5H_WINDOW_S")
  l_quota7d=$(render_quota "7d" "$rl_7d" "$rl_7d_at" "$RL_7D_WINDOW_S")
  l_spend=$(render_spend)
  [ -n "$l_quota5h" ] && emit "$l_quota5h"
  [ -n "$l_quota7d" ] && emit "$l_quota7d"
  [ -n "$l_spend" ]   && emit "$l_spend"
fi
exit 0
