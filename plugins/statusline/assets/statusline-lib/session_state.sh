# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/session_state.sh — the session facts the statusLine JSON omits.
#
# Single responsibility: reading per-session state out of the local files that
# the ledger and the hooks maintain. Everything in this module answers the same
# question — "what does this session look like beyond what the host told us?" —
# and changes when one of those on-disk contracts changes. Every reader is
# failure-tolerant by construction: an absent or malformed file leaves its
# outputs empty, and an empty output renders as an absent segment.
#
# Depends on: platform.sh (file_mtime).

# --- Costs: ONE ledger, ~/.claude/costs.sh over ~/.claude/statusline-costs.jsonl
# Single source of truth for every dollar figure on this statusline (session,
# today, month). Replaces the former statusline-costs.py aggregate, which
# summed every assistant line of every transcript and therefore over-counted
# ~2.2x: Claude Code re-logs one API response 2-3 times (streaming /
# tool-continuation), so a correct total MUST deduplicate on
# message.id:requestId before pricing. Measured 2026-07-26 over 172 local
# transcripts: deduped $3438.84 vs raw $7645.04 (2.22x).
# source: benchmark scratchpad/price.sh (both engines, same pricing.json).
#
# costs.sh prices each session from its OWN transcript plus the full recursive
# <transcript>/subagents/ subtree, so Task subagents, worktree-isolated agents
# and workflow agents are all billed — none of them appear in Claude Code's
# .cost.total_cost_usd, which covers the main thread only. Verified 2026-07-26:
# all 121 local agent transcripts live under <session>/subagents/, including
# the 42 whose cwd is a .claude/worktrees/agent-* path.
COST_LEDGER_SH="${HOME}/.claude/costs.sh"
COST_LOG="${STATUSLINE_COST_LOG:-${HOME}/.claude/statusline-costs.jsonl}"

# read_cost_ledger — price this session and read back the ledger totals.
# pre:  $1 the session id, $2 the raw statusLine JSON (fed to the ledger's
#       update, which is idempotent per session id and internally rate-limits
#       its jq pricing pass, so this stays cheap at a 10s refresh interval).
# post: sets cost_session cost_today cost_month cost_avg_day, each either a
#       numeric string or empty. Any non-numeric result (jq failure, empty
#       ledger) is normalised to empty, which renders as an absent segment
#       rather than as a wrong number.
read_cost_ledger() {
  local session_id="$1" payload="$2" _v
  cost_session=""; cost_today=""; cost_month=""; cost_avg_day=""
  if [ -x "$COST_LEDGER_SH" ] && [ -n "$session_id" ]; then
    printf '%s' "$payload" | "$COST_LEDGER_SH" update 2>/dev/null || true
    cost_today=$("$COST_LEDGER_SH" today 2>/dev/null) || true
    read -r cost_month cost_avg_day < <("$COST_LEDGER_SH" month 2>/dev/null) || true
    # This session's own row: main + subagents, the figure .cost.total_cost_usd
    # cannot give. Read back rather than recomputed — one grep, no second
    # pricing pass.
    if [ -r "$COST_LOG" ]; then
      cost_session=$(grep -F "\"session_id\":\"${session_id}\"" "$COST_LOG" 2>/dev/null \
        | tail -1 | jq -r '.cost_usd // empty' 2>/dev/null) || true
    fi
  fi
  # Numeric guard. Indirect read (${!v}) + printf -v write, never eval: the loop
  # must not be able to execute whatever a malformed ledger row put in the
  # variable.
  for _v in cost_session cost_today cost_month cost_avg_day; do
    case "${!_v}" in ''|*[!0-9.]*) printf -v "$_v" '%s' '' ;; esac
  done
}

