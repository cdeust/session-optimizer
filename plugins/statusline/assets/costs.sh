#!/usr/bin/env bash
# claude-statusline cost ledger CLI.
#
# Owns the ledger, $STATUSLINE_STATE_DIR/costs.jsonl (issue #33: every file this
# script writes lives under the install's state/ directory, never at the root of
# ~/.claude). The render path (statusline-command.sh) shells out to this script
# for every refresh: `update` writes a row, `today` / `month` return current
# totals.
#
# Verbs:
#   update    stdin = Claude Code JSON; upsert a ledger row (idempotent per session_id)
#   today     echo "$X.XX"  — sum of (cost_usd - cost_at_day_start) for today's rows
#   month     echo "$X.XX $Y.YY"  — month-to-date total + weekday-average per day
#   info      human-friendly summary (today + month + avg)
#   debug     full diagnostics (per-session today, last-30-day table, ccusage cross-check, orphan tmps)
#   init      seed the ledger from ccusage (one-time, replaces existing log after backup)
#
# cost_usd source: priced locally from the session's OWN transcript (main +
# <transcript>/subagents/**/*.jsonl), via _COST_JQ — NOT from Claude Code's
# cost.total_cost_usd. That field is cumulative across a resumed/continued
# conversation: after a same-day restart, the new session_id's total_cost_usd
# already carries the prior session's spend, which would double-count against
# that prior session's already-recorded ledger row. A session's transcript
# never contains another session's messages, so per-transcript pricing cannot
# double-count on resume. Verified no overlap between a main transcript and its
# own subagents/ dir (Task-tool sidechains live inline in main; agent-team
# subagents are separate files; never both). Disable the subagent half with
# STATUSLINE_COUNT_SUBAGENTS=0.
#
# Schema (one JSON object per line):
#   {session_id, date, cost_at_day_start, cost_usd, month, month_day_start}
#
# today  = sum(cost_usd - cost_at_day_start)   where date=today
# month  = sum(cost_usd - month_day_start)      where month=current
#
# month_day_start: cost_usd at the moment this session entered the current month.
#   Set to 0 for sessions that started this month.
#   Set to prior cost_usd on a month rollover (session carried over from last month).
#   Never changes within a month — so month total is always derivable from two raw fields,
#   with no accumulation that can drift or be corrupted by concurrent writes.
#
# Day rollover:   cost_at_day_start ← prior cost_usd; month_day_start unchanged (same month)
# Month rollover: month_day_start ← prior cost_usd (carry-over baseline)
# First-seen old session (duration > elapsed since midnight): cost_at_day_start ← cost_usd
#   so its historical cost does NOT inflate today — only new spend from this point counts
# Account-switch protection: if cost_usd drops mid-session, rebaseline the day so
#   today can't go negative, but preserve the month-to-date contribution already made
#   (month_day_start absorbs the offset and may go negative).
# Concurrent-write protection: write is serialised with a mkdir lock; stale locks expire after 30s.
#
# STATUSLINE_TODAY_OVERRIDE (YYYY-MM-DD) / STATUSLINE_MONTH_OVERRIDE (YYYY-MM):
#   test-only hooks to simulate "today"/"month" without faking the system clock —
#   used to exercise the day/month rollover branches deterministically (e.g. the
#   month-boundary transition). Unset in production; real `date` is used.

# Numeric formatting must be locale-independent. Under a comma-decimal locale
# (e.g. fr_FR) awk/printf emit "19,78"; interpolated into a downstream awk
# program that value is read as TWO arguments and the amount is truncated.
# Only the numeric category is forced, matching statusline-command.sh: LC_ALL=C
# would also pin LC_CTYPE, which breaks ${#s} width measurement. LC_ALL must be
# unset first — it outranks LC_NUMERIC when the environment sets it.
unset LC_ALL
export LC_NUMERIC=C

set -u

