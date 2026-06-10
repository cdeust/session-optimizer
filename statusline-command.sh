#!/usr/bin/env bash
# Claude Code statusLine — zetetic partner view (persistent, two-line)
#
# Line 1: [model] [effort] · dir · git:(branch)✗ · ⎇worktree · PR#n
# Line 2: ▓▓▓░░░░░░░ ctx:N% tokens:Nk · $cost · ⏱duration · 5h:N% 7d:N% · +adds/-dels
#
# Context/token color tracks the per-model checkpoint thresholds (orchestrator
# rule, shared with stop-context-guard.py via ~/.claude/ctxguard-thresholds.json):
#   Fable 5 / Mythos : warn 120K, save 160K  (2x rent + 2x cache-expiry penalty)
#   Opus 4.x         : warn 180K, save 200K  (cost discipline; window is 1M)
#   Sonnet 4.6       : warn 180K, save 200K  (cost discipline; window is 1M)
#   Haiku 4.5        : warn 120K, save 170K  (200K IS the window; keep headroom)
#   < warn   green   — healthy
#   >= warn  yellow  — getting full, plan a save
#   >= save  red ⚠   — save memory + start fresh with a recall
#
# No DIM attribute anywhere — it renders unreadable on black terminals.
# Secondary text uses light grey; primary uses bright white.

input=$(cat)
j() { echo "$input" | jq -r "$1"; }

# --- Core ---
model=$(j '.model.display_name // ""')
effort=$(j '.effort.level // ""')
thinking=$(j '.thinking.enabled // false')
used_pct=$(j '.context_window.used_percentage // empty')
in_tokens=$(j '.context_window.total_input_tokens // empty')

# --- Cost / duration / churn ---
cost=$(j '.cost.total_cost_usd // empty')
dur_ms=$(j '.cost.total_duration_ms // empty')
adds=$(j '.cost.total_lines_added // 0')
dels=$(j '.cost.total_lines_removed // 0')

# --- Rate limits (Pro/Max only) ---
rl_5h=$(j '.rate_limits.five_hour.used_percentage // empty')
rl_7d=$(j '.rate_limits.seven_day.used_percentage // empty')

# --- PR / worktree ---
pr_num=$(j '.pr.number // empty')
pr_state=$(j '.pr.review_state // empty')
wt_name=$(j '.worktree.name // .workspace.git_worktree // empty')

# --- Git context ---
cwd=$(j '.workspace.current_dir // .cwd // ""')
dir=$(basename "$cwd")
git_branch=""
git_dirty=""
if git -C "$cwd" rev-parse --git-dir > /dev/null 2>&1; then
  git_branch=$(git -C "$cwd" -c core.useBuiltinFSMonitor=false symbolic-ref --short HEAD 2>/dev/null \
               || git -C "$cwd" -c core.useBuiltinFSMonitor=false rev-parse --short HEAD 2>/dev/null)
  if ! git -C "$cwd" -c core.useBuiltinFSMonitor=false diff --quiet 2>/dev/null \
     || ! git -C "$cwd" -c core.useBuiltinFSMonitor=false diff --cached --quiet 2>/dev/null; then
    git_dirty="✗"
  fi
fi

# --- Colors (all readable on black; no DIM) ---
RESET="\033[0m"
LGREY="\033[38;2;200;200;200m"   # light grey — secondary text / separators
WHITE="\033[1;37m"               # bright white — primary labels
GREEN="\033[38;2;120;220;120m"
YELLOW="\033[38;2;230;210;90m"
RED="\033[38;2;235;100;100m"
CYAN="\033[38;2;120;200;200m"
BLUE="\033[38;2;120;170;230m"
MAGENTA="\033[38;2;205;150;220m"
SEP="${LGREY}·${RESET}"

# --- Checkpoint thresholds (tokens of input context) ---
# Single source of truth: ~/.claude/ctxguard-thresholds.json (shared with the
# stop-context-guard.py hook so passive display and active enforcement cannot
# drift). The case block below is the embedded fallback and MUST mirror the
# hook's FALLBACK_THRESHOLDS table. First substring match wins.
# WARN = checkpoint threshold (save now); SAVE = soft cap (save + recall, fresh session).
CTXGUARD_CONFIG="${HOME}/.claude/ctxguard-thresholds.json"
model_lc=$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')

WARN_TOKENS=""
SAVE_TOKENS=""
if [ -r "$CTXGUARD_CONFIG" ]; then
  # First entry whose .match is a substring of the lowercased display name;
  # falls back to .default. Emits "warn save" or nothing on any jq failure.
  read -r WARN_TOKENS SAVE_TOKENS < <(
    jq -r --arg m "$model_lc" '
      ( [ .models[]? | select(.match != null)
          | select(.match as $p | $m | contains($p)) ][0]
        // .default // empty )
      | select(.warn != null and .hard != null)
      | "\(.warn) \(.hard)"
    ' "$CTXGUARD_CONFIG" 2>/dev/null
  ) || true
fi
case "$WARN_TOKENS" in ''|*[!0-9]*) WARN_TOKENS="" ;; esac
case "$SAVE_TOKENS" in ''|*[!0-9]*) SAVE_TOKENS="" ;; esac

