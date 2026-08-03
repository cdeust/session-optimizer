# shellcheck shell=bash
# statusline-lib/format.sh — numbers and times as the reader sees them.
#
# Single responsibility: turning a raw quantity into the shortest string that
# still reads unambiguously. Changes when a display format changes. Knows
# nothing about severity, colour or layout — none of these functions emit an
# escape sequence.
#
# Depends on: platform.sh (_date_fmt).

# fmt_tokens — a token count in engineering shorthand (1234 -> "1k").
fmt_tokens() {
  local t="$1"
  if   [ "$t" -ge 1000000000 ]; then LC_NUMERIC=C awk "BEGIN{printf \"%.1fG\",$t/1000000000}"
  elif [ "$t" -ge 1000000 ];    then LC_NUMERIC=C awk "BEGIN{printf \"%.1fM\",$t/1000000}"
  elif [ "$t" -ge 1000 ];       then LC_NUMERIC=C awk "BEGIN{printf \"%.0fk\",$t/1000}"
  else echo "$t"; fi
}

# fmt_usd — a dollar amount, thousands folded to "$1.2k".
fmt_usd() {
  # Value passed via -v, never interpolated into the program text: a stray
  # comma decimal must not become an argument separator.
  local v="${1:-0}"
  v="${v/,/.}"
  LC_ALL=C awk -v v="$v" 'BEGIN{ v=v+0; if (v>=1000) printf "$%.1fk", v/1000; else printf "$%.2f", v }'
}

# fmt_dur — compact duration: "Xh Ym" / "Xm Ys" / "Xs" from a seconds count.
fmt_dur() {
  local s="$1"
  case "$s" in ''|*[!0-9]*) s=0 ;; esac
  if   [ "$s" -ge 3600 ]; then printf '%dh%dm' "$(( s / 3600 ))" "$(( (s % 3600) / 60 ))"
  elif [ "$s" -ge 60 ];   then printf '%dm%ds' "$(( s / 60 ))" "$(( s % 60 ))"
  else printf '%ds' "$s"; fi
}

# --- Rate-limit reset times -----------------------------------------------
# .rate_limits.*.resets_at is an epoch in SECONDS (verified against both the
# large and xlarge variants of github.com/AwesomeJun/CC-statusline, which
# subtract it from `date +%s` directly). Guard: only treat all-digit values as
# an epoch so a future ISO string can never crash the arithmetic.

# fmt_reset_in — "XhYm" remaining until the epoch $1. Prints nothing for a
# non-epoch input. Used for the 5h burst window, which turns over often enough
# that a countdown is what the reader wants.
fmt_reset_in() {
  local e="$1"
  case "$e" in ''|*[!0-9]*) return ;; esac
  local rem=$(( e - $(date +%s) )); [ "$rem" -lt 0 ] && rem=0
  printf '%dh%dm' "$(( rem / 3600 ))" "$(( (rem % 3600) / 60 ))"
}

# fmt_reset_at — "Wed 14:00" wall-clock of the epoch $1. Prints nothing for a
# non-epoch input. Used for the 7d window, where a countdown would read
# "138h12m" and mean nothing.
fmt_reset_at() {
  local e="$1"
  case "$e" in ''|*[!0-9]*) return ;; esac
  LC_TIME=C _date_fmt "$e" "%a %H:%M"
}