STATUSLINE_DIR="${STATUSLINE_DIR:-${HOME}/.claude/statusline}"
STATUSLINE_STATE_DIR="${STATUSLINE_STATE_DIR:-${STATUSLINE_DIR}/state}"
COST_LOG="${STATUSLINE_COST_LOG:-${STATUSLINE_STATE_DIR}/costs.jsonl}"
# Per-session price caches (<session>.main, <session>.sub) get a directory of
# their own beside the ledger: 238 of them were measured flat at the root of
# ~/.claude on 2026-09-10. Beside the LEDGER, not the state dir, so a
# $STATUSLINE_COST_LOG override relocates the ledger and its caches together.
COST_CACHE_DIR="${STATUSLINE_COST_CACHE_DIR:-$(dirname "$COST_LOG")/sessions}"
COST_LOG_RETENTION_DAYS="${COST_LOG_RETENTION_DAYS_OVERRIDE:-30}"


# ── Modules ──────────────────────────────────────────────────────────────────
# The pricing engine and the reporting verbs live in lib/ beside this script,
# one concern per file, in the bundle and in the install alike, and platform.sh
# supplies the one cross-platform mtime reader (file_mtime). A missing module
# is a hard failure that names the file: pricing without prices is not a
# ledger. $STATUSLINE_LIB overrides the directory (test harnesses).
_COSTS_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COSTS_LIB="${STATUSLINE_LIB:-${_COSTS_HOME}/lib}"
for _mod in platform pricing ledger_report; do
    if [[ -r "${_COSTS_LIB}/${_mod}.sh" ]]; then
        # shellcheck source=/dev/null
        . "${_COSTS_LIB}/${_mod}.sh"
    else
        echo "costs.sh: module ${_mod}.sh missing from ${_COSTS_LIB}" >&2; exit 1
    fi
done

# ── Helpers ──────────────────────────────────────────────────────────────────

# Exclusive mkdir-based lock (atomic on all POSIX filesystems).
# Stale locks (> 30 s) are removed automatically.
_acquire_lock() {
    local lockdir="${COST_LOG}.lock"
    local waited=0 max_wait=50  # 50 × 0.1s = 5s
    while ! mkdir "$lockdir" 2>/dev/null; do
        local mtime now
        mtime=$(stat -c %Y "$lockdir" 2>/dev/null \
            || stat -f %m "$lockdir" 2>/dev/null || echo 0)
        now=$(date +%s)
        if (( now - mtime > 30 )); then
            rmdir "$lockdir" 2>/dev/null; continue
        fi
        (( waited >= max_wait )) && return 1
        sleep 0.1; (( waited++ )) || true
    done
    return 0
}

_release_lock() { rmdir "${COST_LOG}.lock" 2>/dev/null || true; }

# Trigger async cleanup of orphan .tmp.* files at most once per hour.
# Writes a stamp file after each run; skips entirely if stamp is recent.
_cleanup_tmps_maybe() {
    local stamp="${COST_LOG}.cleanup-stamp"
    local now; now=$(date +%s)
    local mtime=0
    [[ -f "$stamp" ]] && mtime=$(stat -c %Y "$stamp" 2>/dev/null \
        || stat -f %m "$stamp" 2>/dev/null || echo 0)
    (( now - mtime < 3600 )) && return 0   # cleaned within the last hour — skip

    # Touch stamp before forking so concurrent updates don't spawn multiple cleaners.
    touch "$stamp" 2>/dev/null

    local dir base
    dir=$(dirname "$COST_LOG"); base=$(basename "$COST_LOG")
    (
        local f mtime_f now_f; now_f=$(date +%s)
        while IFS= read -r f; do
            mtime_f=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo "$now_f")
            if (( now_f - mtime_f > 60 )); then rm -f "$f" 2>/dev/null || true; fi
        done < <(find "$dir" -maxdepth 1 -name "${base}.tmp.*" 2>/dev/null)
        # Per-session price caches (main + subagent), expire on the retention window.
        local ret_sec=$(( COST_LOG_RETENTION_DAYS * 86400 ))
        while IFS= read -r f; do
            mtime_f=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo "$now_f")
            if (( now_f - mtime_f > ret_sec )); then rm -f "$f" 2>/dev/null || true; fi
        done < <(find "$COST_CACHE_DIR" -maxdepth 1 -type f \( -name '*.sub' -o -name '*.main' \) 2>/dev/null)
    ) &
    disown 2>/dev/null || true
}

