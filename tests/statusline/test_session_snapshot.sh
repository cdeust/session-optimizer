#!/usr/bin/env bash
# tests/statusline/test_session_snapshot.sh — harness for write_session_snapshot
# (lib/session_state.sh): the verbatim per-session statusLine JSON snapshot an
# external reader (a Stream Deck plugin) consumes without re-deriving it.
#
# Same shape as test_fit_and_pace.sh: run_test isolates each test in its own
# subshell, order is randomized, fixture data is synthetic.
set -uo pipefail
SCRIPT_UNDER_TEST="${SCRIPT_UNDER_TEST:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/plugins/statusline/assets/statusline-command.sh}"
LIB_UNDER_TEST="${LIB_UNDER_TEST:-$(dirname "$SCRIPT_UNDER_TEST")/lib}"

SID="0072d0e1-0264-4b5a-8367-61e1a4cd9fbc"

assert_eq() {
  local actual="$1" expected="$2" msg="${3:-assert_eq}"
  [ "$actual" != "$expected" ] && { echo "FAIL: ${msg} — attendu [${expected}] obtenu [${actual}]" >&2; return 1; }
  return 0
}

setup() { TEST_TMPDIR="$(mktemp -d)"; export TEST_TMPDIR; STATUSLINE_STATE_DIR="$TEST_TMPDIR/state"; export STATUSLINE_STATE_DIR; }
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

snapshot_path() { printf '%s/sessions/%s.snapshot.json' "$STATUSLINE_STATE_DIR" "$1"; }

# =========================== write_session_snapshot =======================
# Sourced directly (STATUSLINE_SOURCE_ONLY=1): calls the function itself.

# On origin/main today nothing persists the stdin JSON at all — this is the
# red half of the gate this suite exists to close.
function test_write_session_snapshot_creates_the_file_byte_identical() {
  local payload='{"session_id":"'"$SID"'","cost":{"total_cost_usd":1.5}}'
  write_session_snapshot "$SID" "$payload"
  local out; out="$(snapshot_path "$SID")"
  [ -f "$out" ] || { echo "FAIL: fichier absent" >&2; return 1; }
  cmp -s <(printf '%s' "$payload") "$out" || { echo "FAIL: octets differents" >&2; return 1; }
  return 0
}

# "Verbatim" includes trailing bytes the host sent: a payload ending in a
# newline must keep it, not have it silently stripped.
function test_write_session_snapshot_preserves_a_trailing_newline() {
  local payload; payload=$(printf '{"session_id":"%s"}\n\n' "$SID")
  write_session_snapshot "$SID" "$payload"
  cmp -s <(printf '%s' "$payload") "$(snapshot_path "$SID")" \
    || { echo "FAIL: fin de ligne perdue" >&2; return 1; }
  return 0
}

# One file per session: the LAST render's bytes, never a stale one.
function test_write_session_snapshot_second_write_replaces_the_first() {
  write_session_snapshot "$SID" '{"session_id":"'"$SID"'","n":1}'
  write_session_snapshot "$SID" '{"session_id":"'"$SID"'","n":2}'
  cmp -s <(printf '%s' '{"session_id":"'"$SID"'","n":2}') "$(snapshot_path "$SID")" \
    || { echo "FAIL: la deuxieme ecriture n'a pas remplace la premiere" >&2; return 1; }
  return 0
}

# Atomicity: mv is atomic within one directory on a POSIX filesystem; pin that
# no leftover temp fragment survives a normal write.
function test_write_session_snapshot_never_leaves_a_partial_target() {
  write_session_snapshot "$SID" '{"session_id":"'"$SID"'","big":"data"}'
  local dir; dir="$(dirname "$(snapshot_path "$SID")")"
  local stray; stray=$(find "$dir" -maxdepth 1 -name "${SID}.snapshot.json.tmp.*" 2>/dev/null)
  [ -z "$stray" ] || { echo "FAIL: fragment temporaire abandonne [$stray]" >&2; return 1; }
  return 0
}

# No "/" and no ".." in a session id: a malformed or hostile id must never
# write, or be made to read, outside sessions/.
function test_write_session_snapshot_rejects_path_traversal_ids() {
  local bad
  for bad in "../../../etc/passwd" "a/b" ".." "a/../../b" "/etc/passwd"; do
    write_session_snapshot "$bad" '{"session_id":"'"$bad"'"}'
  done
  local stray; stray=$(find "$TEST_TMPDIR" -type f 2>/dev/null)
  [ -z "$stray" ] || { echo "FAIL: ecriture hors sessions/ [$stray]" >&2; return 1; }
  return 0
}

