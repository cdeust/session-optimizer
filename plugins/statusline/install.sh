#!/usr/bin/env bash
# statusline installer: the ONE path that puts the plugin's files on disk.
#
#   install.sh install   full install or update, then verify (the /statusline skill)
#   install.sh sync      idempotent update of an existing install (the SessionStart hook)
#   install.sh verify    post-install checks, exit 1 on the first missing piece
#
# Layout it produces (issue #33: what a plugin leaves on disk is managed by the
# plugin and readable at a glance):
#
#   ~/.claude/statusline/               $STATUSLINE_DIR
#     statusline-command.sh  lib/  costs.sh  transcript.py  pricing.json  README.md
#     statusline-budget.json            user-tuned, seeded once, never overwritten
#     state/                            everything written at runtime
#       costs.jsonl (+ .lock, .cleanup-stamp, .tmp.*)
#       sessions/<session>.main|.sub    per-session price caches
#       transcript-cache.json, transcript.lock
#       backup/<timestamp>/             superseded files, newest BACKUP_KEEP runs kept
#   ~/.claude/ctxguard-thresholds.json  stays at the root: context-guard's Stop hook
#                                       reads that exact path too, and a file with
#                                       two readers lives where both find it.
#
# A pre-#33 flat install (everything at the root of ~/.claude) is migrated once:
# moved, not copied, so the root files disappear. Nothing here sleeps, retries
# or depends on the clock for a decision; every step is a file test.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="${PLUGIN_ROOT}/assets"
CLAUDE_DIR="${HOME}/.claude"
TARGET="${STATUSLINE_DIR:-${CLAUDE_DIR}/statusline}"
STATE="${TARGET}/state"
BACKUP_ROOT="${STATE}/backup"
BACKUP_KEEP=3
SETTINGS="${CLAUDE_DIR}/settings.json"
WANT_COMMAND="bash ${TARGET}/statusline-command.sh"

CODE_FILES=(statusline-command.sh costs.sh pricing.json transcript.py)
EXECUTABLES=(statusline-command.sh costs.sh)
MODULES=(platform palette fit severity format config gitctx session_state layout render pricing ledger_report)
# Names the pre-#33 layout put at the root of ~/.claude, code and leftovers alike.
LEGACY_CODE=(statusline-command.sh costs.sh pricing.json statusline-transcript.py
             README-statusline.md statusline-costs.py)

changes=()
run_dir=""

note() { changes+=("$1"); }

# The backup run directory is created on first use only, so a sync that changes
# nothing leaves no trace. Same-second runs get a padded suffix; names sort as
# time, which is what prune_backups relies on. Sets $run_dir in THIS shell:
# called plainly, never inside $(...), or every caller would open its own run.
ensure_backup_dir() {
  [ -n "$run_dir" ] && return 0
  local stamp n=1 candidate
  stamp=$(date +%Y%m%d-%H%M%S)
  candidate="${BACKUP_ROOT}/${stamp}"
  while [ -e "$candidate" ]; do
    candidate="${BACKUP_ROOT}/${stamp}-$(printf '%02d' "$n")"; n=$((n + 1))
  done
  mkdir -p "$candidate" || return 1
  run_dir="$candidate"
}

# stash <path> [<name under the run dir>]: move a superseded file or directory
# into this run's backup directory. A move, so the source disappears.
stash() {
  local src="$1" rel="${2:-$(basename "$1")}"
  [ -e "$src" ] || return 0
  ensure_backup_dir || return 1
  mkdir -p "$(dirname "${run_dir}/${rel}")"
  mv "$src" "${run_dir}/${rel}"
}

prune_backups() {
  [ -d "$BACKUP_ROOT" ] || return 0
  local old
  while IFS= read -r old; do
    [ -n "$old" ] && rm -rf "${BACKUP_ROOT:?}/${old}"
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort -r | tail -n +$((BACKUP_KEEP + 1)))
}

legacy_present() {
  [ -e "${CLAUDE_DIR}/statusline-command.sh" ] || [ -d "${CLAUDE_DIR}/statusline-lib" ] \
    || [ -e "${CLAUDE_DIR}/statusline-costs.jsonl" ] || [ -e "${CLAUDE_DIR}/costs.sh" ]
}

installed() { [ -e "${TARGET}/statusline-command.sh" ]; }

# --- Migration of a flat install -------------------------------------------

# move_state <old> <new>: the runtime file moves to its new name; when the new
# layout already holds one, the old copy is stashed rather than clobbering it.
move_state() {
  local old="$1" new="$2"
  [ -e "$old" ] || return 0
  if [ -e "$new" ]; then stash "$old"; else mv "$old" "$new"; fi
}

