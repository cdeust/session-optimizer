#!/usr/bin/env bash
# Claude Code statusLine — zetetic partner view (persistent, two-line)
#
# Line 1: [model] [effort] · dir · git:(branch)✗ · ⎇worktree · PR#n
#         (branch falls back to the most-recently-modified sub-repo of cwd,
#          shown as git:(branch)@repo, when cwd itself is not a git repo)
# Line 2: ▓▓▓░░░░░░░ ctx:N% tokens:Nk · $cost · ⏱duration · 5h:N% 7d:N% · +adds/-dels
# Line 3: 💰 mois:$Xk (moy $Yk/mo) · agent~$Z/run   (aggregated, cached 1h)
#         source data: statusline-costs.py over ~/.claude/projects/**/*.jsonl
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
transcript_path=$(j '.transcript_path // empty')

# --- Cost / duration / churn ---
cost=$(j '.cost.total_cost_usd // empty')
dur_ms=$(j '.cost.total_duration_ms // empty')
adds=$(j '.cost.total_lines_added // 0')
dels=$(j '.cost.total_lines_removed // 0')

# --- Rate limits (Pro/Max only) ---
rl_5h=$(j '.rate_limits.five_hour.used_percentage // empty')
rl_7d=$(j '.rate_limits.seven_day.used_percentage // empty')
rl_5h_at=$(j '.rate_limits.five_hour.resets_at // empty')
rl_7d_at=$(j '.rate_limits.seven_day.resets_at // empty')

# --- Subagent spend (from the SubagentStop tracker; not in the statusline
#     input, so read the per-session aggregate the hook maintains) ---
session_id=$(j '.session_id // empty')
sub_count=""
sub_tokens=""
sub_cost=""
if [ -n "$session_id" ]; then
  SUB_STATE="/tmp/zetetic-subagents-${session_id}.json"
  if [ -r "$SUB_STATE" ]; then
    read -r sub_count sub_tokens sub_cost < <(
      jq -r '.totals as $t
             | "\($t.count // 0) "
             + "\(((($t.input_tokens // 0) + ($t.output_tokens // 0) + ($t.cache_tokens // 0)))) "
             + "\($t.cost_usd // 0)"' "$SUB_STATE" 2>/dev/null
    ) || true
    case "$sub_count" in ''|0|*[!0-9]*) sub_count="" ;; esac
  fi
fi

# --- PR / worktree ---
pr_num=$(j '.pr.number // empty')
pr_state=$(j '.pr.review_state // empty')
wt_name=$(j '.worktree.name // .workspace.git_worktree // empty')

# --- Git context ---
cwd=$(j '.workspace.current_dir // .cwd // ""')
dir=$(basename "$cwd")
git_branch=""
git_dirty=""
git_repo=""        # set to the sub-repo name when the branch is resolved by fallback
git_root=""        # resolved repo root (cwd, or the fallback sub-repo)
# git extras, filled by compute_git_extra below
git_ahead=""; git_behind=""; git_conf=0; nM=0; nA=0; nD=0; nU=0
branch_of() {      # echo "branch dirty" for repo root $1
  local root="$1" b d=""
  b=$(git -C "$root" -c core.useBuiltinFSMonitor=false symbolic-ref --short HEAD 2>/dev/null \
      || git -C "$root" -c core.useBuiltinFSMonitor=false rev-parse --short HEAD 2>/dev/null)
  if ! git -C "$root" -c core.useBuiltinFSMonitor=false diff --quiet 2>/dev/null \
     || ! git -C "$root" -c core.useBuiltinFSMonitor=false diff --cached --quiet 2>/dev/null; then
    d="✗"
  fi
  printf '%s\t%s' "$b" "$d"
}