function test_write_session_snapshot_empty_id_writes_nothing() {
  write_session_snapshot "" '{"session_id":""}'
  local stray; stray=$(find "$TEST_TMPDIR" -type f 2>/dev/null)
  [ -z "$stray" ] || { echo "FAIL: ecriture avec id vide [$stray]" >&2; return 1; }
  return 0
}

# A write failure must be silent for the caller: no raise, no non-zero exit.
function test_write_session_snapshot_unwritable_dir_is_silent() {
  STATUSLINE_STATE_DIR="/nonexistent-$$/deeply/nested/path"
  write_session_snapshot "$SID" '{"session_id":"'"$SID"'"}'
  local status=$?
  assert_eq "$status" "0" "code de sortie" || return 1
  [ ! -e "$STATUSLINE_STATE_DIR" ] || { echo "FAIL: repertoire cree hors de TEST_TMPDIR" >&2; return 1; }
  return 0
}

# =========================== end-to-end (subprocess) =======================
# Through the real renderer, stdin and all: proves the wiring, not just the
# function in isolation.

function test_renderer_writes_the_snapshot_and_still_renders() {
  local payload
  payload=$(printf '{"session_id":"%s","model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp"},"context_window":{"used_percentage":42,"total_input_tokens":1000}}\n' "$SID")
  local out
  out=$(printf '%s' "$payload" \
    | STATUSLINE_LIB="$LIB_UNDER_TEST" STATUSLINE_STATE_DIR="$STATUSLINE_STATE_DIR" \
      bash "$SCRIPT_UNDER_TEST")
  case "$out" in *model*) ;; *) echo "FAIL: rendu absent [$out]" >&2; return 1 ;; esac
  cmp -s <(printf '%s' "$payload") "$(snapshot_path "$SID")" \
    || { echo "FAIL: snapshot non identique au stdin du renderer" >&2; return 1; }
  return 0
}

# The postcondition this pins: an unwritable state dir must not cost the
# render anything — same stdout as the writable run, still exit 0.
function test_renderer_output_is_unaffected_by_a_write_failure() {
  local payload
  payload=$(printf '{"session_id":"%s","model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp"},"context_window":{"used_percentage":42,"total_input_tokens":1000}}\n' "$SID")
  local good bad rc
  good=$(printf '%s' "$payload" \
    | STATUSLINE_LIB="$LIB_UNDER_TEST" STATUSLINE_STATE_DIR="$STATUSLINE_STATE_DIR" \
      bash "$SCRIPT_UNDER_TEST")
  # A regular file where the writer expects a directory: mkdir -p fails.
  : > "$TEST_TMPDIR/blocked"
  bad=$(printf '%s' "$payload" \
    | STATUSLINE_LIB="$LIB_UNDER_TEST" STATUSLINE_STATE_DIR="$TEST_TMPDIR/blocked" \
      bash "$SCRIPT_UNDER_TEST")
  rc=$?
  assert_eq "$rc" "0" "code de sortie sous echec d'ecriture" || return 1
  assert_eq "$bad" "$good" "rendu identique malgre l'echec d'ecriture"
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
    test_write_session_snapshot_creates_the_file_byte_identical
    test_write_session_snapshot_preserves_a_trailing_newline
    test_write_session_snapshot_second_write_replaces_the_first
    test_write_session_snapshot_never_leaves_a_partial_target
    test_write_session_snapshot_rejects_path_traversal_ids
    test_write_session_snapshot_empty_id_writes_nothing
    test_write_session_snapshot_unwritable_dir_is_silent
    test_renderer_writes_the_snapshot_and_still_renders
    test_renderer_output_is_unaffected_by_a_write_failure
  )
  local shuffled=() line
  while IFS= read -r line; do shuffled+=("$line"); done < <(shuffle_tests "${tests[@]}")
  local fail_count=0 t
  for t in "${shuffled[@]}"; do run_test "$t" || fail_count=$((fail_count+1)); done
  echo "Total: ${#shuffled[@]} — Echecs: ${fail_count}"
  [ "$fail_count" -eq 0 ]
}
main "$@"
