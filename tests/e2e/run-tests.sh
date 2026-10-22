#!/usr/bin/env bash
# tests/e2e/run-tests.sh — end-to-end journeys through two shipped plugins, the
# way a user runs them, with nothing of the repository's internals reached into.
# ci.json's "e2e" command runs this file.

# statusline: `install.sh install` into a scratch HOME, then the INSTALLED
# renderer (not the repo copy, no $STATUSLINE_LIB override) fed a Claude Code
# statusLine JSON on stdin, as Claude Code does.

# context-guard: the Stop hook fed a Stop payload on stdin, over a transcript
# below, at and above the per-model thresholds the statusline installer seeded
# in that same HOME (the shared-file contract both READMEs document: one
# ctxguard-thresholds.json, two consumers).

# Same shape as tests/statusline/test_session_snapshot.sh: run_test isolates
# each test in its own subshell with its own scratch HOME, order is randomized,
# fixture data is synthetic.
set -uo pipefail
# source: tests/statusline/test_session_snapshot.sh (same repo-root resolution)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGINS_UNDER_TEST="${PLUGINS_UNDER_TEST:-${REPO_ROOT}/plugins}"
INSTALLER="${PLUGINS_UNDER_TEST}/statusline/install.sh"
STOP_HOOK="${PLUGINS_UNDER_TEST}/context-guard/hooks/stop-context-guard.py"

assert_eq() {
  local actual="$1" expected="$2" msg="${3:-assert_eq}"
  [ "$actual" != "$expected" ] && { echo "FAIL: ${msg} — attendu [${expected}] obtenu [${actual}]" >&2; return 1; }
  return 0
}

assert_contains() {
  local haystack="$1" needle="$2" msg="${3:-assert_contains}"
  case "$haystack" in *"$needle"*) return 0 ;; esac
  echo "FAIL: ${msg} — [${needle}] absent de [${haystack}]" >&2
  return 1
}

# A fresh session id per test: the Stop guard keys its once-per-level state
# file in /tmp on it (stop-context-guard.py STATE_DIR), so a reused id would
# make a rerun silently pass the below case and fail the above one.
new_session_id() { printf 'e2e-%s-%s-%s' "$$" "$RANDOM" "$(date +%s)"; }

setup() {
  TEST_TMPDIR="$(mktemp -d)"; export TEST_TMPDIR
  HOME="$TEST_TMPDIR/home"; export HOME
  mkdir -p "$HOME"
  SID="$(new_session_id)"
  unset STATUSLINE_LIB STATUSLINE_DIR STATUSLINE_STATE_DIR
}
teardown() {
  [ -n "${SID:-}" ] && rm -f "/tmp/zetetic-ctxguard-${SID}.json"
  [ -n "${TEST_TMPDIR:-}" ] && rm -rf "$TEST_TMPDIR"
  unset TEST_TMPDIR SID
}

run_test() {
  local test_name="$1"
  ( setup; trap teardown EXIT; "$test_name" )
  local status=$?
  [ $status -eq 0 ] && echo "PASS: ${test_name}" || echo "FAIL: ${test_name}"
  return $status
}

# ----------------------------- fixtures ------------------------------------

# The statusLine JSON Claude Code pipes to the renderer: the fields the
# renderer reads (statusline-command.sh, "input" block). No transcript_path,
# so no telemetry subprocess is backgrounded by the test.
statusline_payload() {
  printf '{"session_id":"%s","model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"%s"},"context_window":{"used_percentage":42,"total_input_tokens":84000},"cost":{"total_cost_usd":1.25,"total_duration_ms":65000,"total_lines_added":12,"total_lines_removed":3}}\n' \
    "$SID" "$TEST_TMPDIR/project"
}

# One Claude transcript line: an assistant turn carrying the model, a tool_use
# block (the "real activity" the guard requires before it fires) and the usage
# record whose three input counters sum to $1 context tokens.
# source: plugins/context-guard/hooks/transcript_lines.py
transcript_line() {
  local ctx="$1" cached=$(( $1 / 2 ))
  printf '{"type":"assistant","message":{"model":"claude-opus-4-1-20250805","content":[{"type":"tool_use","name":"Read","input":{}}],"usage":{"input_tokens":%s,"cache_creation_input_tokens":0,"cache_read_input_tokens":%s}}}\n' \
    "$(( ctx - cached ))" "$cached"
}