# Fill ahead/behind vs upstream and a porcelain file-stat breakdown for repo $1.
# Pure git; one rev-list + one status call. Sets the git_* / n* globals above.
compute_git_extra() {
  local root="$1" lr line xy
  lr=$(git -C "$root" -c core.useBuiltinFSMonitor=false \
       rev-list --left-right --count HEAD...@{u} 2>/dev/null)
  if [ -n "$lr" ]; then
    git_ahead=$(printf '%s' "$lr" | awk '{print $1}')
    git_behind=$(printf '%s' "$lr" | awk '{print $2}')
  fi
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    xy="${line:0:2}"
    case "$xy" in
      '??')          nU=$((nU+1)) ;;
      U*|?U|AA|DD)   git_conf=$((git_conf+1)) ;;   # unmerged / conflict
      *)
        case "$xy" in *M*) nM=$((nM+1)) ;; esac
        case "$xy" in A*)  nA=$((nA+1)) ;; esac
        case "$xy" in *D*) nD=$((nD+1)) ;; esac
      ;;
    esac
  done < <(git -C "$root" -c core.useBuiltinFSMonitor=false status --porcelain 2>/dev/null)
}

if git -C "$cwd" rev-parse --git-dir > /dev/null 2>&1; then
  IFS=$'\t' read -r git_branch git_dirty < <(branch_of "$cwd")
  git_root="$cwd"
else
  # cwd is not a repo (e.g. a workspace root): show the branch of the sub-repo
  # under cwd whose .git was touched most recently. Bounded to depth 2 for speed.
  newest_git=$(find "$cwd" -maxdepth 2 -name .git -type d -prune 2>/dev/null \
    | while IFS= read -r g; do
        printf '%s %s\n' "$(stat -f '%m' "$g" 2>/dev/null || echo 0)" "${g%/.git}"
      done | sort -rn | head -1 | cut -d' ' -f2-)
  if [ -n "$newest_git" ]; then
    IFS=$'\t' read -r git_branch git_dirty < <(branch_of "$newest_git")
    git_repo=$(basename "$newest_git")
    git_root="$newest_git"
  fi
fi
[ -n "$git_root" ] && compute_git_extra "$git_root"

# --- Aggregated costs (cached; slow scan runs in background on a TTL) ---
COST_CACHE="${HOME}/.claude/.statusline-cost-cache.json"
COST_SCRIPT="${HOME}/.claude/statusline-costs.py"
COST_LOCK="${HOME}/.claude/.statusline-cost.lock"
COST_TTL=3600          # refresh the aggregate at most once an hour
COST_LOCK_TTL=180      # a scan in flight must finish (or go stale) before respawn
now_epoch=$(date +%s)

cache_age=99999
[ -r "$COST_CACHE" ] && cache_age=$(( now_epoch - $(stat -f '%m' "$COST_CACHE" 2>/dev/null || echo 0) ))
if [ "$cache_age" -ge "$COST_TTL" ] && [ -r "$COST_SCRIPT" ]; then
  lock_age=99999
  [ -f "$COST_LOCK" ] && lock_age=$(( now_epoch - $(stat -f '%m' "$COST_LOCK" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -ge "$COST_LOCK_TTL" ]; then
    ( touch "$COST_LOCK"; python3 "$COST_SCRIPT" >/dev/null 2>&1; rm -f "$COST_LOCK" ) &
  fi
fi

cost_cur=""; cost_avg=""; cost_agent=""; tok_cur=""
if [ -r "$COST_CACHE" ]; then
  read -r cost_cur cost_avg cost_agent tok_cur < <(
    jq -r '"\(.current_month // "") \(.avg_month // "") \(.avg_per_agent // "") \(.current_month_tokens // "")"' "$COST_CACHE" 2>/dev/null
  ) || true
fi

# --- Per-session transcript telemetry (tok/s, compactions, last-response age,
# prompt-cache TTL). Same backgrounded-cache pattern as the cost scan but on a
# short TTL: the script reads only the transcript tail + appended bytes, so it is
# cheap, and time-relative values (age, TTL) are recomputed live in bash from the
# cached last_ts so the countdown stays second-accurate between refreshes. ---
TXT_CACHE="${HOME}/.claude/.statusline-transcript-cache.json"
TXT_SCRIPT="${HOME}/.claude/statusline-transcript.py"
TXT_LOCK="${HOME}/.claude/.statusline-transcript.lock"
TXT_TTL=15            # refresh telemetry at most ~once per refresh-and-a-half
TXT_LOCK_TTL=60
if [ -n "$transcript_path" ] && [ -r "$TXT_SCRIPT" ]; then
  txt_age=99999
  [ -r "$TXT_CACHE" ] && txt_age=$(( now_epoch - $(stat -f '%m' "$TXT_CACHE" 2>/dev/null || echo 0) ))
  if [ "$txt_age" -ge "$TXT_TTL" ]; then
    txt_lock_age=99999
    [ -f "$TXT_LOCK" ] && txt_lock_age=$(( now_epoch - $(stat -f '%m' "$TXT_LOCK" 2>/dev/null || echo 0) ))
    if [ "$txt_lock_age" -ge "$TXT_LOCK_TTL" ]; then
      ( touch "$TXT_LOCK"; python3 "$TXT_SCRIPT" "$transcript_path" >/dev/null 2>&1; rm -f "$TXT_LOCK" ) &
    fi
  fi
fi

txt_path=""; txt_last_ts=""; txt_tok_s=""; txt_compactions=""
if [ -r "$TXT_CACHE" ]; then
  read -r txt_path txt_last_ts txt_tok_s txt_compactions < <(
    jq -r '"\(.path // "") \(.last_ts // "") \(.tok_per_s // "") \(.compactions // "")"' "$TXT_CACHE" 2>/dev/null
  ) || true
  # Only trust the cache if it belongs to THIS session's transcript.
  [ "$txt_path" != "$transcript_path" ] && { txt_last_ts=""; txt_tok_s=""; txt_compactions=""; }
fi

# --- Monthly targets (per-person accountability), configurable ---
BUDGET_CONFIG="${HOME}/.claude/statusline-budget.json"
cache_ttl_min=5   # prompt-cache lifetime: 5 (Pro default) | 60 (Max). Source:
                  # docs.anthropic.com/.../prompt-caching — 5-minute default TTL.
if [ -r "$BUDGET_CONFIG" ]; then
  read -r b_ttl < <(
    jq -r '"\(.cache_ttl_min // 5)"' "$BUDGET_CONFIG" 2>/dev/null
  ) || true
  case "$b_ttl" in ''|*[!0-9]*) ;; *) cache_ttl_min="$b_ttl" ;; esac