if [ -z "$WARN_TOKENS" ] || [ -z "$SAVE_TOKENS" ]; then
  case "$model_lc" in
    *fable*|*mythos*) WARN_TOKENS=120000; SAVE_TOKENS=160000 ;;
    *haiku*)          WARN_TOKENS=120000; SAVE_TOKENS=170000 ;;
    *)                WARN_TOKENS=180000; SAVE_TOKENS=200000 ;;
  esac
fi

# pick color for a given token count
token_color() {
  local t="$1"
  if   [ "$t" -ge "$SAVE_TOKENS" ]; then echo "$RED"
  elif [ "$t" -ge "$WARN_TOKENS" ]; then echo "$YELLOW"
  else echo "$GREEN"; fi
}

fmt_tokens() {
  local t="$1"
  if [ "$t" -ge 1000000 ]; then LC_NUMERIC=C awk "BEGIN{printf \"%.1fM\",$t/1000000}"
  elif [ "$t" -ge 1000 ]; then LC_NUMERIC=C awk "BEGIN{printf \"%.0fk\",$t/1000}"
  else echo "$t"; fi
}

# =========================================================================
# LINE 1 — identity, location, git
# =========================================================================
line1="${WHITE}[${model}]${RESET}"

if [ -n "$effort" ]; then
  eff_label="$effort"
  [ "$thinking" = "true" ] && eff_label="${eff_label}+think"
  line1="${line1} ${LGREY}[${eff_label}]${RESET}"
fi

line1="${line1} ${SEP} ${CYAN}${dir}${RESET}"

if [ -n "$git_branch" ]; then
  dirty_part=""
  [ -n "$git_dirty" ] && dirty_part=" ${RED}${git_dirty}${RESET}"
  line1="${line1} ${SEP} ${BLUE}git:(${WHITE}${git_branch}${BLUE})${dirty_part}${RESET}"
fi

if [ -n "$wt_name" ]; then
  line1="${line1} ${SEP} ${MAGENTA}⎇ ${wt_name}${RESET}"
fi

if [ -n "$pr_num" ]; then
  pr_color="$LGREY"
  case "$pr_state" in
    approved)          pr_color="$GREEN" ;;
    changes_requested) pr_color="$RED" ;;
    pending)           pr_color="$YELLOW" ;;
  esac
  line1="${line1} ${SEP} ${pr_color}PR#${pr_num}${RESET}"
fi

# =========================================================================
# LINE 2 — context pressure, tokens, cost, duration, rate limits, churn
# =========================================================================
line2=""

if [ -n "$used_pct" ] || [ -n "$in_tokens" ]; then
  # color by absolute token count against the save threshold (works for 1M window)
  tok="${in_tokens:-0}"
  cc=$(token_color "$tok")

  if [ -n "$used_pct" ]; then
    pct_int=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$used_pct}")
    filled=$((pct_int / 10)); [ "$filled" -gt 10 ] && filled=10
    empty=$((10 - filled))
    bar=""
    [ "$filled" -gt 0 ] && printf -v f "%${filled}s" && bar="${f// /▓}"
    [ "$empty" -gt 0 ] && printf -v e "%${empty}s" && bar="${bar}${e// /░}"
    line2="${cc}${bar}${RESET} ${cc}ctx:${pct_int}%${RESET}"
  fi

  if [ -n "$in_tokens" ]; then
    line2="${line2:+$line2 }${cc}tokens:$(fmt_tokens "$in_tokens")${RESET}"
    # explicit checkpoint hint once past the save threshold
    [ "$in_tokens" -ge "$SAVE_TOKENS" ] && line2="${line2} ${RED}⚠ save+recall${RESET}"
  fi
fi

DOLLAR='$'
if [ -n "$cost" ]; then
  cost_fmt=$(LC_NUMERIC=C awk "BEGIN{printf \"%.2f\",$cost}")
  line2="${line2:+$line2 ${SEP} }${YELLOW}${DOLLAR}${cost_fmt}${RESET}"
fi

if [ -n "$dur_ms" ]; then
  dur_s=$((dur_ms / 1000)); mins=$((dur_s / 60)); secs=$((dur_s % 60))
  line2="${line2:+$line2 ${SEP} }${LGREY}⏱${mins}m${secs}s${RESET}"
fi

if [ -n "$rl_5h" ] || [ -n "$rl_7d" ]; then
  rl_seg=""
  if [ -n "$rl_5h" ]; then
    v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_5h}")
    if [ "$v" -ge 80 ]; then c="$RED"; elif [ "$v" -ge 50 ]; then c="$YELLOW"; else c="$LGREY"; fi
    rl_seg="${c}5h:${v}%${RESET}"
  fi
  if [ -n "$rl_7d" ]; then
    v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_7d}")
    if [ "$v" -ge 80 ]; then c="$RED"; elif [ "$v" -ge 50 ]; then c="$YELLOW"; else c="$LGREY"; fi
    rl_seg="${rl_seg:+$rl_seg }${c}7d:${v}%${RESET}"
  fi
  line2="${line2:+$line2 ${SEP} }${rl_seg}"
fi

if [ "$adds" -gt 0 ] || [ "$dels" -gt 0 ]; then
  line2="${line2:+$line2 ${SEP} }${GREEN}+${adds}${RESET}${LGREY}/${RESET}${RED}-${dels}${RESET}"
fi

# --- Emit (%b interprets ANSI escapes; data is in args, not the format) ---
printf '%b\n' "$line1"
[ -n "$line2" ] && printf '%b\n' "$line2"
exit 0