# --- Per-session transcript telemetry (tok/s, compactions, last-response age,
# prompt-cache TTL). Backgrounded scan on a short TTL: the scanner reads only
# the transcript tail + appended bytes, so it is cheap, and the time-relative
# values (age, TTL) are recomputed live in bash from the cached last_ts so the
# countdown stays second-accurate between refreshes. ---
TXT_CACHE="${HOME}/.claude/.statusline-transcript-cache.json"
TXT_SCRIPT="${HOME}/.claude/statusline-transcript.py"
TXT_LOCK="${HOME}/.claude/.statusline-transcript.lock"
TXT_TTL=15            # refresh telemetry at most ~once per refresh-and-a-half
TXT_LOCK_TTL=60       # a scan that died mid-flight cannot block the next one
                      # for longer than this

# read_transcript_telemetry — last-response timestamp, throughput and compaction
# count for the transcript at $1.
# pre:  $1 the session's transcript path (may be empty), $2 current epoch seconds.
# post: sets txt_last_ts txt_tok_s txt_compactions, each either the cached value
#       or empty. Empty when the cache is missing, stale-but-unrefreshed, or
#       belongs to a DIFFERENT transcript — showing another session's throughput
#       would be worse than showing none. May start one backgrounded scan.
read_transcript_telemetry() {
  local transcript_path="$1" now_epoch="$2" txt_age txt_lock_age txt_path
  txt_last_ts=""; txt_tok_s=""; txt_compactions=""

  if [ -n "$transcript_path" ] && [ -r "$TXT_SCRIPT" ]; then
    txt_age=99999
    [ -r "$TXT_CACHE" ] && txt_age=$(( now_epoch - $(file_mtime "$TXT_CACHE") ))
    if [ "$txt_age" -ge "$TXT_TTL" ]; then
      txt_lock_age=99999
      [ -f "$TXT_LOCK" ] && txt_lock_age=$(( now_epoch - $(file_mtime "$TXT_LOCK") ))
      if [ "$txt_lock_age" -ge "$TXT_LOCK_TTL" ]; then
        ( touch "$TXT_LOCK"; python3 "$TXT_SCRIPT" "$transcript_path" >/dev/null 2>&1; rm -f "$TXT_LOCK" ) &
      fi
    fi
  fi

  if [ -r "$TXT_CACHE" ]; then
    read -r txt_path txt_last_ts txt_tok_s txt_compactions < <(
      jq -r '"\(.path // "") \(.last_ts // "") \(.tok_per_s // "") \(.compactions // "")"' "$TXT_CACHE" 2>/dev/null
    ) || true
    # Only trust the cache if it belongs to THIS session's transcript.
    [ "$txt_path" != "$transcript_path" ] && { txt_last_ts=""; txt_tok_s=""; txt_compactions=""; }
  fi
  return 0
}

# --- Subagent activity, from the SubagentStop tracker. Not in the statusline
# input, so read the per-session aggregate the hook maintains. Count and token
# volume only: the tracker's cost_usd is deliberately NOT read, because subagent
# dollars already sit inside the ledger's session figure (costs.sh prices the
# subagents/ subtree). Two independently priced numbers for the same spend can
# only diverge, so there is exactly one. ---

# read_subagent_totals — how many subagents ran this session, and their tokens.
# pre:  $1 the session id (may be empty).
# post: sets sub_count and sub_tokens. sub_count is empty when no tracker state
#       exists OR when the count is zero — the segment is about activity, and
#       "subagents 0" is noise on every session that never spawned one.
read_subagent_totals() {
  local session_id="$1" state
  sub_count=""; sub_tokens=""
  [ -z "$session_id" ] && return 0
  state="/tmp/zetetic-subagents-${session_id}.json"
  [ -r "$state" ] || return 0
  read -r sub_count sub_tokens < <(
    jq -r '.totals as $t
           | "\($t.count // 0) "
           + "\(((($t.input_tokens // 0) + ($t.output_tokens // 0) + ($t.cache_tokens // 0))))"' \
      "$state" 2>/dev/null
  ) || true
  case "$sub_count" in ''|0|*[!0-9]*) sub_count="" ;; esac
  return 0
}