fi

# --- Verbosity preset (xs|s|m|l|xl) -----------------------------------------
# Controls how many lines render and how wide the bars are. Resolution order:
#   1. $STATUSLINE_SIZE env  2. .size in statusline-budget.json  3. default "l".
# Tiers are monotonic — each larger size is a superset of the smaller one:
#   xs  1 line : identity + context bar             (CTX 6)
#   s   2 lines: + git, session (tokens + cost)      (CTX 8)
#   m   3 lines: + rate limits + churn               (CTX 10)
#   l   5 lines: + $ & token budget gauges + resets  (CTX 10, BW 12)  [default]
#   xl  5 lines: everything, widest bars + avg/mo    (CTX 16, BW 20)
SIZE="${STATUSLINE_SIZE:-}"
if [ -z "$SIZE" ] && [ -r "$BUDGET_CONFIG" ]; then
  SIZE=$(jq -r '.size // empty' "$BUDGET_CONFIG" 2>/dev/null) || SIZE=""
fi
case "$SIZE" in xs|s|m|l|xl) ;; *) SIZE="l" ;; esac
case "$SIZE" in
  xs) RANK=0; CTX_W=6;  BW=10 ;;
  s)  RANK=1; CTX_W=8;  BW=10 ;;
  m)  RANK=2; CTX_W=10; BW=12 ;;
  l)  RANK=3; CTX_W=10; BW=12 ;;
  xl) RANK=4; CTX_W=16; BW=20 ;;
esac

