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

# Columns the host keeps for itself around this script's output, EXCLUDING the
# user-configurable statusLine.padding (fit_budget adds that separately).
#
# source: Claude Code 2.1.220, /Users/…/.local/share/claude/versions/2.1.220,
# read 2026-07-26. The status line is rendered by a component whose container is
#   <Box width={columns} flexDirection={…} flexWrap="wrap" alignItems="flex-start"
#        paddingLeft={2} paddingRight={compact ? 1 : 2} columnGap={1}>
# so the row is the full terminal width less 2 columns on the left and 2 on the
# right — 4. The compact branch reserves 1 on the right, i.e. 3 total; the wider
# of the two is used, because a budget that is one column pessimistic trims one
# segment early, while one column optimistic is cut by the host.
#
# The same read settles what happens on overflow, replacing an inherited claim
# this repo could not reproduce: each emitted line is rendered as
#   <Text dimColor wrap="truncate">
# individually (single-line path, and the per-line map for multi-line output),
# so an over-wide line is truncated ON ITS OWN and does NOT cost the block any
# other row. Line count is independent of line width.
FIT_CHROME_COLS=4

# fit_budget — columns one rendered line may occupy.
# pre:  $1 the terminal width in columns, $2 the configured statusLine.padding.
#       Both are validated here rather than trusted: $1 comes from probe_cols,
#       whose last rung is a fallback, and $2 from a user-edited settings file.
# post: prints a positive integer, always. Pure: no I/O, no global mutation.
# The host applies padding as Ink's paddingX, which indents BOTH sides, so the
# configured value costs twice its own size. A terminal too narrow to hold even
# one column after the reserve yields 1 rather than 0 or a negative: fit_line
# treats a non-positive budget as "no budget" and returns the line untrimmed,
# which is precisely the overflow this exists to prevent.
fit_budget() {
  local cols="$1" pad="${2:-0}" w
  case "$cols" in ''|*[!0-9]*) printf '1'; return ;; esac
  case "$pad" in ''|*[!0-9]*) pad=0 ;; esac
  w=$(( cols - FIT_CHROME_COLS - 2 * pad ))
  [ "$w" -lt 1 ] && w=1
  printf '%s' "$w"
}

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
#     ambiguous characters double-width will be under-measured, and the budget
#     from fit_budget is exact rather than generous, so such a line is cut by
#     the host's own truncate. STATUSLINE_COLS is the override for that case:
#     declaring a narrower width buys back the difference.
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