# Writes a transcript with $1 context tokens on its last assistant turn and
# prints its path.
write_transcript() {
  local path="$TEST_TMPDIR/transcript.jsonl"
  { printf '{"type":"user","message":{"role":"user","content":"hello"}}\n'; transcript_line "$1"; } > "$path"
  printf '%s' "$path"
}

# source: stop-context-guard.py docstring, "Precondition" (the Stop payload)
stop_payload() {
  printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","hook_event_name":"Stop","stop_hook_active":false}' \
    "$SID" "$1" "$TEST_TMPDIR/project"
}

run_stop_hook() { stop_payload "$1" | python3 "$STOP_HOOK"; }

install_statusline() {
  mkdir -p "$TEST_TMPDIR/project"
  bash "$INSTALLER" install > "$TEST_TMPDIR/install.log" 2>&1 \
    || { echo "FAIL: install.sh install — $(cat "$TEST_TMPDIR/install.log")" >&2; return 1; }
}

# ----------------------------- statusline ----------------------------------

function test_statusline_install_then_render_shows_the_session_facts() {
  install_statusline || return 1
  local installed="$HOME/.claude/statusline/statusline-command.sh"
  [ -x "$installed" ] || { echo "FAIL: renderer non installe [$installed]" >&2; return 1; }
  local out rc
  out=$(statusline_payload | STATUSLINE_COLS=200 bash "$installed"); rc=$?
  assert_eq "$rc" "0" "code de sortie du renderer installe" || return 1
  # The documented words, each followed by its value; ANSI codes sit between
  # them, so they are asserted as separate substrings.
  assert_contains "$out" "model " "segment model" || return 1
  assert_contains "$out" "Opus 4.8" "nom du modele" || return 1
  assert_contains "$out" "dir " "segment dir" || return 1
  assert_contains "$out" "project" "repertoire courant" || return 1
  assert_contains "$out" "context " "segment context" || return 1
  assert_contains "$out" "42%" "pourcentage de contexte" || return 1
  assert_contains "$out" "84k tokens" "compteur de tokens" || return 1
  assert_contains "$out" "edits " "segment edits" || return 1
  assert_contains "$out" "+12" "lignes ajoutees" || return 1
  assert_contains "$out" "-3" "lignes retirees"
}

function test_statusline_install_wires_settings_and_the_verbatim_snapshot() {
  install_statusline || return 1
  local want="bash $HOME/.claude/statusline/statusline-command.sh"
  assert_eq "$(jq -r '.statusLine.command // ""' "$HOME/.claude/settings.json")" "$want" \
    "statusLine.command dans settings.json" || return 1
  statusline_payload | STATUSLINE_COLS=200 bash "$HOME/.claude/statusline/statusline-command.sh" >/dev/null || return 1
  local snapshot="$HOME/.claude/statusline/state/sessions/${SID}.snapshot.json"
  [ -f "$snapshot" ] || { echo "FAIL: snapshot absent [$snapshot]" >&2; return 1; }
  cmp -s <(statusline_payload) "$snapshot" || { echo "FAIL: snapshot non identique au stdin" >&2; return 1; }
  [ -f "$HOME/.claude/ctxguard-thresholds.json" ] \
    || { echo "FAIL: ctxguard-thresholds.json non seme a la racine de ~/.claude" >&2; return 1; }
  return 0
}

# A second install over the first changes nothing: the first install opens one
# backup run (the settings.json.before copy wire_settings keeps); the second
# must add none and leave every installed file byte-identical.
backup_runs() { find "$HOME/.claude/statusline/state/backup" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' '; }

function test_statusline_install_is_idempotent() {
  install_statusline || return 1
  local before after
  before=$(backup_runs)
  assert_eq "$before" "1" "sauvegardes ouvertes par la premiere installation" || return 1
  install_statusline || return 1
  after=$(backup_runs)
  assert_eq "$after" "$before" "sauvegardes ouvertes par une reinstallation identique" || return 1
  assert_eq "$(grep -c '^\[statusline\]' "$TEST_TMPDIR/install.log")" "0" "changements rapportes par une reinstallation identique" || return 1
  diff -r "$PLUGINS_UNDER_TEST/statusline/assets/lib" "$HOME/.claude/statusline/lib" >/dev/null \
    || { echo "FAIL: lib/ installe differe du bundle" >&2; return 1; }
  return 0
}