# --- Colors: AI Architect DS — ink (instrument) surface palette (no DIM) ---
# source: AI Architect Design System tokens/colors.css (:root ink-surface
# primitives), each oklch value converted with CSS Color 4 math (scripted
# oklch->srgb). Chrome is the warm-neutral fg scale; status = ok/warn/danger;
# the one accent is terracotta; remaining hues come from the stage/valence
# data families so no two semantically-different segments share a hue.
RESET="\033[0m"
TEXT="\033[38;2;243;241;238m"     # #f3f1ee — primary text · DS --fg-0 oklch(96% 0.005 80) · scripted oklch->srgb
SUBTEXT="\033[38;2;192;189;186m"  # #c0bdba — secondary text / labels · DS --fg-1 oklch(80% 0.006 70) · scripted oklch->srgb
OVERLAY="\033[38;2;136;134;130m"  # #888682 — separators / muted · DS --fg-2 oklch(62% 0.006 70) · scripted oklch->srgb
GREEN="\033[38;2;101;201;140m"    # #65c98c — DS --ok oklch(76% 0.13 155) · scripted oklch->srgb
YELLOW="\033[38;2;232;170;78m"    # #e8aa4e — DS --warn oklch(78% 0.13 75) · scripted oklch->srgb
RED="\033[38;2;232;97;84m"        # #e86154 — DS --danger oklch(66% 0.17 28) · scripted oklch->srgb
PEACH="\033[38;2;207;110;57m"     # #cf6e39 — DS --accent (terracotta) oklch(64% 0.14 47) · scripted oklch->srgb
TEAL="\033[38;2;0;196;189m"       # #00c4bd — DS --stage-early oklch(74% 0.14 190) · scripted oklch->srgb
SKY="\033[38;2;87;184;227m"       # #57b8e3 — DS --info oklch(74% 0.11 230) · scripted oklch->srgb
BLUE="\033[38;2;59;199;255m"      # #3bc7ff — DS --stage-labile oklch(78% 0.15 230) · scripted oklch->srgb
MAUVE="\033[38;2;203;134;219m"    # #cb86db — DS --stage-recon oklch(72% 0.14 320) · scripted oklch->srgb
LAVENDER="\033[38;2;190;140;225m" # #be8ce1 — DS --emo-conflct oklch(72% 0.13 310) · scripted oklch->srgb
SAPPHIRE="\033[38;2;103;176;249m" # #67b0f9 — DS --emo-discov oklch(74% 0.13 250) · scripted oklch->srgb
# back-compat aliases used below
WHITE="$TEXT"; LGREY="$SUBTEXT"; CYAN="$TEAL"; MAGENTA="$MAUVE"
SEP="${OVERLAY}│${RESET}"

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

# interpolate a position (0..100) on the DS ok→warn→accent→danger ramp,
# echoing "r;g;b". Continuous RGB lerp across three segments so the gradient is
# smooth per cell (not 4 flat color blocks). Anchors (DS tokens/colors.css,
# scripted oklch->srgb): --ok #65c98c (0), --warn #e8aa4e (40),
# --accent #cf6e39 (70), --danger #e86154 (100).
grad_rgb() {
  local p="$1" t r g b
  [ "$p" -lt 0 ] && p=0; [ "$p" -gt 100 ] && p=100
  if   [ "$p" -lt 40 ]; then t=$(( p * 100 / 40 ))
       r=$(( 101 + (232-101)*t/100 )); g=$(( 201 + (170-201)*t/100 )); b=$(( 140 + (78-140)*t/100 ))
  elif [ "$p" -lt 70 ]; then t=$(( (p-40) * 100 / 30 ))
       r=$(( 232 + (207-232)*t/100 )); g=$(( 170 + (110-170)*t/100 )); b=$(( 78 + (57-78)*t/100 ))
  else                       t=$(( (p-70) * 100 / 30 ))
       r=$(( 207 + (232-207)*t/100 )); g=$(( 110 + (97-110)*t/100 )); b=$(( 57 + (84-57)*t/100 ))
  fi
  printf '%d;%d;%d' "$r" "$g" "$b"
}

# render a gradient block bar of $2 cells filled to $1 percent (clamped 0..width).
# Each filled cell is colored by its POSITION along the full width via grad_rgb,
# a smooth green→yellow→peach→red gradient, so a longer (more-full) bar visibly
# reaches into the red. Empty cells stay muted. The bar is self-coloring:
# callers must NOT wrap the result in a single color (trailing RESET closes it).
make_bar() {
  local p="$1" w="$2" filled empty i pos rgb b=""
  filled=$(( (p * w + 50) / 100 ))   # round to nearest cell
  [ "$filled" -gt "$w" ] && filled="$w"
  [ "$filled" -lt 0 ] && filled=0
  empty=$(( w - filled ))
  i=0
  while [ "$i" -lt "$filled" ]; do
    pos=$(( w > 1 ? i * 100 / (w - 1) : 0 ))   # cell position 0..100
    rgb=$(grad_rgb "$pos")
    b="${b}\033[38;2;${rgb}m█"
    i=$(( i + 1 ))
  done
  [ "$filled" -gt 0 ] && b="${b}${RESET}"
  [ "$empty" -gt 0 ] && { printf -v e "%${empty}s"; b="${b}${OVERLAY}${e// /░}${RESET}"; }
  printf '%s' "$b"
}

