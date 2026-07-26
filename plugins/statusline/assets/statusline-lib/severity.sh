# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/severity.sh — the one severity scale and its thresholds.
#
# Single responsibility: deciding how alarming a number is. Every
# threshold-driven colour on this statusline resolves through this module, so
# two readings of the same quantity can never disagree about what "warn" means.
# It changes when a threshold policy changes, and for no other reason. Pure
# except for reading the context thresholds config.sh resolved.
#
# Depends on: palette.sh (the status colours).

# --- Severity ranks --------------------------------------------------------
# One ordered scale (0 ok / 1 warn / 2 danger), so the worse of two readings is
# a plain integer max instead of a colour-string comparison.
RANK_OK=0
RANK_WARN=1
RANK_DANGER=2

rank_color() {
  case "$1" in
    "$RANK_DANGER") echo "$RED" ;;
    "$RANK_WARN")   echo "$YELLOW" ;;
    *)              echo "$GREEN" ;;
  esac
}

# pick color for a given token count.
# pre: $1 an integer token count; WARN_TOKENS/SAVE_TOKENS resolved by
#      config.sh (resolve_ctx_thresholds) before the first call.
token_color() {
  local t="$1"
  if   [ "$t" -ge "$SAVE_TOKENS" ]; then echo "$RED"
  elif [ "$t" -ge "$WARN_TOKENS" ]; then echo "$YELLOW"
  else echo "$GREEN"; fi
}

# Absolute quota reading: how much of the window's allowance is already spent.
# danger at 80% — the last fifth is the margin left to notice and slow down
# before lockout; warn at half.
QUOTA_DANGER_PCT=80
QUOTA_WARN_PCT=50
quota_rank() {
  local p="$1"
  if   [ "$p" -ge "$QUOTA_DANGER_PCT" ]; then echo "$RANK_DANGER"
  elif [ "$p" -ge "$QUOTA_WARN_PCT" ];   then echo "$RANK_WARN"
  else echo "$RANK_OK"; fi
}
quota_color() { rank_color "$(quota_rank "$1")"; }

# --- Pace: burn rate measured against the window's own clock ----------------
# A quota reading alone cannot say whether it is a problem: 60% used is fine
# four hours into a five-hour window and alarming ten minutes in. The pace
# ratio divides the share spent by the share of the window elapsed.
#
#   ratio = used% / elapsed%   (printed here as an integer percent: 130 = 1.3x)
#
# Because elapsed% is the fraction of the window gone, used%/elapsed% IS the
# linear projection of usage at reset time: a ratio of 100 projects landing
# exactly on the cap. So the pace thresholds are not new constants —
# PACE_DANGER_RATIO is the cap itself, and PACE_WARN_RATIO is the point where
# the projection lands inside the absolute reading's own danger band.
PACE_DANGER_RATIO=100                       # projects >= 100% used at reset
PACE_WARN_RATIO="$QUOTA_DANGER_PCT"         # projects into the absolute danger band
PACE_MIN_ELAPSED_PCT=10   # Below this the denominator is under half an hour on
                          # the 5h window, so one burst dominates and the
                          # extrapolation swings wildly. Judgement, not a
                          # measured or published figure: it errs toward
                          # printing nothing rather than a number that flips
                          # colour every refresh.

# Window lengths of the two rate-limit windows the statusLine input reports.
# source: docs.anthropic.com/en/docs/claude-code/costs + Anthropic usage-limit
# docs — Pro/Max usage is bounded by a 5-hour rolling window and a 7-day window.
RL_5H_WINDOW_S=18000
RL_7D_WINDOW_S=604800

