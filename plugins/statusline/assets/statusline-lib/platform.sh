# shellcheck shell=bash
# statusline-lib/platform.sh — the BSD/GNU spelling differences, in one place.
#
# Single responsibility: hide the two mutually exclusive spellings of the
# coreutils this renderer needs. `stat` and `date` are the only two commands it
# calls whose flags differ between macOS (BSD) and Linux (GNU); every other
# external it uses (jq, awk, git) takes the same arguments on both. This module
# changes when a new platform is supported, and for no other reason.
#
# Depends on: nothing but the shell and coreutils.

# file_mtime — modification time of $1 as epoch seconds, 0 when unavailable.
# pre:  $1 is a path (may not exist).
# post: prints a non-negative integer and returns 0. 0 is the documented
#       "unavailable" answer — never an empty string, because every call site
#       feeds the result straight into `now - mtime` arithmetic.
# BSD/macOS `stat -f %m` and GNU/Linux `stat -c %Y` are mutually exclusive
# spellings; trying both keeps every cache-age and lock-age comparison in this
# renderer working on either platform instead of silently reading 0 (which
# forces a refresh on every invocation) on Linux.
file_mtime() {
  local f="$1" m=""
  m=$(stat -f '%m' "$f" 2>/dev/null) && [ -n "$m" ] && { printf '%s' "$m"; return; }
  m=$(stat -c '%Y' "$f" 2>/dev/null) && [ -n "$m" ] && { printf '%s' "$m"; return; }
  printf '0'
}

# _date_fmt — format epoch seconds $1 with strftime format $2.
# pre:  $1 all-digit epoch seconds, $2 a strftime format string.
# post: prints the formatted time, or nothing when no spelling works.
# Cross-platform date: BSD/macOS `date -j -f %s` | `date -r` | GNU `date -d @`.
_date_fmt() {
  local epoch="$1" fmt="$2" out=""
  out=$(date -j -f "%s" "$epoch" "+$fmt" 2>/dev/null) && [ -n "$out" ] && { echo "$out"; return; }
  out=$(date -r "$epoch" "+$fmt" 2>/dev/null) && [ -n "$out" ] && { echo "$out"; return; }
  date -d "@$epoch" "+$fmt" 2>/dev/null
}