_days_ago() {
    date -v -"$1"d +%Y-%m-%d 2>/dev/null \
        || date -d "$1 days ago" +%Y-%m-%d 2>/dev/null \
        || echo "1970-01-01"
}

_midnight_ts() {
    date -v0H -v0M -v0S +%s 2>/dev/null \
        || date -d "today 00:00:00" +%s 2>/dev/null \
        || echo $(( $(date +%s) - 86400 ))
}

# Count weekdays (Mon–Fri) elapsed so far this month, including today.
_weekdays_elapsed() {
    local month day_of_month dow d dstr count=0
    month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"
    if [[ -n "${STATUSLINE_TODAY_OVERRIDE:-}" ]]; then
        day_of_month=$((10#${STATUSLINE_TODAY_OVERRIDE##*-}))  # force base-10 (strip leading-zero octal trap)
    else
        day_of_month=$(date +%-d)
    fi
    for (( d=1; d<=day_of_month; d++ )); do
        dstr="${month}-$(printf '%02d' "$d")"
        dow=$(date -j -f "%Y-%m-%d" "$dstr" +%u 2>/dev/null \
            || date -d "$dstr" +%u 2>/dev/null)
        if [[ "$dow" -le 5 ]]; then count=$(( count + 1 )); fi
    done
    [[ "$count" -lt 1 ]] && count=1
    echo "$count"
}


# ── update ───────────────────────────────────────────────────────────────────
# One row per session, upserted. The helpers below read and set the caller's
# locals (bash scopes dynamically): needs_write, day_start, month_day_start.

# _session_cost <transcript>: main + subagent spend, "%.6f", from the session's
# own transcript (see the header: total_cost_usd would double-count a resume).
_session_cost() {
    local transcript="$1" main_cost sub_cost=0
    main_cost=$(_main_cost "$transcript")
    if [[ "${STATUSLINE_COUNT_SUBAGENTS:-1}" != "0" && -n "$transcript" ]]; then
        sub_cost=$(_subagent_cost "$transcript")
    fi
    awk -v a="$main_cost" -v b="$sub_cost" 'BEGIN{printf "%.6f", a+b}'
}

# _baseline_new_session <duration_ms> <session_cost>: a session never seen (or
# an old-schema row). Started today: baseline 0. Started before today: its
# whole cost is historical, only spend from this point counts.
_baseline_new_session() {
    local duration_ms="$1" session_cost="$2" now_ts midnight_ts elapsed_today session_age_sec
    now_ts=$(date +%s); midnight_ts=$(_midnight_ts)
    elapsed_today=$(( now_ts - midnight_ts ))
    session_age_sec=$(( duration_ms / 1000 ))
    needs_write=true
    if [[ $session_age_sec -le $elapsed_today ]]; then
        day_start="0.000000"; month_day_start="0.000000"
    else
        day_start="$session_cost"; month_day_start="$session_cost"
    fi
}

# _prior_month_day_start <row> <prior_cost>: month baseline of a prior row.
# Old-schema rows (month_cost) approximate it as max(0, cost_usd - month_cost).
_prior_month_day_start() {
    local prior="$1" prior_cost="$2" old_mc
    if printf '%s' "$prior" | jq -e 'has("month_day_start")' &>/dev/null; then
        printf '%s' "$prior" | jq -r '.month_day_start // 0' | awk '{printf "%.6f", $0}'
    else
        old_mc=$(printf '%s' "$prior" | jq -r '.month_cost // 0' | awk '{printf "%.6f", $0}')
        awk "BEGIN{v=$prior_cost - $old_mc; printf \"%.6f\", (v<0)?0:v}"
    fi
}

# _baseline_from_prior <row> <session_cost> <today> <month>: day and month
# rollovers, the account-switch rebaseline, and the no-change case.
_baseline_from_prior() {
    local prior="$1" session_cost="$2" today="$3" month="$4"
    local prior_cost prior_date prior_day_start prior_month prior_mds
    prior_cost=$(printf '%s' "$prior" | jq -r '.cost_usd // 0' | awk '{printf "%.6f", $0}')
    prior_date=$(printf '%s' "$prior" | jq -r '.date // ""')
    prior_day_start=$(printf '%s' "$prior" | jq -r '.cost_at_day_start // 0' | awk '{printf "%.6f", $0}')
    prior_month=$(printf '%s' "$prior" | jq -r '.month // ""')
    prior_mds=$(_prior_month_day_start "$prior" "$prior_cost")

    if [[ "$prior_date" != "$today" ]]; then
        needs_write=true
        day_start="$prior_cost"
        # Month rollover: this session carries over; the new month starts from
        # the prior cost. Day rollover within a month: month baseline unchanged.
        if [[ "$prior_month" != "$month" ]]; then month_day_start="$prior_cost"; else month_day_start="$prior_mds"; fi
    elif [[ "$prior_cost" != "$session_cost" ]]; then
        needs_write=true
        if awk "BEGIN{exit !($session_cost < $prior_cost)}"; then
            # Cost dropped: account switch or SDK reset. Rebaseline the day so
            # today's delta cannot go negative, but PRESERVE the month-to-date
            # contribution so far (prior_cost - prior_mds) by carrying it into
            # the new baseline. month_day_start may go negative: it is a pure
            # offset, and month sums clamp at 0.
            day_start="$session_cost"
            month_day_start=$(awk "BEGIN{printf \"%.6f\", $session_cost - ($prior_cost - $prior_mds)}")
        else
            day_start="$prior_day_start"; month_day_start="$prior_mds"
        fi
    else
        day_start="$prior_day_start"; month_day_start="$prior_mds"
    fi
}

# _write_ledger_row: rewrite the ledger without this session's old row and
# without rows past the retention cutoff, then append the new row.
# reads: session_id today month session_cost cutoff day_start month_day_start tmp
_write_ledger_row() {
    {
        if [[ -s "$COST_LOG" ]]; then
            jq -c --arg sid "$session_id" --arg cutoff "$cutoff" \
                'select(.session_id != $sid and .date >= $cutoff)' \
                "$COST_LOG" 2>/dev/null
        fi
        jq -cn \
            --arg     sid   "$session_id"      \
            --arg     date  "$today"            \
            --argjson start "$day_start"        \
            --argjson cur   "$session_cost"     \
            --arg     mon   "$month"            \
            --argjson mds   "$month_day_start"  \
            '{session_id:$sid,date:$date,cost_at_day_start:$start,cost_usd:$cur,month:$mon,month_day_start:$mds}'
    } > "$tmp" 2>/dev/null && mv "$tmp" "$COST_LOG"
}

cmd_update() {
    local input session_id duration_ms transcript_path
    input=$(cat)
    # One jq pass for every field we need — extra subprocesses lengthen the
    # locked critical section under heavy parallelism (many Claude Code windows
    # refreshing at once), which is exactly when lock waiters start timing out.
    IFS=$'\t' read -r session_id duration_ms transcript_path < <(
        printf '%s' "$input" | jq -r \
            '[.session_id // "", .cost.total_duration_ms // 0, .transcript_path // ""] | @tsv' \
            2>/dev/null)
    [[ -z "$session_id" ]] && return 0

    local session_cost; session_cost=$(_session_cost "$transcript_path")
    [[ -z "$session_cost" || "$session_cost" == "0.000000" ]] && return 0

    local today month cutoff
    today="${STATUSLINE_TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"
    month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"
    cutoff=$(_days_ago "$COST_LOG_RETENTION_DAYS")

    mkdir -p "$(dirname "$COST_LOG")" 2>/dev/null
    _cleanup_tmps_maybe

    # Serialise the read→compute→write with a lock so concurrent Claude Code
    # windows can't double-count deltas. No lock within 5s: skip silently, the
    # next refresh catches up.
    _acquire_lock || return 0
    local tmp="${COST_LOG}.tmp.$$"
    trap '_release_lock; rm -f "$tmp" 2>/dev/null' EXIT

    local prior needs_write=false day_start="0.000000" month_day_start="0.000000"
    prior=$(grep -F "\"session_id\":\"${session_id}\"" "$COST_LOG" 2>/dev/null | tail -1)
    if [[ -z "$prior" ]] || ! printf '%s' "$prior" | jq -e 'has("cost_at_day_start")' &>/dev/null; then
        _baseline_new_session "$duration_ms" "$session_cost"
    else
        _baseline_from_prior "$prior" "$session_cost" "$today" "$month"
    fi
    $needs_write && _write_ledger_row

    _release_lock
    rm -f "$tmp" 2>/dev/null
    trap - EXIT
    return 0
}

# ── today ────────────────────────────────────────────────────────────────────

cmd_today() {
    if [[ ! -s "$COST_LOG" ]]; then echo "0"; return 0; fi
    local today; today="${STATUSLINE_TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"
    jq -s --arg d "$today" \
        '[.[] | select(.date == $d) | ((.cost_usd - (.cost_at_day_start // 0)) | if . < 0 then 0 else . end)] | add // 0' \
        "$COST_LOG" 2>/dev/null || echo "0"
}

# ── month ────────────────────────────────────────────────────────────────────
# Outputs "<total> <avg_per_weekday>"

cmd_month() {
    if [[ ! -s "$COST_LOG" ]]; then echo "0 0"; return 0; fi
    local month total wd avg
    month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"
    # Use month_day_start (new schema) if present; fall back to month_cost (old schema).
    total=$(jq -s --arg m "$month" '
        [.[] | select(.month == $m) |
            (if has("month_day_start")
             then (.cost_usd - (.month_day_start // 0))
             else (.month_cost // 0)
             end) | if . < 0 then 0 else . end
        ] | add // 0' \
        "$COST_LOG" 2>/dev/null) || total="0"
    [[ -z "$total" || "$total" == "null" ]] && total="0"
    wd=$(_weekdays_elapsed)
    avg=$(awk -v t="$total" -v w="$wd" 'BEGIN{printf "%.6f", (w>0)?t/w:0}')
    echo "$total $avg"
}


# ── dispatch ─────────────────────────────────────────────────────────────────

usage() {
    cat <<'EOF'
costs.sh — claude-statusline cost ledger CLI

Usage:
  costs.sh update              # stdin = Claude Code JSON; upsert ledger row
  costs.sh today               # echo $X.XX
  costs.sh month               # echo "$X.XX $Y.YY"  (total + avg/weekday)
  costs.sh info                # human-friendly summary
  costs.sh debug               # full diagnostics + ccusage cross-check
  costs.sh init                # seed from ccusage (one-time, backs up existing log)

Schema: {session_id, date, cost_at_day_start, cost_usd, month, month_day_start}
  month total = sum(cost_usd - month_day_start) — derived, never accumulated

Env:
  STATUSLINE_STATE_DIR         # where every runtime file goes (default: ~/.claude/statusline/state)
  STATUSLINE_COST_LOG          # override ledger path (default: $STATUSLINE_STATE_DIR/costs.jsonl);
                               # the per-session caches follow it into <dir of ledger>/sessions/
  COST_LOG_RETENTION_DAYS_OVERRIDE   # override retention window (default: 30)
EOF
}

case "${1:-}" in
    update) shift; cmd_update "$@" ;;
    today)  shift; cmd_today  "$@" ;;
    month)  shift; cmd_month  "$@" ;;
    info)   shift; cmd_info   "$@" ;;
    debug)  shift; cmd_debug  "$@" ;;
    init)   shift; cmd_init   "$@" ;;
    __price_sample) shift; cmd_price_sample "$@" ;;
    ""|help|-h|--help) usage; [[ -z "${1:-}" ]] && exit 1 || exit 0 ;;
    *) echo "unknown verb: $1" >&2; usage >&2; exit 1 ;;
esac