migrate_flat() {
  local f name sid
  mkdir -p "${STATE}/sessions" "$BACKUP_ROOT" || return 1

  move_state "${CLAUDE_DIR}/statusline-budget.json" "${TARGET}/statusline-budget.json"

  move_state "${CLAUDE_DIR}/statusline-costs.jsonl" "${STATE}/costs.jsonl"
  move_state "${CLAUDE_DIR}/statusline-costs.jsonl.cleanup-stamp" "${STATE}/costs.jsonl.cleanup-stamp"
  rmdir "${CLAUDE_DIR}/statusline-costs.jsonl.lock" 2>/dev/null
  rm -f "${CLAUDE_DIR}"/statusline-costs.jsonl.tmp.*
  for f in "${CLAUDE_DIR}"/statusline-costs.jsonl.main.* "${CLAUDE_DIR}"/statusline-costs.jsonl.sub.*; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    case "$name" in
      statusline-costs.jsonl.main.*) sid="${name#statusline-costs.jsonl.main.}"; move_state "$f" "${STATE}/sessions/${sid}.main" ;;
      statusline-costs.jsonl.sub.*)  sid="${name#statusline-costs.jsonl.sub.}";  move_state "$f" "${STATE}/sessions/${sid}.sub" ;;
    esac
  done
  for f in "${CLAUDE_DIR}"/statusline-costs.jsonl.bak.*; do stash "$f"; done

  move_state "${CLAUDE_DIR}/.statusline-transcript-cache.json" "${STATE}/transcript-cache.json"
  rm -f "${CLAUDE_DIR}/.statusline-transcript.lock"
  # Written by statusline-costs.py, which 2.1.0 removed: nothing reads it.
  stash "${CLAUDE_DIR}/.statusline-cost-cache.json"

  # Old code and the prose hook's *.bak.* copies. The bundle replaces the code
  # below; the copies go with it so the root is left to Claude Code.
  for name in "${LEGACY_CODE[@]}"; do
    stash "${CLAUDE_DIR}/${name}"
    for f in "${CLAUDE_DIR}/${name}".bak.*; do stash "$f"; done
  done
  stash "${CLAUDE_DIR}/statusline-lib" statusline-lib
  note "migrated the flat install into ${TARGET}"
}

# --- Code assets -------------------------------------------------------------

# place <source> <destination> <label>: copy when absent or different, backing
# up the previous copy first. Idempotent: an identical file is left untouched.
place() {
  local src="$1" dst="$2" label="$3"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then return 0; fi
  [ -f "$dst" ] && { stash "$dst" "$label" || return 1; }
  mkdir -p "$(dirname "$dst")" && cp "$src" "$dst" || return 1
  note "updated ${label}"
}

