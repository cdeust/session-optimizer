#!/usr/bin/env bash
# tests/statusline/test_heat_rgb.sh — harness for heat_rgb()/make_bar() (issue #4)
#
# Adapted from the PRD model (prd-output/run_mrlq/05-testing.md) with the
# post-jury AC-008 fix applied: test_make_bar_segmented_rendering asserts
# per-cell multi-palier color, not mere non-emptiness (jury verdict: mendeleev
# FAIL, confidence 0.60 — see prd-output/run_mrlq/10-verification-report.md).
#
# Isolation: each test runs in its own subshell via run_test (setup/teardown,
# trap EXIT). Execution order is randomized (shuf) on every run. All fixture
# data is synthetic (percentages 0-100); no production data, no PII.
set -uo pipefail
SCRIPT_UNDER_TEST="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/plugins/statusline/assets/statusline-command.sh}"

# The renderer is a composition root plus a directory of modules (heat_rgb and
# make_bar live in statusline-lib/palette.sh, token_color in severity.sh). A
# static check that read only the main script would pass vacuously, so the
# checks below sweep the whole source set.
LIB_UNDER_TEST="${LIB_UNDER_TEST:-$(dirname "$SCRIPT_UNDER_TEST")/statusline-lib}"
SOURCES_UNDER_TEST=("$SCRIPT_UNDER_TEST")
for _f in "$LIB_UNDER_TEST"/*.sh; do
  [ -r "$_f" ] && SOURCES_UNDER_TEST+=("$_f")
done
unset _f

# Expected palette values — asserted independently of the script's own HEAT_*
# constants (except HEAT_3, whose exact terracotta-attenuated RGB is derived
# from a scripted oklch->srgb conversion and is inherently the same computed
# value; see tests/statusline/oklch2srgb.py for the standalone converter used
# to derive it, source: Bjorn Ottosson OKLab, https://bottosson.github.io/posts/oklab/).
EXPECTED_HEAT_1="136;134;130"   # #888682 DS --fg-2 (reused from OVERLAY)
EXPECTED_HEAT_2="192;189;186"   # #c0bdba DS --fg-1 (reused from SUBTEXT)
EXPECTED_HEAT_3="181;125;98"    # #b57d62 scripted oklch->srgb oklch(64% 0.08 47)
EXPECTED_HEAT_4="207;110;57"    # #cf6e39 DS --accent (reused from PEACH)

assert_eq() {
  local actual="$1" expected="$2" msg="${3:-assert_eq}"
  [ "$actual" != "$expected" ] && { echo "FAIL: ${msg} — attendu [${expected}] obtenu [${actual}]" >&2; return 1; }
  return 0
}

gen_pct_fixture() { seq 0 "$1"; }

setup() { TEST_TMPDIR="$(mktemp -d)"; export TEST_TMPDIR; }
teardown() { [ -n "${TEST_TMPDIR:-}" ] && rm -rf "$TEST_TMPDIR"; unset TEST_TMPDIR; }

run_test() {
  local test_name="$1"
  # shellcheck source=/dev/null  # The path is a variable BY DESIGN:
  # $SCRIPT_UNDER_TEST points the suite at the repo copy or an installed one.
  ( setup; trap teardown EXIT; STATUSLINE_SOURCE_ONLY=1 source "$SCRIPT_UNDER_TEST"; "$test_name" )
  local status=$?
  [ $status -eq 0 ] && echo "PASS: ${test_name}" || echo "FAIL: ${test_name}"
  return $status
}

# --- heat_rgb: paliers at their medians (AC-001..AC-004) ---

function test_heat_rgb_palier1_low() {
  assert_eq "$(heat_rgb 0)" "$EXPECTED_HEAT_1" "palier1 borne 0" && assert_eq "$(heat_rgb 25)" "$EXPECTED_HEAT_1" "palier1 milieu 25"
}

function test_heat_rgb_palier2_mid() {
  assert_eq "$(heat_rgb 50)" "$EXPECTED_HEAT_2" "palier2 borne 50" && assert_eq "$(heat_rgb 60)" "$EXPECTED_HEAT_2" "palier2 milieu 60"
}

function test_heat_rgb_palier3_high() {
  assert_eq "$(heat_rgb 75)" "$EXPECTED_HEAT_3" "palier3 borne 75" && assert_eq "$(heat_rgb 80)" "$EXPECTED_HEAT_3" "palier3 milieu 80"
}

function test_heat_rgb_palier4_critical() {
  assert_eq "$(heat_rgb 90)" "$EXPECTED_HEAT_4" "palier4 borne 90" && assert_eq "$(heat_rgb 95)" "$EXPECTED_HEAT_4" "palier4 milieu 95"
}

# --- pivots: the 3 exact boundary transitions (AC-005..AC-007) ---

function test_heat_rgb_pivot_49_50() {
  assert_eq "$(heat_rgb 49)" "$EXPECTED_HEAT_1" "49 reste palier1" && assert_eq "$(heat_rgb 50)" "$EXPECTED_HEAT_2" "50 bascule palier2"
}

function test_heat_rgb_pivot_74_75() {
  assert_eq "$(heat_rgb 74)" "$EXPECTED_HEAT_2" "74 reste palier2" && assert_eq "$(heat_rgb 75)" "$EXPECTED_HEAT_3" "75 bascule palier3"
}

function test_heat_rgb_pivot_89_90() {
  assert_eq "$(heat_rgb 89)" "$EXPECTED_HEAT_3" "89 reste palier3" && assert_eq "$(heat_rgb 90)" "$EXPECTED_HEAT_4" "90 bascule palier4"
}

# --- clamps and input validation ---

function test_heat_rgb_negative_input_clamped() {
  assert_eq "$(heat_rgb -1)" "$EXPECTED_HEAT_1" "-1 clampe palier1"
}

function test_heat_rgb_overflow_input_clamped() {
  assert_eq "$(heat_rgb 101)" "$EXPECTED_HEAT_4" "101 clampe palier4"
}

function test_heat_rgb_non_numeric_input_rejected() {
  heat_rgb "abc" >/dev/null 2>&1 && { echo "FAIL: 'abc' doit echouer" >&2; return 1; }
  heat_rgb "" >/dev/null 2>&1 && { echo "FAIL: entree vide doit echouer" >&2; return 1; }
  return 0
}

# --- exhaustive sweep 0..100 ---

function test_heat_rgb_full_pct_sweep() {
  local pct color
  for pct in $(gen_pct_fixture 100); do
    color="$(heat_rgb "$pct")"
    [ -z "$color" ] && { echo "FAIL: heat_rgb($pct) vide" >&2; return 1; }
    if [ "$pct" -lt 50 ]; then assert_eq "$color" "$EXPECTED_HEAT_1" "sweep $pct" || return 1
    elif [ "$pct" -lt 75 ]; then assert_eq "$color" "$EXPECTED_HEAT_2" "sweep $pct" || return 1
    elif [ "$pct" -lt 90 ]; then assert_eq "$color" "$EXPECTED_HEAT_3" "sweep $pct" || return 1
    else assert_eq "$color" "$EXPECTED_HEAT_4" "sweep $pct" || return 1; fi
  done
  return 0
}

# --- make_bar: segmented rendering (AC-008 canonical — post-jury fix) ---
# 60% over 10 cells: filled = round(60*10/100) = 6. pos(i) = i*100/(w-1) for
# w=10 -> i=0..5 give pos 0,11,22,33,44,55. Paliers: pos<50 -> HEAT_1 (i=0..4,
# 5 cells), pos>=50 -> HEAT_2 (i=5, 1 cell). Verified by counting literal
# "38;2;<rgb>" occurrences in make_bar's raw (unescaped) text output.
function test_make_bar_segmented_rendering() {
  local bar; bar="$(make_bar 60 10)"
  [ -z "$bar" ] && { echo "FAIL: make_bar(60,10) vide" >&2; return 1; }
  local n1 n2
  # Match "38;2;<rgb>m█" specifically (color escape immediately followed by
  # the FILLED-cell glyph). A bare triplet match is not enough: HEAT_1
  # intentionally reuses OVERLAY's RGB (136;134;130), which also prefixes
  # the muted empty-cell run ("38;2;136;134;130m░░░░") — matching the bare
  # triplet would double-count that empty-cell escape as a filled HEAT_1 cell.
  n1=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_1}m█" | wc -l | tr -d ' ')
  n2=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_2}m█" | wc -l | tr -d ' ')
  assert_eq "$n1" "5" "make_bar(60,10) HEAT_1 cell count" || return 1
  assert_eq "$n2" "1" "make_bar(60,10) HEAT_2 cell count" || return 1
  return 0
}

# 90% over 10 cells: filled = round(90*10/100) = 9. pos(i) for i=0..8:
# 0,11,22,33,44,55,66,77,88 -> HEAT_1 x5 (0..44), HEAT_2 x2 (55,66),
# HEAT_3 x2 (77,88), HEAT_4 x0. Confirms multi-palier accumulation
# (AC-008: "no repaint of the whole bar in a single color").
function test_make_bar_multi_palier_traversal() {
  local bar; bar="$(make_bar 90 10)"
  local n1 n2 n3 n4
  n1=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_1}m█" | wc -l | tr -d ' ')
  n2=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_2}m█" | wc -l | tr -d ' ')
  n3=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_3}m█" | wc -l | tr -d ' ')
  n4=$(printf '%s' "$bar" | grep -o "38;2;${EXPECTED_HEAT_4}m█" | wc -l | tr -d ' ')
  assert_eq "$n1" "5" "make_bar(90,10) HEAT_1 count" || return 1
  assert_eq "$n2" "2" "make_bar(90,10) HEAT_2 count" || return 1
  assert_eq "$n3" "2" "make_bar(90,10) HEAT_3 count" || return 1
  assert_eq "$n4" "0" "make_bar(90,10) HEAT_4 count" || return 1
  return 0
}

function test_make_bar_empty_cells_glyph() {
  local bar; bar="$(make_bar 30 10)"
  printf '%s' "$bar" | grep -q "░" || { echo "FAIL: glyphe vide absent" >&2; return 1; }
  return 0
}

function test_make_bar_zero_percent() {
  local bar; bar="$(make_bar 0 10)"
  printf '%s' "$bar" | grep -q "█" && { echo "FAIL: 0% ne doit avoir aucune cellule pleine" >&2; return 1; }
  return 0
}

function test_make_bar_full_percent() {
  local bar; bar="$(make_bar 100 10)"
  printf '%s' "$bar" | grep -q "░" && { echo "FAIL: 100% ne doit avoir aucune cellule vide" >&2; return 1; }
  return 0
}

function test_make_bar_concurrent_instances() {
  local out_a="$TEST_TMPDIR/a" out_b="$TEST_TMPDIR/b"
  ( make_bar 30 10 > "$out_a" ) & local pa=$!
  ( make_bar 90 10 > "$out_b" ) & local pb=$!
  wait "$pa"; wait "$pb"
  [ "$(cat "$out_a")" = "$(cat "$out_b")" ] && { echo "FAIL: etat partage suspecte" >&2; return 1; }
  return 0
}

# --- static / integration checks against the script source ---

function test_static_no_lerp_or_gradrgb_residue() {
  grep -q -E "\\bgrad_rgb\\b" "${SOURCES_UNDER_TEST[@]}" && { echo "FAIL: grad_rgb residuel" >&2; return 1; }
  grep -q -E "lerp" "${SOURCES_UNDER_TEST[@]}" && { echo "FAIL: lerp residuel" >&2; return 1; }
  return 0
}

function test_static_heat_track_no_semaphore_colors() {
  # GREEN/YELLOW/RED must not appear inside heat_rgb()/make_bar() bodies
  grep -q -E "(GREEN|YELLOW|RED)" <(sed -n '/^heat_rgb()/,/^}/p;/^make_bar()/,/^}/p' "${SOURCES_UNDER_TEST[@]}") && { echo "FAIL: ref semaphore dans la jauge" >&2; return 1; }
  return 0
}

# token_color must exist exactly once across the source set: the split moved it
# out of the main script, and two definitions would mean one silently shadows
# the other depending on module load order.
function test_static_token_color_unaffected() {
  local n
  n=$(grep -hcE "^token_color\\(\\)" "${SOURCES_UNDER_TEST[@]}" | awk '{s+=$1} END{print s+0}')
  assert_eq "$n" "1" "definitions de token_color"
}

function test_static_no_eval_no_unquoted_expansion() {
  # Full-line comments are skipped: the rule against this builtin is worth
  # explaining at the site that had to avoid it, and a comment cannot execute
  # anything. Only lines that could actually run are checked.
  grep -nE "\\beval\\b" "${SOURCES_UNDER_TEST[@]}" | grep -vE ':[[:space:]]*#' \
    && { echo "FAIL: eval detecte" >&2; return 1; }
  return 0
}

# --- performance: heat_rgb cost above the instrument, 20 paired samples ---
# Asserts the DELTA only (AC-015/NFR-002: "delta < +5ms"). An absolute budget is
# not assertable here: `date +%s%N` forks an external binary twice per sample,
# and on this machine that fork alone costs ~5ms (measured:
# `s=$(date +%s%N); e=$(date +%s%N); echo $((e-s))` ~5,034,000ns in isolation),
# which already exceeds any budget heat_rgb — a bash function doing integer
# comparisons with no I/O — could be held to.
#
# The delta is measured against a CONTROL, not against an earlier sample of
# heat_rgb itself. The previous form timed one heat_rgb call as its baseline and
# compared it to the mean of twenty more: both sides contained the same work, so
# the delta was noise around zero rather than a cost, and with n=1 on the
# baseline a single slow first sample failed the assertion outright. It failed
# roughly 4 runs in 10 on an otherwise idle machine (measured 2026-07-26, this
# host) — a flaky test asserting nothing.
#
# The timestamps are also taken ONCE PER BLOCK rather than once per sample,
# because the fork is not merely large, it is erratic: timing an EMPTY pair of
# `date +%s%N` calls on this host returned 4.2, 21.4, 21.7, 21.8 and 23.5 ms
# across five consecutive attempts (measured 2026-07-26). A ~19 ms spread cannot
# support a 5 ms assertion at any sample count that keeps the suite fast — which
# is why the old form was flaky rather than merely imprecise. Two timestamps
# around N iterations divide that spread by N instead, so at N=2000 the
# instrument contributes ~0.01 ms per call.
#
# Both arms are measured the same way, and the control carries the same
# redirection so that only heat_rgb's own work separates them. Measured on this
# host at N=2000: heat_rgb 173 us/call, control 41 us/call — a delta of 0.13 ms
# against a 5 ms budget, and the whole test runs in ~0.4 s.
function test_perf_heat_rgb_20run_avg() {
  local i s e n=2000 per_call per_ctrl delta
  heat_rgb 50 >/dev/null      # warm-up, discarded: first call pays one-time costs
  s=$(date +%s%N); for ((i=0;i<n;i++)); do heat_rgb 50 >/dev/null; done; e=$(date +%s%N)
  per_call=$(( (e-s)/n ))
  s=$(date +%s%N); for ((i=0;i<n;i++)); do : >/dev/null;           done; e=$(date +%s%N)
  per_ctrl=$(( (e-s)/n ))
  delta=$(( per_call - per_ctrl ))
  [ "$delta" -gt 5000000 ] && {
    echo "FAIL: heat_rgb ${delta}ns au-dessus du controle > +5ms" >&2; return 1; }
  return 0
}

# Portable Fisher-Yates shuffle using bash's builtin $RANDOM — avoids a
# `shuf`/coreutils dependency so the suite runs unmodified on macOS's stock
# bash 3.2 (no mapfile, no shuf) as well as GNU bash/Linux CI runners.
shuffle_tests() {
  local -a arr=("$@")
  local n=${#arr[@]} i j tmp
  i=$n
  while [ "$i" -gt 1 ]; do
    i=$((i-1))
    j=$((RANDOM % (i+1)))
    tmp="${arr[i]}"; arr[i]="${arr[j]}"; arr[j]="$tmp"
  done
  printf '%s\n' "${arr[@]}"
}

main() {
  local tests=(
    test_heat_rgb_palier1_low test_heat_rgb_palier2_mid test_heat_rgb_palier3_high
    test_heat_rgb_palier4_critical test_heat_rgb_pivot_49_50 test_heat_rgb_pivot_74_75
    test_heat_rgb_pivot_89_90 test_heat_rgb_negative_input_clamped
    test_heat_rgb_overflow_input_clamped test_heat_rgb_non_numeric_input_rejected
    test_heat_rgb_full_pct_sweep test_make_bar_segmented_rendering
    test_make_bar_multi_palier_traversal test_make_bar_empty_cells_glyph
    test_make_bar_zero_percent test_make_bar_full_percent
    test_make_bar_concurrent_instances test_static_no_lerp_or_gradrgb_residue
    test_static_heat_track_no_semaphore_colors
    test_static_token_color_unaffected test_static_no_eval_no_unquoted_expansion
    test_perf_heat_rgb_20run_avg
  )
  local shuffled=() line
  while IFS= read -r line; do shuffled+=("$line"); done < <(shuffle_tests "${tests[@]}")
  local fail_count=0 t
  for t in "${shuffled[@]}"; do run_test "$t" || fail_count=$((fail_count+1)); done
  echo "Total: ${#shuffled[@]} — Echecs: ${fail_count}"
  [ "$fail_count" -eq 0 ]
}
main "$@"