# pick color for a given token count
token_color() {
  local t="$1"
  if   [ "$t" -ge "$SAVE_TOKENS" ]; then echo "$RED"
  elif [ "$t" -ge "$WARN_TOKENS" ]; then echo "$YELLOW"
  else echo "$GREEN"; fi
}

fmt_tokens() {
  local t="$1"
  if   [ "$t" -ge 1000000000 ]; then LC_NUMERIC=C awk "BEGIN{printf \"%.1fG\",$t/1000000000}"
  elif [ "$t" -ge 1000000 ];    then LC_NUMERIC=C awk "BEGIN{printf \"%.1fM\",$t/1000000}"
  elif [ "$t" -ge 1000 ];       then LC_NUMERIC=C awk "BEGIN{printf \"%.0fk\",$t/1000}"
  else echo "$t"; fi
}

# --- Rate-limit reset times -----------------------------------------------
# .rate_limits.*.resets_at is an epoch in SECONDS (verified against both the
# large and xlarge variants of github.com/AwesomeJun/CC-statusline, which
# subtract it from `date +%s` directly). Guard: only treat all-digit values as
# an epoch so a future ISO string can never crash the arithmetic.
# Cross-platform date: BSD/macOS `date -j -f %s` | `date -r` | GNU `date -d @`.
_date_fmt() {
  local epoch="$1" fmt="$2" out=""
  out=$(date -j -f "%s" "$epoch" "+$fmt" 2>/dev/null) && [ -n "$out" ] && { echo "$out"; return; }
  out=$(date -r "$epoch" "+$fmt" 2>/dev/null) && [ -n "$out" ] && { echo "$out"; return; }
  date -d "@$epoch" "+$fmt" 2>/dev/null
}

# 5h window: "Xh Ym" remaining until reset
fmt_reset_in() {
  local e="$1"
  case "$e" in ''|*[!0-9]*) return ;; esac
  local rem=$(( e - $(date +%s) )); [ "$rem" -lt 0 ] && rem=0
  printf '%dh%dm' "$(( rem / 3600 ))" "$(( (rem % 3600) / 60 ))"
}

# compact duration: "Xh Ym" / "Xm Ys" / "Xs" from a seconds count
fmt_dur() {
  local s="$1"
  case "$s" in ''|*[!0-9]*) s=0 ;; esac
  if   [ "$s" -ge 3600 ]; then printf '%dh%dm' "$(( s / 3600 ))" "$(( (s % 3600) / 60 ))"
  elif [ "$s" -ge 60 ];   then printf '%dm%ds' "$(( s / 60 ))" "$(( s % 60 ))"
  else printf '%ds' "$s"; fi
}

# 7d window: "Wed 14:00" wall-clock of reset
fmt_reset_at() {
  local e="$1"
  case "$e" in ''|*[!0-9]*) return ;; esac
  LC_TIME=C _date_fmt "$e" "%a %H:%M"
}

# Lines are grouped one concern per line (identity / git / session / $ target /
# token target). Empty groups are skipped at emit time, so the statusline grows
# and shrinks with what is actually present instead of cramming everything.

# =========================================================================
# LINE: identity — model, effort, thinking, directory
# =========================================================================
l_id="${MAUVE}🤖 ${WHITE}${model}${RESET}"
if [ -n "$effort" ]; then
  l_id="${l_id} ${SEP} ${PEACH}⚡ ${effort}${RESET}"
  [ "$thinking" = "true" ] && l_id="${l_id} ${YELLOW}💡${RESET}"
fi
l_id="${l_id} ${SEP} ${TEAL}📂 ${dir}${RESET}"