# pace_ratio — burn rate relative to the window clock.
# pre:  $1 used percentage (non-negative integer), $2 resets_at epoch seconds,
#       $3 window length in seconds, $4 current epoch seconds.
# post: on success prints the ratio as an integer percent (130 = 1.3x) and
#       returns 0. Returns 1 and prints nothing when the inputs are not usable
#       (non-numeric, zero-length window) or when too little of the window has
#       elapsed for the projection to be informative (< PACE_MIN_ELAPSED_PCT).
#       Elapsed time is clamped into [0, window] so a clock skew or a stale
#       resets_at can only yield a boundary value, never a negative divisor.
pace_ratio() {
  local used="$1" resets_at="$2" window="$3" now="$4" elapsed elapsed_pct
  case "$used"      in ''|*[!0-9]*) return 1 ;; esac
  case "$resets_at" in ''|*[!0-9]*) return 1 ;; esac
  case "$window"    in ''|*[!0-9]*|0) return 1 ;; esac
  case "$now"       in ''|*[!0-9]*) return 1 ;; esac
  elapsed=$(( window - ( resets_at - now ) ))
  [ "$elapsed" -lt 0 ] && elapsed=0
  [ "$elapsed" -gt "$window" ] && elapsed="$window"
  elapsed_pct=$(( elapsed * 100 / window ))
  [ "$elapsed_pct" -lt "$PACE_MIN_ELAPSED_PCT" ] && return 1
  echo $(( used * 100 / elapsed_pct ))
}

pace_rank() {
  local r="$1"
  if   [ "$r" -ge "$PACE_DANGER_RATIO" ]; then echo "$RANK_DANGER"
  elif [ "$r" -ge "$PACE_WARN_RATIO" ];   then echo "$RANK_WARN"
  else echo "$RANK_OK"; fi
}

# quota_reading — resolve one rate-limit window into everything the render needs.
# pre:  $1 raw used_percentage (any numeric form jq returned), $2 resets_at
#       epoch seconds, $3 window length seconds, $4 current epoch seconds.
# post: on success prints "<pct> <rank> <pace_rank> <ratio>" and returns 0:
#       pct   — percentage rounded to an integer
#       rank  — the WORSE of the absolute and pace severities; this is the
#               window's overall state, carried by the percentage
#       pace_rank — the pace severity alone, carried by the pace figure so a
#               harmless burn rate is not painted red just because the window
#               happens to be nearly spent (and vice versa)
#       ratio — pace ratio as an integer percent, empty when not informative.
#               Last field on purpose: it is the only one that can be empty, and
#               `read` with default IFS collapses interior blank fields but
#               leaves a trailing one empty, so a caller's variables cannot shift.
#       Returns 1 and prints nothing when $1 is not numeric.
# The worse-of-two rule is what makes the two readings safe to combine: a window
# at 95% with the clock nearly up has a harmless pace but is still nearly spent,
# and a window at 30% ten minutes in has a harmless absolute but is burning at
# 3x. Either alone would stay green in one of those cases.
quota_reading() {
  local raw="$1" at="$2" win="$3" now="$4" pct ratio rank prank
  # Validate BEFORE awk: awk reads an unquoted non-numeric token as an
  # uninitialised variable and prints 0, so "abc" would silently render as a
  # healthy 0% instead of being reported as unusable.
  case "$raw" in ''|*[!0-9.]*) return 1 ;; esac
  pct=$(LC_NUMERIC=C awk "BEGIN{printf \"%.0f\",$raw}" 2>/dev/null)
  case "$pct" in ''|*[!0-9]*) return 1 ;; esac
  rank=$(quota_rank "$pct")
  prank="$RANK_OK"
  if ratio=$(pace_ratio "$pct" "$at" "$win" "$now"); then
    prank=$(pace_rank "$ratio")
    [ "$prank" -gt "$rank" ] && rank="$prank"
  else
    ratio=""
  fi
  # newline-terminated: `read` reports failure on an unterminated line, which
  # would make every successful reading look like a failed one at the call site.
  printf '%s %s %s %s\n' "$pct" "$rank" "$prank" "$ratio"
}

# pace ratio as the multiple it is: 130 -> "1.3x"
fmt_pace() { LC_NUMERIC=C awk "BEGIN{printf \"%.1fx\",$1/100}"; }