install_code() {
  local name m
  mkdir -p "${TARGET}/lib" "${STATE}/sessions" || return 1
  for name in "${CODE_FILES[@]}"; do
    place "${BUNDLE}/${name}" "${TARGET}/${name}" "$name" || return 1
  done
  place "${PLUGIN_ROOT}/README.md" "${TARGET}/README.md" "README.md" || return 1
  # The whole module set, as one unit: the renderer refuses to start on a
  # missing module, and a module the bundle no longer ships is stashed.
  for m in "${MODULES[@]}"; do
    place "${BUNDLE}/lib/${m}.sh" "${TARGET}/lib/${m}.sh" "lib/${m}.sh" || return 1
  done
  for name in "${TARGET}"/lib/*; do
    [ -e "$name" ] || continue
    [ -f "${BUNDLE}/lib/$(basename "$name")" ] && continue
    stash "$name" "lib/$(basename "$name")" || return 1
    note "removed lib/$(basename "$name") (no longer shipped)"
  done
  for name in "${EXECUTABLES[@]}"; do chmod +x "${TARGET}/${name}"; done
}

# Config is seeded when absent and never written again.
seed_config() {
  if [ ! -e "${TARGET}/statusline-budget.json" ]; then
    cp "${BUNDLE}/statusline-budget.json" "${TARGET}/statusline-budget.json" && note "seeded statusline-budget.json"
  fi
  if [ ! -e "${CLAUDE_DIR}/ctxguard-thresholds.json" ]; then
    cp "${BUNDLE}/ctxguard-thresholds.json" "${CLAUDE_DIR}/ctxguard-thresholds.json" \
      && note "seeded ctxguard-thresholds.json (shared with context-guard)"
  fi
}

# --- settings.json -----------------------------------------------------------

# wire_settings <mode>: point statusLine.command at the installed renderer.
# "install" always registers it; "sync" only rewrites a command that names a
# statusline-command.sh elsewhere (the flat layout), so a user who removed the
# statusline from settings.json is not re-enrolled by a hook. Other statusLine
# fields and every other setting are preserved; an unparseable file is left as
# it is and reported, never overwritten.
wire_settings() {
  local mode="$1" current tmp
  mkdir -p "$CLAUDE_DIR"
  [ -e "$SETTINGS" ] || printf '{}\n' > "$SETTINGS"
  if ! current=$(jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null); then
    note "settings.json is not valid JSON; statusLine.command left as it is"
    return 0
  fi
  [ "$current" = "$WANT_COMMAND" ] && return 0
  if [ "$mode" = "sync" ]; then
    case "$current" in *statusline-command.sh*) ;; *) return 0 ;; esac
  fi
  ensure_backup_dir && cp "$SETTINGS" "${run_dir}/settings.json.before" || return 1
  tmp="${SETTINGS}.tmp.$$"
  if jq --arg cmd "$WANT_COMMAND" '
        .statusLine = ((.statusLine // {}) + {
          type: "command", command: $cmd,
          padding: ((.statusLine // {}).padding // 1),
          refreshInterval: ((.statusLine // {}).refreshInterval // 10) })' \
      "$SETTINGS" > "$tmp" 2>/dev/null && mv "$tmp" "$SETTINGS"; then
    note "settings.json statusLine.command -> ${WANT_COMMAND}"
  else
    rm -f "$tmp"
    note "could not rewrite settings.json; statusLine.command left as it is"
  fi
}

# --- Verbs -------------------------------------------------------------------

preflight() {
  local ok=0 cmd
  for cmd in jq python3; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "BLOCKING: '$cmd' not found"; ok=1; }
  done
  command -v git >/dev/null 2>&1 || echo "OPTIONAL: 'git' not found; the branch line stays empty"
  mkdir -p "$CLAUDE_DIR" 2>/dev/null && [ -w "$CLAUDE_DIR" ] || { echo "BLOCKING: cannot write to ${CLAUDE_DIR}"; ok=1; }
  if [ -e "$SETTINGS" ] && [ ! -w "$SETTINGS" ]; then echo "BLOCKING: ${SETTINGS} not writable"; ok=1; fi
  return $ok
}

report() {
  local c
  for c in "${changes[@]}"; do printf '[statusline] %s\n' "$c"; done
}

cmd_sync() {
  installed || legacy_present || return 0
  legacy_present && { migrate_flat || return 1; }
  install_code && seed_config && wire_settings sync || return 1
  prune_backups
  report
}

cmd_install() {
  preflight || return 1
  legacy_present && { migrate_flat || return 1; }
  install_code && seed_config && wire_settings install || return 1
  prune_backups
  report
  cmd_verify
}

cmd_verify() {
  local status=0 name m
  for name in "${CODE_FILES[@]}" statusline-budget.json README.md; do
    if [ -f "${TARGET}/${name}" ]; then echo "OK: ${name} present"; else echo "ERROR: ${name} missing from ${TARGET}"; status=1; fi
  done
  for m in "${MODULES[@]}"; do
    if [ -r "${TARGET}/lib/${m}.sh" ]; then echo "OK: module ${m}.sh present"; else echo "ERROR: module ${m}.sh missing"; status=1; fi
  done
  for name in "${EXECUTABLES[@]}"; do
    if [ -x "${TARGET}/${name}" ]; then echo "OK: ${name} executable"; else echo "ERROR: ${name} not executable"; status=1; fi
  done
  if [ -f "${CLAUDE_DIR}/ctxguard-thresholds.json" ]; then echo "OK: ctxguard-thresholds.json present (shared with context-guard)"; else echo "ERROR: ctxguard-thresholds.json missing"; status=1; fi
  if [ -d "${STATE}/sessions" ]; then echo "OK: state directory present"; else echo "ERROR: ${STATE}/sessions missing"; status=1; fi
  if [ "$(jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null)" = "$WANT_COMMAND" ]; then
    echo "OK: statusLine.command registered in settings.json"
  else
    echo "ERROR: settings.json statusLine.command is not '${WANT_COMMAND}'"; status=1
  fi
  local stray=()
  for name in "${CLAUDE_DIR}"/statusline-* "${CLAUDE_DIR}"/.statusline-* "${CLAUDE_DIR}"/costs.sh \
              "${CLAUDE_DIR}"/pricing.json "${CLAUDE_DIR}"/README-statusline.md; do
    [ -e "$name" ] && stray+=("$(basename "$name")")
  done
  if [ "${#stray[@]}" -gt 0 ]; then
    echo "ERROR: flat-layout files still at the root of ${CLAUDE_DIR}:"; printf '  %s\n' "${stray[@]}"; status=1
  else
    echo "OK: nothing of the statusline at the root of ${CLAUDE_DIR}"
  fi
  if [ "$status" -eq 0 ]; then
    if printf '{"model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"%s"},"context_window":{"used_percentage":20,"total_input_tokens":200000}}' "$PWD" \
        | bash "${TARGET}/statusline-command.sh" >/dev/null 2>&1; then
      echo "OK: renderer runs"
    else
      echo "ERROR: renderer failed"; status=1
    fi
  fi
  return $status
}

case "${1:-}" in
  install) cmd_install ;;
  sync)    cmd_sync ;;
  verify)  cmd_verify ;;
  *) echo "usage: install.sh install|sync|verify" >&2; exit 2 ;;
esac