# =========================================================================
# LINE: git — branch (+dirty +fallback repo), worktree, PR
# =========================================================================
l_git=""
if [ -n "$git_branch" ]; then
  dirty_part=""
  [ -n "$git_dirty" ] && dirty_part=" ${RED}${git_dirty}${RESET}"
  repo_part=""
  [ -n "$git_repo" ] && repo_part="${OVERLAY}@${git_repo}${RESET}"
  l_git="${GREEN}🌿 ${WHITE}${git_branch}${RESET}${dirty_part}${repo_part}"

  # ahead/behind vs upstream + conflicts (s+). Only nonzero counts render.
  if [ "$RANK" -ge 1 ]; then
    { [ -n "$git_ahead" ]  && [ "$git_ahead"  -gt 0 ]; } && l_git="${l_git} ${GREEN}↑${git_ahead}${RESET}"
    { [ -n "$git_behind" ] && [ "$git_behind" -gt 0 ]; } && l_git="${l_git} ${YELLOW}↓${git_behind}${RESET}"
    [ "$git_conf" -gt 0 ] && l_git="${l_git} ${RED}⚠${git_conf}${RESET}"
  fi
  # file-stat breakdown (m+): !modified +added ✘deleted ?untracked, nonzero only.
  if [ "$RANK" -ge 2 ]; then
    fs=""
    [ "$nM" -gt 0 ] && fs="${fs:+$fs }${YELLOW}!${nM}${RESET}"
    [ "$nA" -gt 0 ] && fs="${fs:+$fs }${GREEN}+${nA}${RESET}"
    [ "$nD" -gt 0 ] && fs="${fs:+$fs }${RED}✘${nD}${RESET}"
    [ "$nU" -gt 0 ] && fs="${fs:+$fs }${OVERLAY}?${nU}${RESET}"
    [ -n "$fs" ] && l_git="${l_git} ${SEP} ${fs}"
  fi
fi
if [ -n "$wt_name" ]; then
  l_git="${l_git:+$l_git ${SEP} }${MAUVE}⎇ ${wt_name}${RESET}"
fi
if [ -n "$pr_num" ]; then
  pr_color="$SUBTEXT"
  case "$pr_state" in
    approved)          pr_color="$GREEN" ;;
    changes_requested) pr_color="$RED" ;;
    pending)           pr_color="$YELLOW" ;;
  esac
  l_git="${l_git:+$l_git ${SEP} }${pr_color}🔀 PR#${pr_num}${RESET}"
fi

# =========================================================================
# LINE: session — context bar, tokens, cost, duration, rate limits, churn
# =========================================================================
line2=""

if [ -n "$used_pct" ] || [ -n "$in_tokens" ]; then
  # color by absolute token count against the save threshold (works for 1M window)
  tok="${in_tokens:-0}"
  cc=$(token_color "$tok")

  if [ -n "$used_pct" ]; then
    pct_int=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$used_pct}")
    bar=$(make_bar "$pct_int" "$CTX_W")
    line2="${SUBTEXT}🧠 ${RESET}${bar} ${cc}${pct_int}%${RESET}"
  fi

  if [ -n "$in_tokens" ] && [ "$RANK" -ge 1 ]; then
    line2="${line2:+$line2 }${cc}$(fmt_tokens "$in_tokens") tok${RESET}"
    # explicit checkpoint hint once past the save threshold
    [ "$in_tokens" -ge "$SAVE_TOKENS" ] && line2="${line2} ${RED}⚠ save+recall${RESET}"
  fi
fi

DOLLAR='$'
if [ -n "$cost" ] && [ "$RANK" -ge 1 ]; then
  cost_fmt=$(LC_NUMERIC=C awk "BEGIN{printf \"%.2f\",$cost}")
  line2="${line2:+$line2 ${SEP} }${YELLOW}💰 ${DOLLAR}${cost_fmt}${RESET}"
fi

if [ -n "$dur_ms" ] && [ "$RANK" -ge 2 ]; then
  dur_s=$((dur_ms / 1000)); mins=$((dur_s / 60)); secs=$((dur_s % 60))
  line2="${line2:+$line2 ${SEP} }${SUBTEXT}⏱ ${mins}m${secs}s${RESET}"
fi

