# shellcheck shell=bash
# shellcheck disable=SC2154,SC2153  # Every renderer here is a projection of the
# facts the collectors resolved (model, git_*, cost_*, RANK, the palette). Those
# globals are assigned in sibling modules, which shellcheck does not follow, so
# it reads every one of them as unassigned. Each function's `reads:` line is the
# contract; the golden render diff is what enforces it.
# statusline-lib/render.sh — one function per status line.
#
# Single responsibility: composing the collected facts into text. Changes when
# what is displayed, or in what order, changes. Nothing here reads a file, runs
# git, or probes the terminal — each function is a pure projection of globals
# the collectors already resolved, and each PRINTS its line rather than
# assigning one, so the composition root decides what to do with it.
#
# Lines are grouped one concern per line (identity / git / session / telemetry /
# quota / spend). Empty lines are skipped at emit time, so the statusline grows
# and shrinks with what is actually present instead of cramming everything.
#
# Every line is built MOST-IMPORTANT-FIRST: fit_line drops whole trailing
# segments when the terminal is too narrow, so segment order IS the priority
# order. A segment moved right is a segment volunteered for deletion.
#
# Typography: no emoji or pictographs anywhere. Every line opens with an
# explicit lowercase word label naming its concern, and every value inside a
# line is preceded by the word for what it measures. Rationale: a glyph has to
# be learned and is ambiguous across terminal fonts (and several rendered as
# double-width, breaking column budgets); a word is self-describing and reads
# the same everywhere. Status is carried by colour + number, never by an icon.
#
# Depends on: palette.sh, severity.sh, format.sh.

DOLLAR='$'

# render_identity — model, directory, effort, thinking.
# reads: model dir effort thinking
# The directory sits second, immediately after the model, because segment order
# IS priority order here: fit_line drops from the tail, and on a narrow terminal
# "which tree am I in" is the segment worth keeping. Effort and thinking are
# session settings the reader chose and can recall; the directory changes under
# them, and mistaking it is how work lands in the wrong repository. The previous
# order put dir last and therefore volunteered it first.
render_identity() {
  local line="${LABEL}model ${WHITE}${model}${RESET}"
  line="${line} ${SEP} ${KEY}dir ${TEXT}${dir}${RESET}"
  if [ -n "$effort" ]; then
    # sole accent usage (DS G4): effort is the one user-selected dial per session
    line="${line} ${SEP} ${KEY}effort ${PEACH}${effort}${RESET}"
    [ "$thinking" = "true" ] && line="${line} ${SEP} ${KEY}thinking ${SUBTEXT}on${RESET}"
  fi
  printf '%s' "$line"
}

