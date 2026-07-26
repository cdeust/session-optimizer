# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/fit.sh — measuring and trimming a rendered line.
#
# Single responsibility: how much terminal a rendered string occupies, and what
# to drop when it occupies too much. This module changes when the trimming
# policy changes. It does not probe the terminal (layout.sh does that) and does
# not know what any segment means — it only knows they are joined by " │ " and
# ordered most-important-first. Everything here is pure.
#
# Depends on: palette.sh (SEP, RESET).
#
# Claude Code truncates a status line that reaches the terminal's right margin,
# and a line long enough to wrap costs the whole block a row. Rather than let
# the terminal cut mid-word, the renderer measures each line and drops its
# lowest-priority segments itself.

# Share of the terminal width a line may occupy. Lines are fitted to a fraction
# of the width rather than to the width itself, because the host keeps part of
# the row for its own chrome and cuts whatever crosses into it.
#
# NOT A MEASURED CONSTANT — read this before trusting it. 85 is inherited from
# the pictet-tech claude-statusline plugin (assets/statusline.sh:98-108), which
# is no longer installed here and can no longer be consulted. Its claim — that a
# line approaching the full width is truncated with "…" AND costs the block its
# second row — has never been reproduced against the host by this repo. The
# value is a working default carried forward, not evidence.
#
# What IS established, source: code.claude.com/docs/en/statusline, read
# 2026-07-26 against Claude Code 2.1.220 — the reserve the host takes is
# ADDITIVE, not proportional. `statusLine.padding` is "extra horizontal spacing
# (in characters)", defaults to 0, and is "in addition to the interface's
# built-in spacing", whose column count the docs do not publish. A percentage
# therefore models the constraint in the wrong shape: it over-reserves on wide
# terminals and under-reserves on narrow ones.
#
# Known cost of keeping the ratio, so the next reader is not surprised by it:
# tests/statusline/measure_widths.sh puts preset l's widest line at 89 columns
# (measured 2026-07-26), so at 85% l needs 104 columns to render untrimmed —
# while SIZE_L_MIN_COLS in layout.sh selects l from 90 up. Between 90 and 104
# columns l is selected and then trimmed on every refresh. Replacing this with a
# measured additive reserve closes that gap and needs one observation against a
# live host; it is deliberately not done here.
FIT_RATIO=85

# vislen — terminal columns a rendered segment will occupy.
# pre:  $1 is a segment as built by this renderer: literal "\033[<params>m" SGR
#       sequences (the four characters backslash-0-3-3 followed by params and
#       "m", NOT interpreted escapes — printf '%b' interprets them at emit
#       time) interleaved with printable text.
# post: prints the column count. SGR sequences count 0; every printable
#       character counts 1. Pure: no I/O, no global mutation.
# Two known deviations, both documented rather than corrected:
#   - The block/box glyphs used here (│ █ ░ …) are East-Asian-Ambiguous width.
#     They are counted as 1, which matches Terminal.app, iTerm2 and Warp in
#     their default configuration. A terminal explicitly configured to render
#     ambiguous characters double-width will be under-measured; the FIT_RATIO
#     headroom absorbs the common case and STATUSLINE_COLS overrides it.
#   - Under a non-UTF-8 locale bash counts bytes, so multi-byte characters
#     over-measure. That can only make fit_line trim earlier, never overflow.
vislen() {
  local s="$1" out="" pre rest
  while [ -n "$s" ]; do
    case "$s" in
      *'\033['*) pre=${s%%'\033['*} ;;
      *)         out="$out$s"; break ;;
    esac
    out="$out$pre"
    rest=${s#*'\033['}
    case "$rest" in
      *m*) s=${rest#*m} ;;
      *)   break ;;              # unterminated SGR: stop, count nothing more
    esac
  done
  printf '%s' "${#out}"
}

# vistrunc — cut a segment to at most $2 columns, ending in an ellipsis.
# pre:  $1 a segment (same shape as vislen's input), $2 a positive column budget.
# post: prints a segment of at most $2 columns, closed with RESET so the cut
#       cannot leak a colour into the rest of the terminal. SGR sequences are
#       copied through intact — the cut never lands inside one. Prints nothing
#       for a non-positive budget.
vistrunc() {
  local s="$1" max="$2" out="" n=0 pre rest esc
  case "$max" in ''|*[!0-9]*|0) return ;; esac
  while [ -n "$s" ]; do
    case "$s" in
      *'\033['*) pre=${s%%'\033['*} ;;
      *)         pre=$s ;;
    esac
    if [ $(( n + ${#pre} )) -gt "$max" ]; then
      # one column is spent on the ellipsis itself
      out="${out}${pre:0:$(( max - n - 1 ))}…"
      printf '%s' "${out}${RESET}"
      return
    fi
    out="$out$pre"; n=$(( n + ${#pre} ))
    s=${s#"$pre"}
    [ -z "$s" ] && break
    rest=${s#'\033['}
    case "$rest" in
      *m*) esc=${rest%%m*}; out="${out}\\033[${esc}m"; s=${rest#*m} ;;
      *)   break ;;
    esac
  done
  printf '%s' "$out"
}

# fit_line — make one status line fit $2 columns.
# pre:  $1 a rendered line whose segments are joined by " ${SEP} ", $2 a column
#       budget. Lines in this renderer are built most-important-first, so the
#       tail is by construction the lowest-priority material.
# post: prints the longest leading run of whole segments that fits, closed with
#       RESET. A line already within budget is returned untouched (fast path,
#       one vislen call). When even the first segment overflows it is truncated
#       in place rather than dropped, so a line never renders empty. Pure.
fit_line() {
  local line="$1" max="$2" d=" ${SEP} " out="" rest seg first=1 n=0 dw sw
  case "$max" in ''|*[!0-9]*|0) printf '%s' "$line"; return ;; esac
  [ "$(vislen "$line")" -le "$max" ] && { printf '%s' "$line"; return ; }
  dw=$(vislen "$d")
  rest="$line"
  while :; do
    case "$rest" in
      *"$d"*) seg=${rest%%"$d"*}; rest=${rest#*"$d"} ;;
      *)      seg=$rest; rest="" ;;
    esac
    sw=$(vislen "$seg")
    if [ "$first" = "1" ]; then
      [ "$sw" -gt "$max" ] && { vistrunc "$seg" "$max"; return; }
      out="$seg"; n="$sw"; first=0
    else
      [ $(( n + dw + sw )) -gt "$max" ] && break
      out="${out}${d}${seg}"; n=$(( n + dw + sw ))
    fi
    [ -z "$rest" ] && break
  done
  printf '%s' "${out}${RESET}"
}