# Inline rate limits only at the m preset; l/xl render full quota bars below.
if { [ -n "$rl_5h" ] || [ -n "$rl_7d" ]; } && [ "$RANK" -eq 2 ]; then
  rl_seg=""
  if [ -n "$rl_5h" ]; then
    v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_5h}")
    if [ "$v" -ge 80 ]; then c="$RED"; elif [ "$v" -ge 50 ]; then c="$YELLOW"; else c="$SUBTEXT"; fi
    rp=""; [ "$RANK" -ge 3 ] && r=$(fmt_reset_in "$rl_5h_at") && [ -n "$r" ] && rp=" ${OVERLAY}↻${r}${RESET}"
    rl_seg="${c}🚀 5h ${v}%${RESET}${rp}"
  fi
  if [ -n "$rl_7d" ]; then
    v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_7d}")
    if [ "$v" -ge 80 ]; then c="$RED"; elif [ "$v" -ge 50 ]; then c="$YELLOW"; else c="$SUBTEXT"; fi
    rp=""; [ "$RANK" -ge 3 ] && r=$(fmt_reset_at "$rl_7d_at") && [ -n "$r" ] && rp=" ${OVERLAY}↻${r}${RESET}"
    rl_seg="${rl_seg:+$rl_seg }${c}🌟 7d ${v}%${RESET}${rp}"
  fi
  line2="${line2:+$line2 ${SEP} }${rl_seg}"
fi

if { [ "$adds" -gt 0 ] || [ "$dels" -gt 0 ]; } && [ "$RANK" -ge 2 ]; then
  line2="${line2:+$line2 ${SEP} }${SUBTEXT}✏️ ${GREEN}+${adds}${OVERLAY}/${RED}-${dels}${RESET}"
fi

# Subagents: count · tokens · cost — surfaces work the statusline input omits.
if [ -n "$sub_count" ]; then
  sub_seg="${MAGENTA}🤖${sub_count}${RESET}"
  if [ -n "$sub_tokens" ] && [ "$sub_tokens" -gt 0 ] 2>/dev/null; then
    sub_seg="${sub_seg} ${LGREY}$(fmt_tokens "$sub_tokens")${RESET}"
  fi
  if [ -n "$sub_cost" ]; then
    sub_cost_fmt=$(LC_NUMERIC=C awk "BEGIN{printf \"%.2f\",$sub_cost}" 2>/dev/null)
    [ -n "$sub_cost_fmt" ] && sub_seg="${sub_seg} ${YELLOW}${DOLLAR}${sub_cost_fmt}${RESET}"
  fi
  line2="${line2:+$line2 ${SEP} }${sub_seg}"
fi

# =========================================================================
# LINE: telemetry — turn throughput, last-response age, compactions, cache TTL
# (from the backgrounded transcript scan; age + TTL recomputed live here).
# =========================================================================
l_tele=""
if [ "$RANK" -ge 2 ]; then
  # turn throughput (tok/s) — wall-clock, includes tool latency (lower bound)
  if [ -n "$txt_tok_s" ] && [ "$txt_tok_s" != "null" ]; then
    ts_int=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$txt_tok_s}")
    [ "$ts_int" -gt 0 ] && l_tele="${TEAL}⚡ ${ts_int} t/s${RESET}"
  fi

  # last-response age + prompt-cache TTL countdown, both from last_ts
  if [ -n "$txt_last_ts" ] && [ "$txt_last_ts" != "null" ]; then
    age_s=$(LC_NUMERIC=C awk "BEGIN{a=$now_epoch-$txt_last_ts; printf \"%d\", (a>0)?a:0}")
    l_tele="${l_tele:+$l_tele ${SEP} }${SUBTEXT}🕑 $(fmt_dur "$age_s")${RESET}"

    # cache warm until last_ts + cache_ttl_min; show remaining, red when cold.
    ttl_s=$(( cache_ttl_min * 60 ))
    rem_s=$(( ttl_s - age_s ))
    if [ "$rem_s" -gt 0 ]; then
      cc="$GREEN"; [ "$rem_s" -lt 60 ] && cc="$YELLOW"
      l_tele="${l_tele:+$l_tele ${SEP} }${cc}❄ $(fmt_dur "$rem_s")${RESET}"
    else
      l_tele="${l_tele:+$l_tele ${SEP} }${RED}❄ cold${RESET}"
    fi
  fi

  # context compactions this session (only when any have happened)
  if [ -n "$txt_compactions" ] && [ "$txt_compactions" != "null" ] && [ "$txt_compactions" -gt 0 ] 2>/dev/null; then
    l_tele="${l_tele:+$l_tele ${SEP} }${MAUVE}🗜 ${txt_compactions}${RESET}"
  fi