# render_git — branch (+dirty +fallback repo), ahead/behind, file stats,
# worktree, PR. Prints nothing when there is no repository and no worktree/PR.
# reads: git_branch git_dirty git_repo git_ahead git_behind git_conf
#        nM nA nD nU wt_name pr_num pr_state RANK
render_git() {
  local line="" dirty_part repo_part fs pr_color pr_word
  if [ -n "$git_branch" ]; then
    dirty_part=""
    [ -n "$git_dirty" ] && dirty_part=" ${RED}modified${RESET}"
    repo_part=""
    [ -n "$git_repo" ] && repo_part="${OVERLAY}@${git_repo}${RESET}"
    line="${LABEL}branch ${WHITE}${git_branch}${RESET}${dirty_part}${repo_part}"

    # ahead/behind vs upstream + conflicts (s+). Only nonzero counts render.
    if [ "$RANK" -ge 1 ]; then
      { [ -n "$git_ahead" ]  && [ "$git_ahead"  -gt 0 ]; } && line="${line} ${SEP} ${KEY}ahead ${GREEN}${git_ahead}${RESET}"
      { [ -n "$git_behind" ] && [ "$git_behind" -gt 0 ]; } && line="${line} ${SEP} ${KEY}behind ${YELLOW}${git_behind}${RESET}"
      [ "$git_conf" -gt 0 ] && line="${line} ${SEP} ${KEY}conflicts ${RED}${git_conf}${RESET}"
    fi
    # file-stat breakdown (m+): nonzero counts only, each named in full.
    if [ "$RANK" -ge 2 ]; then
      fs=""
      [ "$nM" -gt 0 ] && fs="${fs:+$fs }${KEY}mod ${YELLOW}${nM}${RESET}"
      [ "$nA" -gt 0 ] && fs="${fs:+$fs }${KEY}add ${GREEN}${nA}${RESET}"
      [ "$nD" -gt 0 ] && fs="${fs:+$fs }${KEY}del ${RED}${nD}${RESET}"
      [ "$nU" -gt 0 ] && fs="${fs:+$fs }${KEY}untracked ${SUBTEXT}${nU}${RESET}"
      [ -n "$fs" ] && line="${line} ${SEP} ${fs}"
    fi
  fi
  if [ -n "$wt_name" ]; then
    line="${line:+$line ${SEP} }${KEY}worktree ${TEXT}${wt_name}${RESET}"
  fi
  if [ -n "$pr_num" ]; then
    pr_color="$SUBTEXT"
    pr_word=""
    case "$pr_state" in
      approved)          pr_color="$GREEN";  pr_word=" approved" ;;
      changes_requested) pr_color="$RED";    pr_word=" changes requested" ;;
      pending)           pr_color="$YELLOW"; pr_word=" pending" ;;
    esac
    line="${line:+$line ${SEP} }${KEY}pr ${pr_color}${pr_num}${pr_word}${RESET}"
  fi
  printf '%s' "$line"
}

