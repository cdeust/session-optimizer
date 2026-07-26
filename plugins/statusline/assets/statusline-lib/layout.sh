# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/layout.sh — how wide the terminal is and how much to show.
#
# Single responsibility: the coarse adjustment — how many LINES render and how
# wide the bars are. (The fine one, how many SEGMENTS survive on each line, is
# fit.sh's job.) Changes when the preset ladder or the width probe changes.
#
# Depends on: config.sh (read_configured_size).

# probe_cols — the terminal's width in columns.
# post: prints a positive integer, always.
# The statusLine JSON carries no terminal size, so it is probed. Every source
# below is a real measurement of a real terminal; when none of them can answer,
# the fallback is deliberately WIDE. An over-generous guess reproduces exactly
# the pre-width-aware behaviour, whereas an over-tight one hides information
# that would have fitted — so a guess is never preferred to no answer.
# In particular `tput cols` is consulted only when stdout is a terminal: with
# stdout on a pipe (which is how the host captures this script) tput cannot
# query anything and returns terminfo's blind default of 80, which would
# silently downgrade the preset on every IDE and web host.
probe_cols() {
  local cols="${STATUSLINE_COLS:-}"
  if [ -n "$cols" ]; then printf '%s' "$cols"; return; fi
  # stdin is the JSON pipe, so the size is read from the controlling tty. The
  # stderr redirect wraps the whole group, not just stty: with no controlling
  # terminal it is the "< /dev/tty" REDIRECTION that fails, and the shell
  # reports that itself before stty ever runs, so a 2>/dev/null on the command
  # alone would still leak "Device not configured" on every refresh.
  cols=$( { stty size < /dev/tty | awk '{print $2}'; } 2>/dev/null )
  case "$cols" in ''|*[!0-9]*|0) cols="${COLUMNS:-}" ;; esac
  case "$cols" in ''|*[!0-9]*|0) [ -t 1 ] && cols=$(tput cols 2>/dev/null) ;; esac
  case "$cols" in ''|*[!0-9]*|0) cols=200 ;; esac
  printf '%s' "$cols"
}

# --- Verbosity preset (xs|s|m|l|xl) -----------------------------------------
# Resolution:
#   $STATUSLINE_SIZE env — a hard pin, honoured at any width. The escape hatch:
#                          forcing a preset for testing or for a terminal whose
#                          width cannot be probed correctly.
#   .size in the budget config, else "l" — a PREFERENCE, capped by the width
#                          rule below. It is what the config ships with, so
#                          treating it as a pin would leave the width rule dead
#                          for every default installation.
# Tiers are monotonic — each larger size is a superset of the smaller one:
#   xs  1 line : identity + context bar             (CTX 6)
#   s   2 lines: + git, session (tokens + cost)      (CTX 8)
#   m   3 lines: + rate limits + churn               (CTX 10)
#   l   5 lines: + $ & token budget gauges + resets  (CTX 10, BW 12)  [default]
#   xl  5 lines: everything, widest bars + avg/mo    (CTX 16, BW 20)
#
# Width caps the preference, and only downward. Width can prove that a preset
# does not fit; it cannot prove the user wants a bigger one — the ladder encodes
# how much detail they asked for, not just how much the terminal can hold. So a
# wide terminal never silently promotes the preference, and a narrow one lowers
# it at most to "s".
#
# Widest rendered line per preset, source: measured 2026-07-26 with
# tests/statusline/measure_widths.sh over a representative populated payload —
#   s 64 | xs 85 | l 89 | xl 95 | m 139
# The ladder is monotonic in CONTENT but NOT in width: xs crams identity and
# context onto one line and m inlines both quota windows into the session line,
# so both are wider than l, which spreads strictly more material over more rows.
# Auto-selection therefore has exactly two useful rungs — l, and s when l cannot
# fit — and never picks xs or m, which are line-count preferences to be pinned
# explicitly, not narrow-terminal fallbacks.
#
# The threshold is l's widest measured line rounded up (89 -> 90), compared
# against the RAW width rather than the fitted budget: above it, l is at worst
# lightly trimmed inside the FIT_RATIO headroom, which is precisely fit_line's
# job; below it the preset is structurally too wide and a real downgrade is the
# honest answer. The measurement anchors the threshold; it does not guarantee it
# — a long branch name or a deep directory widens any preset, and fit_line is
# what actually holds the line to the terminal.
SIZE_L_MIN_COLS=90

# resolve_preset — choose the verbosity preset for a terminal $1 columns wide.
# pre:  $1 a positive integer column count.
# post: sets SIZE (xs|s|m|l|xl), RANK (0..4, the preset as an ordered level),
#       CTX_W (context bar cells) and BW (quota bar cells). Returns 0.
resolve_preset() {
  local cols="$1"
  SIZE="${STATUSLINE_SIZE:-}"
  case "$SIZE" in
    xs|s|m|l|xl) ;;                       # env pin: honoured at any width
    *)
      SIZE=$(read_configured_size)
      case "$SIZE" in xs|s|m|l|xl) ;; *) SIZE="l" ;; esac
      # width cap, downward only: "s" is the one preset narrower than "l"
      # (measured 64 cols vs 89 — see the table above).
      [ "$cols" -lt "$SIZE_L_MIN_COLS" ] && case "$SIZE" in m|l|xl) SIZE="s" ;; esac
    ;;
  esac
  case "$SIZE" in
    xs) RANK=0; CTX_W=6;  BW=10 ;;
    s)  RANK=1; CTX_W=8;  BW=10 ;;
    m)  RANK=2; CTX_W=10; BW=12 ;;
    l)  RANK=3; CTX_W=10; BW=12 ;;
    xl) RANK=4; CTX_W=16; BW=20 ;;
  esac
}