fi

# =========================================================================
# LINE: quota — Pro/Max rate-limit windows: the REAL "don't overshoot" cap.
#   .used_percentage is already a ratio of the quota the account is bound to,
#   so 100% = the cap (lockout). 5h = burst window, 7d = sustained window.
#   This replaces the old arbitrary $/token monthly budgets: on a flat-rate
#   Pro/Max plan the binding constraint is the quota, not an absolute spend.
# LINE: cost reference — informational $/month + $/run (NOT a cap), kept on
#   the user's request alongside the quota gauges.
# =========================================================================
fmt_usd() {
  local v="$1"
  LC_NUMERIC=C awk "BEGIN{ if($v>=1000) printf \"\$%.1fk\",$v/1000; else printf \"\$%.2f\",$v }"
}

# color a quota ratio: green < 50, yellow 50–79, red >= 80 (warn before lockout)
quota_color() {
  local p="$1"
  if   [ "$p" -ge 80 ]; then echo "$RED"
  elif [ "$p" -ge 50 ]; then echo "$YELLOW"
  else echo "$GREEN"; fi
}

# BW (bar width) is set by the verbosity preset above.
l_quota5h=""
if [ -n "$rl_5h" ]; then
  v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_5h}")
  qc=$(quota_color "$v"); qb=$(make_bar "$v" "$BW")
  qover=""; [ "$v" -ge 100 ] && qover="${RED} ⚠${RESET}"
  rp=""; r=$(fmt_reset_in "$rl_5h_at") && [ -n "$r" ] && rp=" ${OVERLAY}↻${r}${RESET}"
  l_quota5h="${PEACH}🎯 🚀 5h${RESET} ${qb} ${qc}${v}%${RESET}${rp}${qover}"
fi

l_quota7d=""
if [ -n "$rl_7d" ]; then
  v=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$rl_7d}")
  qc=$(quota_color "$v"); qb=$(make_bar "$v" "$BW")
  qover=""; [ "$v" -ge 100 ] && qover="${RED} ⚠${RESET}"
  rp=""; r=$(fmt_reset_at "$rl_7d_at") && [ -n "$r" ] && rp=" ${OVERLAY}↻${r}${RESET}"
  l_quota7d="${PEACH}🎯 🌟 7d${RESET} ${qb} ${qc}${v}%${RESET}${rp}${qover}"
fi

l_costref=""
[ -n "$cost_cur" ]   && l_costref="${SUBTEXT}💰 $(fmt_usd "$cost_cur") ce mois${RESET}"
[ -n "$cost_agent" ] && l_costref="${l_costref:+$l_costref ${SEP} }${SAPPHIRE}🤖 ${YELLOW}$(fmt_usd "$cost_agent")${OVERLAY}/run${RESET}"
[ "$RANK" -ge 4 ] && [ -n "$cost_avg" ] && l_costref="${l_costref:+$l_costref ${SEP} }${OVERLAY}(moy $(fmt_usd "$cost_avg")/mo)${RESET}"

# --- Emit (%b interprets ANSI escapes; data is in args, not the format) ---
# One concern per line; empty groups are skipped so the block stays compact.
# Line count grows with the verbosity preset (RANK): xs collapses identity +
# context onto a single line; git appears at s+; budget gauges at l+.
if [ "$RANK" -le 0 ]; then
  printf '%b\n' "${l_id}${line2:+ ${SEP} ${line2}}"
  exit 0
fi
printf '%b\n' "$l_id"
[ -n "$l_git" ]    && printf '%b\n' "$l_git"
[ -n "$line2" ]    && printf '%b\n' "$line2"
[ -n "$l_tele" ]   && printf '%b\n' "$l_tele"
[ "$RANK" -ge 3 ] && [ -n "$l_quota5h" ] && printf '%b\n' "$l_quota5h"
[ "$RANK" -ge 3 ] && [ -n "$l_quota7d" ] && printf '%b\n' "$l_quota7d"
[ "$RANK" -ge 3 ] && [ -n "$l_costref" ] && printf '%b\n' "$l_costref"
exit 0