# render_session — context bar, tokens, cost, duration, rate limits, churn,
# subagents.
# reads: used_pct in_tokens CTX_W RANK SAVE_TOKENS cost_session cost dur_ms
#        rl_5h rl_7d rl_5h_at rl_7d_at now_epoch adds dels sub_count sub_tokens
render_session() {
  local line="" tok cc pct_int bar cost_fmt dur_s mins secs
  local rl_seg v vrank vprank vratio sub_seg

  if [ -n "$used_pct" ] || [ -n "$in_tokens" ]; then
    # color by absolute token count against the save threshold (works for 1M window)
    tok="${in_tokens:-0}"
    cc=$(token_color "$tok")

    if [ -n "$used_pct" ]; then
      pct_int=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$used_pct}")
      bar=$(make_bar "$pct_int" "$CTX_W")
      line="${LABEL}context ${RESET}${bar} ${cc}${pct_int}%${RESET}"
    fi

    if [ -n "$in_tokens" ] && [ "$RANK" -ge 1 ]; then
      line="${line:+$line }${cc}$(fmt_tokens "$in_tokens") tokens${RESET}"
      # explicit checkpoint hint once past the save threshold
      [ "$in_tokens" -ge "$SAVE_TOKENS" ] && line="${line} ${RED}save and recall now${RESET}"
    fi
  fi

  # Session spend. Prefers the ledger row (main thread + every subagent, deduped
  # and priced from this session's own transcript) over .cost.total_cost_usd,
  # which counts the main thread only and additionally carries prior spend
  # forward on a resumed session. Falls back to the harness figure, labelled
  # "main" so an understated number is never shown as if it were the total.
  if [ "$RANK" -ge 1 ]; then
    if [ -n "$cost_session" ]; then
      cost_fmt=$(LC_NUMERIC=C awk "BEGIN{printf \"%.2f\",$cost_session}")
      line="${line:+$line ${SEP} }${KEY}session ${SUBTEXT}${DOLLAR}${cost_fmt}${RESET}"
    elif [ -n "$cost" ]; then
      cost_fmt=$(LC_NUMERIC=C awk "BEGIN{printf \"%.2f\",$cost}")
      line="${line:+$line ${SEP} }${KEY}session main ${SUBTEXT}${DOLLAR}${cost_fmt}${RESET}"
    fi
  fi

  if [ -n "$dur_ms" ] && [ "$RANK" -ge 2 ]; then
    dur_s=$((dur_ms / 1000)); mins=$((dur_s / 60)); secs=$((dur_s % 60))
    line="${line:+$line ${SEP} }${KEY}elapsed ${SUBTEXT}${mins}m${secs}s${RESET}"
  fi

  # Inline rate limits only at the m preset; l/xl render full quota bars below.
  # Percentage and colour come from quota_reading, the same resolver the full
  # quota lines use, so the compact and the expanded rendering of one window can
  # never show different severities. No reset time here — at m the line is
  # already carrying context, cost, elapsed and churn.
  if { [ -n "$rl_5h" ] || [ -n "$rl_7d" ]; } && [ "$RANK" -eq 2 ]; then
    rl_seg=""
    if read -r v vrank vprank vratio < <(quota_reading "$rl_5h" "$rl_5h_at" "$RL_5H_WINDOW_S" "$now_epoch"); then
      rl_seg="${KEY}quota 5h $(rank_color "$vrank")${v}%${RESET}"
      [ -n "$vratio" ] && rl_seg="${rl_seg} ${KEY}pace $(rank_color "$vprank")$(fmt_pace "$vratio")${RESET}"
    fi
    if read -r v vrank vprank vratio < <(quota_reading "$rl_7d" "$rl_7d_at" "$RL_7D_WINDOW_S" "$now_epoch"); then
      rl_seg="${rl_seg:+$rl_seg ${SEP} }${KEY}quota 7d $(rank_color "$vrank")${v}%${RESET}"
      [ -n "$vratio" ] && rl_seg="${rl_seg} ${KEY}pace $(rank_color "$vprank")$(fmt_pace "$vratio")${RESET}"
    fi
    line="${line:+$line ${SEP} }${rl_seg}"
  fi

  if { [ "$adds" -gt 0 ] || [ "$dels" -gt 0 ]; } && [ "$RANK" -ge 2 ]; then
    line="${line:+$line ${SEP} }${KEY}edits ${GREEN}+${adds}${OVERLAY}/${RED}-${dels}${RESET}"
  fi

  # Subagents: live count and token volume for THIS session, from the
  # SubagentStop tracker. Their dollar cost is not repeated here — it is already
  # inside the "session" figure above, which the ledger prices from the
  # subagents/ subtree. Showing it twice would read as additive.
  if [ -n "$sub_count" ]; then
    sub_seg="${KEY}subagents ${SUBTEXT}${sub_count}${RESET}"
    if [ -n "$sub_tokens" ] && [ "$sub_tokens" -gt 0 ] 2>/dev/null; then
      sub_seg="${sub_seg} ${LGREY}$(fmt_tokens "$sub_tokens") tokens${RESET}"
    fi
    line="${line:+$line ${SEP} }${sub_seg}"
  fi
  printf '%s' "$line"
}

# render_telemetry — turn throughput, last-response age, cache TTL, compactions.
# The age and the TTL countdown are recomputed here from the cached last_ts, so
# they stay second-accurate between the backgrounded scans.
# reads: RANK txt_tok_s txt_last_ts txt_compactions now_epoch cache_ttl_min
render_telemetry() {
  local line="" ts_int age_s ttl_s rem_s cc
  [ "$RANK" -ge 2 ] || { printf ''; return; }

  # turn throughput (tok/s) — wall-clock, includes tool latency (lower bound)
  if [ -n "$txt_tok_s" ] && [ "$txt_tok_s" != "null" ]; then
    ts_int=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$txt_tok_s}")
    [ "$ts_int" -gt 0 ] && line="${LABEL}throughput ${SUBTEXT}${ts_int} tokens/s${RESET}"
  fi

  # last-response age + prompt-cache TTL countdown, both from last_ts
  if [ -n "$txt_last_ts" ] && [ "$txt_last_ts" != "null" ]; then
    age_s=$(LC_NUMERIC=C awk "BEGIN{a=$now_epoch-$txt_last_ts; printf \"%d\", (a>0)?a:0}")
    line="${line:+$line ${SEP} }${KEY}idle ${SUBTEXT}$(fmt_dur "$age_s")${RESET}"

    # cache warm until last_ts + cache_ttl_min; show remaining, red when cold.
    ttl_s=$(( cache_ttl_min * 60 ))
    rem_s=$(( ttl_s - age_s ))
    if [ "$rem_s" -gt 0 ]; then
      cc="$GREEN"; [ "$rem_s" -lt 60 ] && cc="$YELLOW"
      line="${line:+$line ${SEP} }${KEY}cache warm ${cc}$(fmt_dur "$rem_s")${RESET}"
    else
      line="${line:+$line ${SEP} }${KEY}cache ${RED}cold${RESET}"
    fi
  fi

  # context compactions this session (only when any have happened)
  if [ -n "$txt_compactions" ] && [ "$txt_compactions" != "null" ] && [ "$txt_compactions" -gt 0 ] 2>/dev/null; then
    line="${line:+$line ${SEP} }${KEY}compactions ${SUBTEXT}${txt_compactions}${RESET}"
  fi
  printf '%s' "$line"
}

