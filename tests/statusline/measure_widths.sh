#!/usr/bin/env bash
# tests/statusline/measure_widths.sh — measure the rendered width of each
# verbosity preset, so the width→preset cap in statusline-command.sh rests on a
# measurement instead of a guess.
#
# Method: render the script against one representative populated payload at an
# effectively unlimited terminal width (so fit_line never trims), then measure
# every emitted line with the script's OWN vislen — the same function that
# decides trimming at runtime, so the measurement and the decision cannot use
# different rulers (in particular the same treatment of the ambiguous-width
# block glyphs). Emitted lines carry INTERPRETED escapes (printf '%b' already
# ran), whereas vislen's contract is the uninterpreted form, so the real SGR
# sequences are stripped first and vislen counts the remaining characters.
# Reports the widest line per preset.
#
# The cap threshold for a preset is that preset's widest line divided by
# FIT_RATIO (the fraction of the terminal a line is allowed to occupy), i.e.
# the narrowest terminal in which the preset still renders untrimmed.
#
# Isolation: STATUSLINE_COST_LOG points at a throwaway ledger under a temp dir,
# so a measurement run never touches ~/.claude/statusline-costs.jsonl. All
# fixture data is synthetic.
set -uo pipefail

SCRIPT_UNDER_TEST="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/plugins/statusline/assets/statusline-command.sh}"
[ -r "$SCRIPT_UNDER_TEST" ] || { echo "introuvable: $SCRIPT_UNDER_TEST" >&2; exit 1; }

# vislen(), FIT_RATIO and the palette come from the script under test itself.
# shellcheck source=/dev/null  # The path is a variable BY DESIGN:
# $SCRIPT_UNDER_TEST points the suite at the repo copy or an installed one.
STATUSLINE_SOURCE_ONLY=1 source "$SCRIPT_UNDER_TEST"
command -v vislen >/dev/null || { echo "vislen absent de $SCRIPT_UNDER_TEST" >&2; exit 1; }

TMPDIR_M="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_M"' EXIT

# Representative payload: every optional group populated, with field values at
# the long end of what is realistic (a 17-character directory, a six-figure
# token count, three-digit churn). A payload with empty groups would measure a
# preset narrower than it renders in practice, and the cap would then let a
# preset through into a terminal that cannot hold it.
now=$(date +%s)
cat > "$TMPDIR_M/payload.json" <<EOF
{
  "model": {"display_name": "Opus 5"},
  "effort": {"level": "high"},
  "thinking": {"enabled": true},
  "context_window": {"used_percentage": 62.4, "total_input_tokens": 124800},
  "cost": {"total_cost_usd": 1.2345, "total_duration_ms": 754000,
           "total_lines_added": 143, "total_lines_removed": 27},
  "rate_limits": {
    "five_hour": {"used_percentage": 41.2, "resets_at": $((now + 5400))},
    "seven_day": {"used_percentage": 68.9, "resets_at": $((now + 240000))}
  },
  "session_id": "measure-widths-fixture",
  "workspace": {"current_dir": "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"},
  "transcript_path": "$TMPDIR_M/absent-transcript.jsonl"
}
EOF

UNLIMITED_COLS=10000   # wide enough that FIT_W can never bind

printf '%-4s %8s %8s   %s\n' preset widest cap lines
for size in xs s m l xl; do
  widest=0; nlines=0
  while IFS= read -r line; do
    nlines=$(( nlines + 1 ))
    plain=$(printf '%s' "$line" | sed $'s/\x1b\\[[0-9;]*m//g')
    w=$(vislen "$plain")
    [ "$w" -gt "$widest" ] && widest="$w"
  done < <(
    STATUSLINE_COST_LOG="$TMPDIR_M/ledger.jsonl" \
    STATUSLINE_COLS="$UNLIMITED_COLS" \
    STATUSLINE_SIZE="$size" \
    bash "$SCRIPT_UNDER_TEST" < "$TMPDIR_M/payload.json"
  )
  # narrowest terminal that still renders this preset untrimmed
  cap=$(( widest * 100 / FIT_RATIO ))
  printf '%-4s %8s %8s   %s\n' "$size" "$widest" "$cap" "$nlines"
done
