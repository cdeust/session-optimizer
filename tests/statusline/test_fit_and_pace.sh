#!/usr/bin/env bash
# tests/statusline/test_fit_and_pace.sh — harness for the width-fitting and
# rate-limit-pace functions of statusline-command.sh:
#   vislen, vistrunc, fit_line          (width fitting)
#   pace_ratio, pace_rank, quota_rank,
#   rank_color, quota_reading           (rate-limit severity)
#   file_mtime                          (cross-platform stat)
#
# Same shape as test_heat_rgb.sh: each test runs in its own subshell via
# run_test (setup/teardown, trap EXIT), execution order is randomized on every
# run, and all fixture data is synthetic (no production data, no PII).
#
# The functions under test live in statusline-lib/ and are loaded by the main
# script above its STATUSLINE_SOURCE_ONLY guard, so sourcing that one script
# loads every module without reading stdin or rendering.
set -uo pipefail
SCRIPT_UNDER_TEST="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/plugins/statusline/assets/statusline-command.sh}"

# The renderer is a composition root plus a directory of modules. A static check
# that reads only the main script would pass vacuously on any rule the modules
# are the ones violating, so every static test below sweeps the WHOLE source
# set. Resolved from the script under test, so pointing SCRIPT_UNDER_TEST at an
# installed copy checks that copy's own modules.
LIB_UNDER_TEST="${LIB_UNDER_TEST:-$(dirname "$SCRIPT_UNDER_TEST")/statusline-lib}"
SOURCES_UNDER_TEST=("$SCRIPT_UNDER_TEST")
for _f in "$LIB_UNDER_TEST"/*.sh; do
  [ -r "$_f" ] && SOURCES_UNDER_TEST+=("$_f")
done
unset _f

# Size cap from rules/coding-standards.md §4.1. Asserted here rather than left to
# review: the file this suite covers was split precisely because it had grown
# past it, and a cap nothing measures grows back.
MAX_FILE_LINES=500

assert_eq() {
  local actual="$1" expected="$2" msg="${3:-assert_eq}"
  [ "$actual" != "$expected" ] && { echo "FAIL: ${msg} — attendu [${expected}] obtenu [${actual}]" >&2; return 1; }
  return 0
}
assert_le() {
  local actual="$1" bound="$2" msg="${3:-assert_le}"
  [ "$actual" -le "$bound" ] || { echo "FAIL: ${msg} — ${actual} > ${bound}" >&2; return 1; }
  return 0
}

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

# =========================== vislen ======================================

function test_vislen_plain_ascii() {
  assert_eq "$(vislen "abc")" "3" "ascii" || return 1
  assert_eq "$(vislen "")" "0" "chaine vide"
}

# SGR sequences are zero-width: the same visible text must measure the same
# whether or not it is coloured.
function test_vislen_ignores_sgr() {
  assert_eq "$(vislen "${RED}abc${RESET}")" "3" "texte colore" || return 1
  assert_eq "$(vislen "${RED}${RESET}")" "0" "sequences seules" || return 1
  assert_eq "$(vislen "a${GREEN}b${RESET}c")" "3" "sequence au milieu"
}

# The block/box glyphs this renderer uses are multi-byte but single-column;
# counting their bytes would over-measure every bar by 20 columns and make
# fit_line trim lines that fit.
function test_vislen_multibyte_glyphs_count_one_column() {
  assert_eq "$(vislen "█")" "1" "bloc plein" || return 1
  assert_eq "$(vislen "░")" "1" "bloc vide" || return 1
  assert_eq "$(vislen "│")" "1" "separateur" || return 1
  assert_eq "$(vislen "…")" "1" "ellipse"
}

function test_vislen_measures_a_real_bar() {
  # make_bar 62 10 is ten cells, each one column, whatever colouring it applies
  assert_eq "$(vislen "$(make_bar 62 10)")" "10" "barre 10 cellules"
}

# A truncated write (or a future palette bug) must not spin the scanner: an
# unterminated SGR ends the scan instead of looping on a string that never
# shortens.
function test_vislen_unterminated_sgr_terminates() {
  assert_eq "$(vislen "abc\\033[38;2;1")" "3" "SGR non terminee"
}

# =========================== vistrunc ====================================

function test_vistrunc_leaves_short_input_untouched() {
  assert_eq "$(vistrunc "abc" 10)" "abc" "entree deja courte"
}

function test_vistrunc_respects_budget_and_marks_the_cut() {
  local out; out="$(vistrunc "abcdefghij" 5)"
  assert_le "$(vislen "$out")" "5" "budget respecte" || return 1
  case "$out" in *…*) ;; *) echo "FAIL: coupe non signalee" >&2; return 1 ;; esac
  return 0
}

# The cut must never land inside an escape sequence, and must close the colour
# it was in — otherwise the truncation bleeds into the rest of the terminal.
function test_vistrunc_never_splits_an_escape() {
  local out; out="$(vistrunc "${RED}abcdefghij${RESET}" 5)"
  assert_le "$(vislen "$out")" "5" "budget respecte" || return 1
  case "$out" in *"\\033[38;2;232;97;84m"*) ;; *) echo "FAIL: sequence tronquee" >&2; return 1 ;; esac
  case "$out" in *"\\033[0m") ;; *) echo "FAIL: couleur non refermee" >&2; return 1 ;; esac
  return 0
}

function test_vistrunc_zero_budget_is_empty() {
  assert_eq "$(vistrunc "abcdef" 0)" "" "budget nul"
}

# =========================== fit_line ====================================

# Helper: build a line from segments joined the way the renderer joins them.
_mkline() {
  local out="" s
  for s in "$@"; do out="${out:+$out ${SEP} }$s"; done
  printf '%s' "$out"
}

function test_fit_line_passthrough_when_it_fits() {
  local line; line="$(_mkline aaa bbb ccc)"
  assert_eq "$(fit_line "$line" 200)" "$line" "ligne deja dans le budget"
}

# Overflow drops WHOLE trailing segments — the head of the line, which carries
# the most important material, survives intact.
function test_fit_line_drops_lowest_priority_tail() {
  local line out; line="$(_mkline aaa bbb ccc ddd)"
  out="$(fit_line "$line" 12)"
  assert_le "$(vislen "$out")" "12" "budget respecte" || return 1
  case "$out" in aaa*) ;; *) echo "FAIL: tete perdue" >&2; return 1 ;; esac
  case "$out" in *ddd*) echo "FAIL: queue conservee hors budget" >&2; return 1 ;; esac
  return 0
}

# A line whose FIRST segment already overflows is truncated in place, never
# dropped: a status line that renders empty tells the reader nothing at all.
function test_fit_line_truncates_oversized_first_segment() {
  local out; out="$(fit_line "$(_mkline aaaaaaaaaaaaaaaaaaaa bbb)" 8)"
  assert_le "$(vislen "$out")" "8" "budget respecte" || return 1
  [ -n "$out" ] || { echo "FAIL: ligne vide" >&2; return 1; }
  return 0
}

function test_fit_line_never_returns_empty() {
  local w out
  for w in 1 2 3 5 8 13 21 34; do
    out="$(fit_line "$(_mkline "${RED}alpha${RESET}" "${GREEN}beta${RESET}" gamma)" "$w")"
    [ -z "$out" ] && { echo "FAIL: vide a largeur ${w}" >&2; return 1; }
    assert_le "$(vislen "$out")" "$w" "budget a largeur ${w}" || return 1
  done
  return 0
}

# A budget the caller could not resolve must not silently blank the line.
function test_fit_line_invalid_budget_passes_through() {
  local line; line="$(_mkline aaa bbb)"
  assert_eq "$(fit_line "$line" "")" "$line" "budget vide" || return 1
  assert_eq "$(fit_line "$line" 0)" "$line" "budget nul"
}

# =========================== pace_ratio ==================================
# Fixture clock: NOW is fixed so the tests do not depend on wall time.
NOW=1000000000

function test_pace_ratio_on_pace_is_100() {
  # half the window gone, half the quota spent -> exactly on pace
  assert_eq "$(pace_ratio 50 $((NOW + RL_5H_WINDOW_S / 2)) "$RL_5H_WINDOW_S" "$NOW")" "100" "50%/50%"
}

function test_pace_ratio_under_and_over() {
  assert_eq "$(pace_ratio 25 $((NOW + RL_5H_WINDOW_S / 2)) "$RL_5H_WINDOW_S" "$NOW")" "50"  "sous le rythme" || return 1
  assert_eq "$(pace_ratio 75 $((NOW + RL_5H_WINDOW_S / 2)) "$RL_5H_WINDOW_S" "$NOW")" "150" "au-dessus du rythme"
}

# Below PACE_MIN_ELAPSED_PCT the denominator is too small for the extrapolation
# to mean anything, so the function reports nothing rather than a number that
# would flip colour on every refresh.
function test_pace_ratio_silent_early_in_window() {
  local remaining=$(( RL_5H_WINDOW_S - RL_5H_WINDOW_S * (PACE_MIN_ELAPSED_PCT - 1) / 100 ))
  pace_ratio 5 $((NOW + remaining)) "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 \
    && { echo "FAIL: doit rester muet sous le seuil" >&2; return 1; }
  return 0
}

# Clock skew or a stale resets_at must clamp, never divide by a negative or
# zero elapsed time.
function test_pace_ratio_clamps_out_of_range_clock() {
  # reset already in the past -> window fully elapsed -> ratio == used%
  assert_eq "$(pace_ratio 42 $((NOW - 99999)) "$RL_5H_WINDOW_S" "$NOW")" "42" "reset passe" || return 1
  # reset further away than the window is long -> nothing elapsed -> silent
  pace_ratio 42 $((NOW + RL_5H_WINDOW_S * 3)) "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 \
    && { echo "FAIL: fenetre non entamee doit rester muette" >&2; return 1; }
  return 0
}

function test_pace_ratio_rejects_bad_input() {
  pace_ratio "abc" "$NOW" "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 && { echo "FAIL: pct non numerique" >&2; return 1; }
  pace_ratio 50 "later" "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 && { echo "FAIL: reset non numerique" >&2; return 1; }
  pace_ratio 50 "$NOW" 0 "$NOW" >/dev/null 2>&1 && { echo "FAIL: fenetre nulle" >&2; return 1; }
  pace_ratio "" "" "" "" >/dev/null 2>&1 && { echo "FAIL: entrees vides" >&2; return 1; }
  return 0
}

# =========================== ranks =======================================

function test_quota_rank_boundaries() {
  assert_eq "$(quota_rank 0)"  "$RANK_OK"     "0%"  || return 1
  assert_eq "$(quota_rank $((QUOTA_WARN_PCT - 1)))"   "$RANK_OK"     "sous warn"   || return 1
  assert_eq "$(quota_rank "$QUOTA_WARN_PCT")"         "$RANK_WARN"   "warn"        || return 1
  assert_eq "$(quota_rank $((QUOTA_DANGER_PCT - 1)))" "$RANK_WARN"   "sous danger" || return 1
  assert_eq "$(quota_rank "$QUOTA_DANGER_PCT")"       "$RANK_DANGER" "danger"
}

function test_pace_rank_boundaries() {
  assert_eq "$(pace_rank 0)"   "$RANK_OK"     "0x"           || return 1
  assert_eq "$(pace_rank $((PACE_WARN_RATIO - 1)))"   "$RANK_OK"     "sous warn" || return 1
  assert_eq "$(pace_rank "$PACE_WARN_RATIO")"         "$RANK_WARN"   "warn"      || return 1
  assert_eq "$(pace_rank $((PACE_DANGER_RATIO - 1)))" "$RANK_WARN"   "sous cap"  || return 1
  assert_eq "$(pace_rank "$PACE_DANGER_RATIO")"       "$RANK_DANGER" "projette le cap"
}

function test_rank_color_maps_the_scale() {
  assert_eq "$(rank_color "$RANK_OK")"     "$GREEN"  "ok"     || return 1
  assert_eq "$(rank_color "$RANK_WARN")"   "$YELLOW" "warn"   || return 1
  assert_eq "$(rank_color "$RANK_DANGER")" "$RED"    "danger"
}

# =========================== quota_reading ===============================

# The pace ratio is the only field that can be empty, so it is emitted last:
# `read` collapses interior blank fields but leaves a trailing one empty. If the
# order ever changes, the caller's variables silently shift by one.
function test_quota_reading_fields_do_not_shift_when_pace_is_absent() {
  local pct rank prank ratio
  # reset far beyond the window -> nothing elapsed -> no pace
  read -r pct rank prank ratio < <(quota_reading 42 $((NOW + RL_5H_WINDOW_S * 3)) "$RL_5H_WINDOW_S" "$NOW")
  assert_eq "$pct" "42" "pct" || return 1
  assert_eq "$rank" "$RANK_OK" "rank" || return 1
  assert_eq "$prank" "$RANK_OK" "pace rank" || return 1
  assert_eq "$ratio" "" "ratio absent"
}

function test_quota_reading_rounds_the_percentage() {
  local pct rest
  read -r pct rest < <(quota_reading 68.9 $((NOW + 1)) "$RL_7D_WINDOW_S" "$NOW")
  assert_eq "$pct" "69" "arrondi"
}

# The combined rank is the WORSE of the two readings — this is the whole point
# of showing pace at all.
function test_quota_reading_combines_worse_of_absolute_and_pace() {
  local pct rank prank ratio
  # low absolute (30%), burning at 3x: absolute alone would stay green
  read -r pct rank prank ratio < <(quota_reading 30 $((NOW + RL_5H_WINDOW_S * 9 / 10)) "$RL_5H_WINDOW_S" "$NOW")
  assert_eq "$ratio" "300" "rythme 3x" || return 1
  assert_eq "$prank" "$RANK_DANGER" "rythme dangereux" || return 1
  assert_eq "$rank" "$RANK_DANGER" "rang combine suit le pire" || return 1

  # high absolute (85% = danger) with the window essentially over: the pace is
  # only a warning (85% projected lands in the absolute danger band but not at
  # the cap), so the combined rank has to come from the absolute side.
  # Note a pace rank of OK is unreachable here by construction: with the window
  # fully elapsed the ratio equals used%, so any percentage high enough to be an
  # absolute danger is also at least a pace warning. A nearly-spent window can
  # never read as healthy — which is the intended property.
  read -r pct rank prank ratio < <(quota_reading 85 $((NOW + 1)) "$RL_5H_WINDOW_S" "$NOW")
  assert_eq "$prank" "$RANK_WARN" "rythme en avertissement" || return 1
  assert_eq "$rank" "$RANK_DANGER" "rang combine suit l'absolu"
}

function test_quota_reading_rejects_absent_window() {
  quota_reading "" "" "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 && { echo "FAIL: pourcentage absent" >&2; return 1; }
  quota_reading "abc" "$NOW" "$RL_5H_WINDOW_S" "$NOW" >/dev/null 2>&1 && { echo "FAIL: non numerique" >&2; return 1; }
  return 0
}

# =========================== probe_cols ==================================
# The probe walks four sources in order. Only the $STATUSLINE_COLS rung is
# reachable as-is from a test process, because the others depend on whether the
# runner happens to own a terminal — which differs between a developer's shell
# and CI. Each lower rung is therefore reached by shadowing the command that
# rung calls with a failing stub: probe_cols invokes `stty` and `tput` as plain
# commands, so a function of the same name defined in the test's subshell wins.
# No production seam is added for the tests, and no case depends on the runner.

function test_probe_cols_env_override_wins() {
  assert_eq "$(STATUSLINE_COLS=42 COLUMNS=137 probe_cols)" "42" "override" || return 1
  assert_eq "$(STATUSLINE_COLS=300 probe_cols)" "300" "override large"
}

# The postcondition is "a positive integer, always". The override is an escape
# hatch, not an exemption: garbage must fall through to the probes, never reach
# the caller, where it would break the arithmetic width comparisons.
# shellcheck disable=SC2329  # The stubs below ARE invoked — by name
# shadowing from inside probe_cols, which shellcheck cannot follow.
function test_probe_cols_rejects_a_non_numeric_override() {
  local out
  for bad in "abc" "0" "-5" "80x24" " "; do
    out=$(stty() { return 1; }; tput() { return 1; }
          STATUSLINE_COLS="$bad" COLUMNS=137 probe_cols)
    assert_eq "$out" "137" "override invalide [$bad]" || return 1
  done
  return 0
}

# shellcheck disable=SC2329  # The stubs below ARE invoked — by name
# shadowing from inside probe_cols, which shellcheck cannot follow.
function test_probe_cols_falls_back_to_columns_when_the_tty_is_unreadable() {
  local out
  out=$(stty() { return 1; }; unset STATUSLINE_COLS; COLUMNS=137; probe_cols)
  assert_eq "$out" "137" "COLUMNS" || return 1
  # A zero or non-numeric COLUMNS is no better than none: keep walking.
  out=$(stty() { return 1; }; tput() { return 1; }
        unset STATUSLINE_COLS; COLUMNS=0; probe_cols)
  assert_eq "$out" "200" "COLUMNS nul ignore"
}

# The regression this rung exists for: with stdout on a pipe — which is how the
# host captures this renderer — `tput cols` cannot query anything and answers
# terminfo's blind 80, silently downgrading the preset on every IDE and web
# session. It must NOT be consulted, so the answer here is the wide fallback and
# never the stub's 80. Command substitution puts stdout on a pipe, so this is
# the real condition, not a simulated one.
# shellcheck disable=SC2329  # The stubs below ARE invoked — by name
# shadowing from inside probe_cols, which shellcheck cannot follow.
function test_probe_cols_ignores_tput_when_stdout_is_not_a_terminal() {
  local out
  out=$(stty() { return 1; }; tput() { printf '80'; }
        unset STATUSLINE_COLS COLUMNS; probe_cols)
  assert_eq "$out" "200" "tput consulte hors terminal" || return 1
  case "$out" in 80) echo "FAIL: 80 aveugle de terminfo" >&2; return 1 ;; esac
  return 0
}

# When nothing can answer, the guess is deliberately WIDE: an over-generous
# width reproduces the pre-width-aware rendering, an over-tight one hides
# information that would have fitted.
# shellcheck disable=SC2329  # The stubs below ARE invoked — by name
# shadowing from inside probe_cols, which shellcheck cannot follow.
function test_probe_cols_final_fallback_is_wide() {
  local out
  out=$(stty() { return 1; }; tput() { return 1; }
        unset STATUSLINE_COLS COLUMNS; probe_cols)
  assert_eq "$out" "200" "repli large" || return 1
  [ "$out" -ge "$SIZE_L_MIN_COLS" ] || { echo "FAIL: repli sous le seuil l" >&2; return 1; }
  return 0
}

# =========================== resolve_preset ==============================
# resolve_preset sets SIZE/RANK/CTX_W/BW rather than printing, so each case runs
# it directly and reads the globals. BUDGET_CONFIG is repointed at a temp file
# to drive the config rung without touching the developer's real config.

# The env pin is the escape hatch for a terminal whose width cannot be probed,
# so width must never override it — not even at a width where the preset cannot
# structurally fit.
# shellcheck disable=SC2153  # RANK is an OUTPUT of resolve_preset, set in
# the sourced module; shellcheck cannot follow that boundary.
function test_resolve_preset_env_pin_is_honoured_at_any_width() {
  STATUSLINE_SIZE=xl resolve_preset 40
  assert_eq "$SIZE" "xl" "pin xl etroit" || return 1
  assert_eq "$RANK" "4" "rang xl" || return 1
  assert_eq "$CTX_W" "16" "CTX_W xl" || return 1
  assert_eq "$BW" "20" "BW xl" || return 1
  STATUSLINE_SIZE=xs resolve_preset 300
  assert_eq "$SIZE" "xs" "pin xs large" || return 1
  assert_eq "$RANK" "0" "rang xs"
}

function test_resolve_preset_ignores_an_invalid_env_pin() {
  BUDGET_CONFIG="$TEST_TMPDIR/absent.json"
  STATUSLINE_SIZE=huge resolve_preset 300
  assert_eq "$SIZE" "l" "pin invalide -> defaut" || return 1
  STATUSLINE_SIZE="" resolve_preset 300
  assert_eq "$SIZE" "l" "pin vide -> defaut"
}

function test_resolve_preset_defaults_to_l_on_a_wide_terminal() {
  BUDGET_CONFIG="$TEST_TMPDIR/absent.json"
  unset STATUSLINE_SIZE
  resolve_preset 300
  assert_eq "$SIZE" "l" "defaut" || return 1
  assert_eq "$RANK" "3" "rang l" || return 1
  assert_eq "$CTX_W" "10" "CTX_W l" || return 1
  assert_eq "$BW" "12" "BW l"
}

# The threshold is l's widest measured line rounded up. Both sides are asserted:
# a cap that fires one column early is as wrong as one that never fires.
function test_resolve_preset_width_cap_boundary() {
  BUDGET_CONFIG="$TEST_TMPDIR/absent.json"
  unset STATUSLINE_SIZE
  resolve_preset "$SIZE_L_MIN_COLS"
  assert_eq "$SIZE" "l" "au seuil" || return 1
  resolve_preset $((SIZE_L_MIN_COLS - 1))
  assert_eq "$SIZE" "s" "sous le seuil" || return 1
  assert_eq "$RANK" "1" "rang s" || return 1
  assert_eq "$CTX_W" "8" "CTX_W s"
}

# The config value is a PREFERENCE: honoured when it fits, capped when it does
# not. Treating it as a pin would leave the width rule dead for every default
# installation, since the shipped config names a size.
function test_resolve_preset_config_size_is_a_preference_capped_by_width() {
  BUDGET_CONFIG="$TEST_TMPDIR/budget.json"
  printf '{"size":"m"}' > "$BUDGET_CONFIG"
  unset STATUSLINE_SIZE
  resolve_preset 300
  assert_eq "$SIZE" "m" "preference respectee" || return 1
  resolve_preset 50
  assert_eq "$SIZE" "s" "preference plafonnee"
}

# The cap is downward-only and lands on "s", the one preset measured narrower
# than l. A preset already at or below it is left alone — a narrow terminal must
# never promote xs to s, and a wide one must never promote s to l.
function test_resolve_preset_cap_never_promotes() {
  BUDGET_CONFIG="$TEST_TMPDIR/budget.json"
  unset STATUSLINE_SIZE
  printf '{"size":"xs"}' > "$BUDGET_CONFIG"
  resolve_preset 50
  assert_eq "$SIZE" "xs" "xs conserve en etroit" || return 1
  printf '{"size":"s"}' > "$BUDGET_CONFIG"
  resolve_preset 300
  assert_eq "$SIZE" "s" "s non promu en large"
}

# =========================== file_mtime ==================================

function test_file_mtime_reads_an_existing_file() {
  local f="$TEST_TMPDIR/stamp"; : > "$f"
  local m; m="$(file_mtime "$f")"
  case "$m" in ''|*[!0-9]*) echo "FAIL: mtime non numerique [$m]" >&2; return 1 ;; esac
  [ "$m" -gt 0 ] || { echo "FAIL: mtime nul sur fichier existant" >&2; return 1; }
  return 0
}

# 0 is the documented "unavailable" answer; anything else (empty, an error
# string) would break the `now - mtime` arithmetic at every call site.
function test_file_mtime_missing_file_is_zero() {
  assert_eq "$(file_mtime "$TEST_TMPDIR/absent")" "0" "fichier absent"
}

# =========================== static checks ===============================

# Every mtime read must go through file_mtime: a bare `stat -f` is BSD-only and
# silently reads 0 on Linux, which forces a cache refresh on every invocation.
function test_static_no_bare_stat_call() {
  # Skip comment lines (which legitimately name both spellings) and the two
  # assignments inside file_mtime itself; anything left is a bare call.
  grep -nE "stat -[fc] " "${SOURCES_UNDER_TEST[@]}" \
    | grep -vE ':[[:space:]]*#' \
    | grep -vE ':[[:space:]]*m=\$\(stat' \
    && { echo "FAIL: appel stat hors de file_mtime" >&2; return 1; }
  return 0
}

# Every emitted line must pass through fit_line (via emit), or it can overflow
# the terminal and cost the block a row.
function test_static_every_line_is_emitted_through_fit() {
  grep -n "printf '%b\\\\n'" "${SOURCES_UNDER_TEST[@]}" | grep -v 'fitted' \
    && { echo "FAIL: ligne emise sans passer par fit_line" >&2; return 1; }
  return 0
}

# =========================== identity line ===============================
# Segment order IS priority order: fit_line drops from the tail. These tests pin
# the ORDER, not the text, because the order is the design decision — the
# directory must outlive the session dials on a terminal too narrow for both.

# Fixture: a populated identity line, rendered through the real function.
# `local` is visible to callees in bash, so render_identity reads these.
# shellcheck disable=SC2034  # These locals are READ by render_identity:
# bash scopes `local` dynamically, which is what makes this fixture work.
_identity_fixture() {
  local model="Opus 5" dir="session-optimizer" effort="high" thinking="true"
  render_identity
}

function test_identity_puts_dir_before_the_session_dials() {
  local line; line="$(_identity_fixture)"
  case "$line" in
    *dir*effort*) return 0 ;;
    *) echo "FAIL: dir n'est pas avant effort [${line}]" >&2; return 1 ;;
  esac
}

# The regression this pins: at a budget too small for the whole line, the
# directory has to survive and the session dials are what go.
function test_identity_keeps_dir_when_the_terminal_is_narrow() {
  local line out; line="$(_identity_fixture)"
  [ "$(vislen "$line")" -gt 40 ] || { echo "FAIL: fixture trop courte pour serrer" >&2; return 1; }
  out="$(fit_line "$line" 40)"
  assert_le "$(vislen "$out")" "40" "budget respecte" || return 1
  case "$out" in *session-optimizer*) ;; *) echo "FAIL: dir perdu au serrage [${out}]" >&2; return 1 ;; esac
  case "$out" in *thinking*) echo "FAIL: la queue a survecu au lieu d'etre coupee" >&2; return 1 ;; esac
  return 0
}

# =========================== modules =====================================

# §4.1: no source file over 500 lines. The whole point of the split.
function test_static_no_file_exceeds_the_size_cap() {
  local f n rc=0
  for f in "${SOURCES_UNDER_TEST[@]}"; do
    n=$(wc -l < "$f" | tr -d ' ')
    [ "$n" -gt "$MAX_FILE_LINES" ] && { echo "FAIL: $(basename "$f") ${n} lignes > ${MAX_FILE_LINES}" >&2; rc=1; }
  done
  return $rc
}

# The renderer is only useful if the modules actually load: this asserts the
# whole set is present and sourceable, and that sourcing still defines a
# function from EACH module rather than silently loading a subset.
function test_modules_all_load_and_define_their_functions() {
  local fn
  for fn in file_mtime heat_rgb vislen quota_reading fmt_tokens \
            resolve_ctx_thresholds collect_git read_cost_ledger \
            probe_cols render_identity; do
    declare -F "$fn" >/dev/null || { echo "FAIL: ${fn} non definie apres source" >&2; return 1; }
  done
  return 0
}

# A missing module must fail loudly. Rendering around it is impossible — without
# palette.sh there are no colours to print an error in — so the contract is:
# non-zero exit, and a line on the statusline itself naming the missing file. A
# silently blank status bar reads as "nothing is happening", which is the one
# outcome that must not happen.
function test_missing_module_fails_loudly() {
  local out rc
  out=$(STATUSLINE_LIB="$TEST_TMPDIR/absent-lib" bash "$SCRIPT_UNDER_TEST" </dev/null 2>/dev/null)
  rc=$?
  [ "$rc" -eq 0 ] && { echo "FAIL: sortie 0 malgre un module manquant" >&2; return 1; }
  case "$out" in
    *"platform.sh"*) ;;
    *) echo "FAIL: le module manquant n'est pas nomme [${out}]" >&2; return 1 ;;
  esac
  return 0
}

# Portable Fisher-Yates shuffle using bash's builtin $RANDOM — no shuf/coreutils
# dependency, so the suite runs on macOS's stock bash 3.2 as well as GNU bash.
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
    test_vislen_plain_ascii test_vislen_ignores_sgr
    test_vislen_multibyte_glyphs_count_one_column test_vislen_measures_a_real_bar
    test_vislen_unterminated_sgr_terminates
    test_vistrunc_leaves_short_input_untouched
    test_vistrunc_respects_budget_and_marks_the_cut
    test_vistrunc_never_splits_an_escape test_vistrunc_zero_budget_is_empty
    test_fit_line_passthrough_when_it_fits
    test_fit_line_drops_lowest_priority_tail
    test_fit_line_truncates_oversized_first_segment
    test_fit_line_never_returns_empty test_fit_line_invalid_budget_passes_through
    test_pace_ratio_on_pace_is_100 test_pace_ratio_under_and_over
    test_pace_ratio_silent_early_in_window
    test_pace_ratio_clamps_out_of_range_clock test_pace_ratio_rejects_bad_input
    test_quota_rank_boundaries test_pace_rank_boundaries test_rank_color_maps_the_scale
    test_quota_reading_fields_do_not_shift_when_pace_is_absent
    test_quota_reading_rounds_the_percentage
    test_quota_reading_combines_worse_of_absolute_and_pace
    test_quota_reading_rejects_absent_window
    test_probe_cols_env_override_wins
    test_probe_cols_rejects_a_non_numeric_override
    test_probe_cols_falls_back_to_columns_when_the_tty_is_unreadable
    test_probe_cols_ignores_tput_when_stdout_is_not_a_terminal
    test_probe_cols_final_fallback_is_wide
    test_resolve_preset_env_pin_is_honoured_at_any_width
    test_resolve_preset_ignores_an_invalid_env_pin
    test_resolve_preset_defaults_to_l_on_a_wide_terminal
    test_resolve_preset_width_cap_boundary
    test_resolve_preset_config_size_is_a_preference_capped_by_width
    test_resolve_preset_cap_never_promotes
    test_file_mtime_reads_an_existing_file test_file_mtime_missing_file_is_zero
    test_static_no_bare_stat_call test_static_every_line_is_emitted_through_fit
    test_identity_puts_dir_before_the_session_dials
    test_identity_keeps_dir_when_the_terminal_is_narrow
    test_static_no_file_exceeds_the_size_cap
    test_modules_all_load_and_define_their_functions
    test_missing_module_fails_loudly
  )
  local shuffled=() line
  while IFS= read -r line; do shuffled+=("$line"); done < <(shuffle_tests "${tests[@]}")
  local fail_count=0 t
  for t in "${shuffled[@]}"; do run_test "$t" || fail_count=$((fail_count+1)); done
  echo "Total: ${#shuffled[@]} — Echecs: ${fail_count}"
  [ "$fail_count" -eq 0 ]
}
main "$@"