# ----------------------------- context-guard -------------------------------
# Thresholds for "claude-opus-4-1-...": warn 180000, hard 200000, read from the
# ctxguard-thresholds.json the statusline installer seeded in this HOME.
# source: plugins/statusline/assets/ctxguard-thresholds.json, row "opus"

function test_context_guard_below_warn_is_silent_and_writes_nothing() {
  install_statusline || return 1
  local out rc
  out=$(run_stop_hook "$(write_transcript 50000)"); rc=$?
  assert_eq "$rc" "0" "code de sortie sous le seuil" || return 1
  assert_eq "$out" "" "stdout sous le seuil" || return 1
  [ ! -e "$HOME/.claude/memories/checkpoints/${SID}.md" ] \
    || { echo "FAIL: stub ecrit sous le seuil" >&2; return 1; }
  [ ! -e "/tmp/zetetic-ctxguard-${SID}.json" ] \
    || { echo "FAIL: etat de session ecrit sous le seuil" >&2; return 1; }
  return 0
}

function test_context_guard_at_warn_blocks_once_with_a_checkpoint_stub() {
  install_statusline || return 1
  local transcript out rc
  transcript="$(write_transcript 185000)"
  out=$(run_stop_hook "$transcript"); rc=$?
  assert_eq "$rc" "0" "code de sortie au seuil warn" || return 1
  assert_eq "$(printf '%s' "$out" | jq -r '.decision')" "block" "decision" || return 1
  assert_contains "$(printf '%s' "$out" | jq -r '.systemMessage')" "185,000 tokens ≥ 180,000 checkpoint threshold" "systemMessage" || return 1
  local stub="$HOME/.claude/memories/checkpoints/${SID}.md"
  [ -f "$stub" ] || { echo "FAIL: stub absent [$stub]" >&2; return 1; }
  assert_contains "$(printf '%s' "$out" | jq -r '.reason')" "$stub" "le reason nomme le stub" || return 1
  # Exactly once: the same Stop again, same level, is silent.
  out=$(run_stop_hook "$transcript"); rc=$?
  assert_eq "$rc" "0" "code de sortie au second Stop" || return 1
  assert_eq "$out" "" "stdout au second Stop (deja declenche)"
}

function test_context_guard_at_hard_cap_forces_the_checkpoint() {
  install_statusline || return 1
  local out
  out=$(run_stop_hook "$(write_transcript 205000)") || return 1
  assert_eq "$(printf '%s' "$out" | jq -r '.decision')" "block" "decision" || return 1
  assert_contains "$(printf '%s' "$out" | jq -r '.systemMessage')" "205,000 tokens ≥ 200,000 soft cap" "systemMessage"
}

# Loop safety: inside a forced continuation the guard must stay out of the way.
function test_context_guard_honours_stop_hook_active() {
  install_statusline || return 1
  local out rc
  out=$(printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","stop_hook_active":true}' \
          "$SID" "$(write_transcript 205000)" "$TEST_TMPDIR" | python3 "$STOP_HOOK"); rc=$?
  assert_eq "$rc" "0" "code de sortie" || return 1
  assert_eq "$out" "" "stdout avec stop_hook_active"
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
    test_statusline_install_then_render_shows_the_session_facts
    test_statusline_install_wires_settings_and_the_verbatim_snapshot
    test_statusline_install_is_idempotent
    test_context_guard_below_warn_is_silent_and_writes_nothing
    test_context_guard_at_warn_blocks_once_with_a_checkpoint_stub
    test_context_guard_at_hard_cap_forces_the_checkpoint
    test_context_guard_honours_stop_hook_active
  )
  local shuffled=() line
  while IFS= read -r line; do shuffled+=("$line"); done < <(shuffle_tests "${tests[@]}")
  local fail_count=0 t
  for t in "${shuffled[@]}"; do run_test "$t" || fail_count=$((fail_count+1)); done
  echo "Total: ${#shuffled[@]} — Echecs: ${fail_count}"
  [ "$fail_count" -eq 0 ]
}
main "$@"