# render_quota — one rate-limit window: the REAL "don't overshoot" cap.
#   .used_percentage is already a ratio of the quota the account is bound to, so
#   100% = the cap (lockout). 5h = burst window, 7d = sustained window. This
#   replaces the old arbitrary $/token monthly budgets: on a flat-rate Pro/Max
#   plan the binding constraint is the quota, not an absolute spend.
# pre:  $1 the window label as displayed ("5h"|"7d") — the statusLine input
#       reports exactly these two windows, so the set is closed by the host's
#       own contract, not open-ended; $2 raw used_percentage, $3 resets_at
#       epoch, $4 window length in seconds.
# post: prints the line and returns 0, or prints nothing and returns 1 when the
#       window is absent or unreadable.
# reads: BW now_epoch
# The bar stays on the heat track (coloured by position, DS gate G6) and is not
# a status colour; the percentage carries the combined absolute+pace severity.
render_quota() {
  local label="$1" raw="$2" at="$3" win="$4" v vrank vprank vratio qb line r
  read -r v vrank vprank vratio < <(quota_reading "$raw" "$at" "$win" "$now_epoch") || return 1
  qb=$(make_bar "$v" "$BW")
  line="${LABEL}quota ${label}${RESET}  ${qb} $(rank_color "$vrank")${v}%${RESET}"
  [ -n "$vratio" ] && line="${line} ${SEP} ${KEY}pace $(rank_color "$vprank")$(fmt_pace "$vratio")${RESET}"
  # A countdown is what the reader wants on a window that turns over five times
  # a day; on the 7-day one it would read "138h12m" and mean nothing, so that
  # window shows the wall-clock instant instead.
  case "$label" in
    5h) r=$(fmt_reset_in "$at") && [ -n "$r" ] && line="${line} ${SEP} ${KEY}resets in ${SUBTEXT}${r}${RESET}" ;;
    *)  r=$(fmt_reset_at "$at") && [ -n "$r" ] && line="${line} ${SEP} ${KEY}resets ${SUBTEXT}${r}${RESET}" ;;
  esac
  [ "$v" -ge 100 ] && line="${line} ${SEP} ${RED}over cap${RESET}"
  printf '%s' "$line"
}

# render_spend — today / month-to-date / average per day, all read from the ONE
# ledger. Informational, not a cap: on a flat-rate Pro/Max plan the binding
# constraint is the quota above, not an absolute spend. Every figure includes
# subagent spend, and all three come from the same ledger rows as the "session"
# segment on the context line, so they are consistent by construction.
# reads: cost_today cost_month cost_avg_day RANK
render_spend() {
  local line=""
  [ -n "$cost_today" ] && line="${LABEL}spend today ${SUBTEXT}$(fmt_usd "$cost_today")${RESET}"
  [ -n "$cost_month" ] && line="${line:+$line ${SEP} }${KEY}month ${SUBTEXT}$(fmt_usd "$cost_month")${RESET}"
  [ "$RANK" -ge 4 ] && [ -n "$cost_avg_day" ] && line="${line:+$line ${SEP} }${KEY}average ${SUBTEXT}$(fmt_usd "$cost_avg_day")${OVERLAY}/day${RESET}"
  printf '%s' "$line"
}
