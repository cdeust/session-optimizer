#!/usr/bin/env bash
# claude-statusline cost ledger CLI.
#
# Owns ~/.claude/statusline-costs.jsonl. The render path (statusline.sh) shells
# out to this script for every refresh: `update` writes a row, `today` / `month`
# return current totals.
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

set -u

COST_LOG="${STATUSLINE_COST_LOG:-${HOME}/.claude/statusline-costs.jsonl}"
COST_LOG_RETENTION_DAYS="${COST_LOG_RETENTION_DAYS_OVERRIDE:-30}"

# ── Shared pricing (single source of truth: pricing.json next to this script) ──
_PRICING="${STATUSLINE_PRICING_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pricing.json}"
[[ -f "$_PRICING" ]] || _PRICING="$HOME/.claude/pricing.json"
if [[ ! -f "$_PRICING" ]]; then
  echo "costs.sh: pricing.json not found (looked next to script and in ~/.claude)" >&2; exit 1
fi
_today=$(date +%Y-%m-%d)
_MODELS=$(jq -c --arg today "$_today" '
  .sonnet5_intro_until as $cut
  | .models
  | map( if .intro then (. + (if $today < $cut then .intro else .list end) | del(.intro,.list)) else . end )
  | map({match, ind, out, cw5, cw1, cr})' "$_PRICING")

# Price a stream of Claude Code transcript lines (passed on stdin) from token
# usage. Standard-tier $/Mtok by model family, resolved from the injected
# $models array (see pricing.json); cache writes split 5m/1h when the
# breakdown is present, with any unattributed cache-creation priced at the 5m rate.
# shellcheck disable=SC2016  # jq program: $models/$ml are jq variables, not shell.
_COST_JQ='
def rates(m):
  (m // "" | ascii_downcase) as $ml
  | ($models | map(select(.match as $rx | $rx == "" or ($ml | test($rx)))) | first);
# Dedupe by message id BEFORE summing. Claude Code writes each API response to the
# transcript multiple times (streaming / tool-continuation re-logging), so summing
# every assistant line over-counts a response 2-3x. Key on message.id:requestId
# (matching ccusage); a line without message.id falls back to its stream position
# so id-less lines are never merged together.
[ inputs
  | (fromjson? // empty)
  | objects
  | select(.type=="assistant" and (.message.usage != null)) ]
| to_entries
| map( .key as $i | .value as $v
     | { dk: (if ($v.message.id) then ($v.message.id + ":" + ($v.requestId // "")) else "line-\($i)" end),
         m: $v.message.model, u: $v.message.usage } )
| unique_by(.dk)
| [ .[]
  | .u as $u | rates(.m) as $r
  | ( ($u.input_tokens // 0)  * $r.ind
    + ($u.output_tokens // 0) * $r.out
    + (($u.cache_creation.ephemeral_5m_input_tokens // 0) * $r.cw5)
    + (($u.cache_creation.ephemeral_1h_input_tokens // 0) * $r.cw1)
    + ( ( [ ($u.cache_creation_input_tokens // 0)
            - (($u.cache_creation.ephemeral_5m_input_tokens // 0)
              +($u.cache_creation.ephemeral_1h_input_tokens // 0)), 0 ] | max ) * $r.cw5 )
    + ($u.cache_read_input_tokens // 0) * $r.cr
    ) / 1000000
] | add // 0
'

# Test seam: price EACH line of a transcript-shaped file independently (no
# .type=="assistant" filter, no message.id dedupe), using the same rates(m)
# lookup and the same per-line cost formula as _COST_JQ above. Used by the
# pricing-parity test to prove pricing.json produces byte-identical numbers
# to the pre-refactor hardcoded rates, one cost per input line.
# shellcheck disable=SC2016  # jq program: $models/$ml are jq variables, not shell.
_SAMPLE_JQ='
def rates(m):
  (m // "" | ascii_downcase) as $ml
  | ($models | map(select(.match as $rx | $rx == "" or ($ml | test($rx)))) | first);
[ inputs | (fromjson? // empty) | objects ]
| .[]
| .message.model as $m | .message.usage as $u | rates($m) as $r
| ( ($u.input_tokens // 0)  * $r.ind
  + ($u.output_tokens // 0) * $r.out
  + (($u.cache_creation.ephemeral_5m_input_tokens // 0) * $r.cw5)
  + (($u.cache_creation.ephemeral_1h_input_tokens // 0) * $r.cw1)
  + ( ( [ ($u.cache_creation_input_tokens // 0)
          - (($u.cache_creation.ephemeral_5m_input_tokens // 0)
            +($u.cache_creation.ephemeral_1h_input_tokens // 0)), 0 ] | max ) * $r.cw5 )
  + ($u.cache_read_input_tokens // 0) * $r.cr
  ) / 1000000
'

cmd_price_sample() {
    local file="$1"
    [[ -n "$file" && -f "$file" ]] || { echo "usage: costs.sh __price_sample <file>" >&2; return 2; }
    jq -nR --argjson models "$_MODELS" "$_SAMPLE_JQ" < "$file" 2>/dev/null
}

# Generic memoized pricing: re-price only when the watched mtime changes (and
# not more than once per throttle window), caching "<newest_mtime> <cost>
# <priced_at>" in $1. Shared by _main_cost and _subagent_cost below.
#   * mtime key  — transcripts are append-only, so if the newest mtime is
#                  unchanged the priced cost is still exact; skip the parse.
#   * throttle   — even when it IS changing, re-price at most once per
#                  $3 seconds. Bounds a busy burst to one parse per window;
#                  the displayed cost simply lags up to that delta.
# Steady state is one stat (~2ms) instead of re-reading a tens-of-MB transcript.
# ponytail: mtime granularity is whole seconds; the throttle makes sub-second
# precision irrelevant anyway.
_price_cached() {
    local cache="$1" newest="$2" throttle="$3"; shift 3
    local price_fn="$1"; shift
    [[ -n "$newest" ]] || { echo 0; return; }

    local now; now=$(date +%s)
    if [[ -s "$cache" ]]; then
        local cached_ts cached_cost cached_at
        read -r cached_ts cached_cost cached_at < "$cache"
        # Reuse if unchanged OR we re-priced within the throttle window.
        if [[ "$cached_ts" == "$newest" ]] || (( now - ${cached_at:-0} < throttle )); then
            printf '%s' "$cached_cost"; return
        fi
    fi

    local cost; cost=$("$price_fn" "$@")
    cost=$(awk -v c="${cost:-0}" 'BEGIN{printf "%.6f", c+0}')
    printf '%s %s %s\n' "$newest" "$cost" "$now" > "$cache" 2>/dev/null
    printf '%s' "$cost"
}

_do_price_file() {
    jq -nR --argjson models "$_MODELS" "$_COST_JQ" < "$1" 2>/dev/null
}

_do_price_dir() {
    find "$1" -type f -name '*.jsonl' -exec cat {} + 2>/dev/null \
        | jq -nR --argjson models "$_MODELS" "$_COST_JQ" 2>/dev/null
}

# Price the session's OWN spend from its main transcript file. This is the
# authoritative source for the main conversation's cost (see header note on
# why cost.total_cost_usd is not used: it double-counts on session resume).
# Arg: path to the main session transcript (.../<session_id>.jsonl).
_main_cost() {
    local transcript="$1"
    [[ -n "$transcript" && -f "$transcript" ]] || { echo 0; return; }
    local mtime; mtime=$(stat -f '%m' "$transcript" 2>/dev/null || stat -c '%Y' "$transcript" 2>/dev/null)
    local cache; cache="${COST_LOG}.main.$(basename "${transcript%.jsonl}")"
    _price_cached "$cache" "$mtime" "${STATUSLINE_MAIN_REFRESH_SEC:-60}" _do_price_file "$transcript"
}

# Sum subagent/agent-team spend for a session from its transcript siblings.
# Arg: path to the main session transcript (.../<session_id>.jsonl).
# Echoes a dollar figure (0 if the subagents dir is absent or empty).
_subagent_cost() {
    local transcript="$1"
    [[ -n "$transcript" ]] || { echo 0; return; }
    local subdir="${transcript%.jsonl}/subagents"
    [[ -d "$subdir" ]] || { echo 0; return; }

    # Newest transcript mtime across the WHOLE subtree = change key. Must recurse to
    # match the recursive pricing below: agents can spawn agents, so a nested
    # transcript being written has to invalidate the cache too.
    local newest
    newest=$(find "$subdir" -type f -name '*.jsonl' -print0 2>/dev/null \
        | while IFS= read -r -d '' f; do
              stat -f '%m' "$f" 2>/dev/null || stat -c '%Y' "$f" 2>/dev/null
          done | sort -rn | head -1)
    [[ -n "$newest" ]] || { echo 0; return; }   # dir present but empty

    local cache; cache="${COST_LOG}.sub.$(basename "${transcript%.jsonl}")"
    _price_cached "$cache" "$newest" "${STATUSLINE_SUBAGENT_REFRESH_SEC:-60}" _do_price_dir "$subdir"
}

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
        done < <(find "$dir" -maxdepth 1 \( -name "${base}.sub.*" -o -name "${base}.main.*" \) 2>/dev/null)
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

    # Price from the session's own transcript (see header note: total_cost_usd
    # is cumulative across a resume and would double-count).
    local main_cost; main_cost=$(_main_cost "$transcript_path")
    local sub_cost=0
    if [[ "${STATUSLINE_COUNT_SUBAGENTS:-1}" != "0" && -n "$transcript_path" ]]; then
        sub_cost=$(_subagent_cost "$transcript_path")
    fi
    local cost_usd; cost_usd=$(awk -v a="$main_cost" -v b="$sub_cost" 'BEGIN{printf "%.6f", a+b}')
    local session_cost; session_cost=$(printf '%s' "$cost_usd" | awk '{printf "%.6f", $0+0}')
    [[ -z "$session_cost" || "$session_cost" == "0.000000" ]] && return 0

    local today month cutoff
    today="${STATUSLINE_TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"
    month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"
    cutoff=$(_days_ago "$COST_LOG_RETENTION_DAYS")

    mkdir -p "$(dirname "$COST_LOG")" 2>/dev/null
    _cleanup_tmps_maybe

    # Serialise the read→compute→write with a lock so concurrent Claude Code
    # windows can't double-count deltas.
    if ! _acquire_lock; then
        return 0  # couldn't acquire within 5s — skip silently, next refresh will catch up
    fi
    local tmp="${COST_LOG}.tmp.$$"
    trap '_release_lock; rm -f "$tmp" 2>/dev/null' EXIT

    local prior
    prior=$(grep -F "\"session_id\":\"${session_id}\"" "$COST_LOG" 2>/dev/null | tail -1)

    local needs_write=false day_start="0.000000" month_day_start="0.000000"

    if [[ -z "$prior" ]] || ! printf '%s' "$prior" | jq -e 'has("cost_at_day_start")' &>/dev/null; then
        # New session OR old-schema row: detect whether session started today.
        local now_ts midnight_ts elapsed_today session_age_sec
        now_ts=$(date +%s); midnight_ts=$(_midnight_ts)
        elapsed_today=$(( now_ts - midnight_ts ))
        session_age_sec=$(( duration_ms / 1000 ))
        needs_write=true
        if [[ $session_age_sec -le $elapsed_today ]]; then
            # Started today — baseline is 0.
            day_start="0.000000"; month_day_start="0.000000"
        else
            # Started before today (or before this month) — treat its entire
            # cost as historical; only new spend from this point counts.
            day_start="$session_cost"; month_day_start="$session_cost"
        fi
    else
        local prior_cost prior_date prior_day_start prior_month prior_mds
        prior_cost=$(printf '%s' "$prior" | jq -r '.cost_usd // 0' | awk '{printf "%.6f", $0}')
        prior_date=$(printf '%s' "$prior" | jq -r '.date // ""')
        prior_day_start=$(printf '%s' "$prior" | jq -r '.cost_at_day_start // 0' | awk '{printf "%.6f", $0}')
        prior_month=$(printf '%s' "$prior" | jq -r '.month // ""')
        # Support both new schema (month_day_start) and old schema (month_cost).
        # For old rows: approximate month_day_start = max(0, cost_usd - month_cost).
        if printf '%s' "$prior" | jq -e 'has("month_day_start")' &>/dev/null; then
            prior_mds=$(printf '%s' "$prior" | jq -r '.month_day_start // 0' | awk '{printf "%.6f", $0}')
        else
            local old_mc
            old_mc=$(printf '%s' "$prior" | jq -r '.month_cost // 0' | awk '{printf "%.6f", $0}')
            prior_mds=$(awk "BEGIN{v=$prior_cost - $old_mc; printf \"%.6f\", (v<0)?0:v}")
        fi

        if [[ "$prior_date" != "$today" ]]; then
            needs_write=true
            day_start="$prior_cost"
            if [[ "$prior_month" != "$month" ]]; then
                # Month rollover: this session carries over; new month starts from prior cost.
                month_day_start="$prior_cost"
            else
                # Day rollover within same month: month baseline unchanged.
                month_day_start="$prior_mds"
            fi
        elif [[ "$prior_cost" != "$session_cost" ]]; then
            needs_write=true
            if awk "BEGIN{exit !($session_cost < $prior_cost)}"; then
                # Cost dropped → account switch or SDK reset. Rebaseline the day
                # baseline so today's delta can't go negative, but PRESERVE this
                # session's month-to-date contribution so far (prior_cost - prior_mds):
                # carry it into the new baseline so month delta (cost_usd - month_day_start)
                # still equals it at the switch point. month_day_start may go negative,
                # which is fine — it's a pure offset, and month sums clamp at 0.
                day_start="$session_cost"
                month_day_start=$(awk "BEGIN{printf \"%.6f\", $session_cost - ($prior_cost - $prior_mds)}")
            else
                day_start="$prior_day_start"
                month_day_start="$prior_mds"
            fi
        else
            day_start="$prior_day_start"; month_day_start="$prior_mds"
        fi
    fi

    if $needs_write; then
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
    fi

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

# ── info ─────────────────────────────────────────────────────────────────────

cmd_info() {
    local today_total month_pair month_total month_avg
    today_total=$(cmd_today)
    month_pair=$(cmd_month)
    month_total=${month_pair%% *}
    month_avg=${month_pair##* }

    if [[ ! -s "$COST_LOG" ]]; then
        echo "Ledger: $COST_LOG (empty)"
        echo "Run \`$0 init\` to seed from ccusage, or wait for the first Claude Code session to populate it."
        return 0
    fi

    printf 'Today  (%s):       $%.2f\n' "$(date +%Y-%m-%d)" "$today_total"
    printf 'Month  (%s):           $%.2f\n' "$(date +%Y-%m)" "$month_total"
    printf 'Avg/weekday this month:    $%.2f\n' "$month_avg"
    echo "Ledger: $COST_LOG"
}

# ── debug ────────────────────────────────────────────────────────────────────

cmd_debug() {
    local today; today="${STATUSLINE_TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"
    local month; month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"

    echo "claude-statusline costs debug"
    echo "  cost log:  $COST_LOG"
    echo "  retention: ${COST_LOG_RETENTION_DAYS} days"

    if [[ -s "$COST_LOG" ]]; then
        local lines size oldest newest
        lines=$(wc -l < "$COST_LOG" | tr -d ' ')
        size=$(wc -c < "$COST_LOG" | tr -d ' ')
        oldest=$(jq -rs '[.[].date] | min // "(none)"' "$COST_LOG" 2>/dev/null)
        newest=$(jq -rs '[.[].date] | max // "(none)"' "$COST_LOG" 2>/dev/null)
        echo "  entries:   ${lines} rows (${size} bytes)"
        echo "  range:     ${oldest} → ${newest}"
    else
        echo "  entries:   (no entries yet)"
    fi

    echo
    echo "Today (${today}):"
    if [[ -s "$COST_LOG" ]]; then
        jq -r --arg d "$today" '
            select(.date == $d)
            | "  \(.session_id)  start=\(.cost_at_day_start)  cur=\(.cost_usd)  delta=\((.cost_usd - (.cost_at_day_start // 0)))"
        ' "$COST_LOG" 2>/dev/null
        printf '  total: $%.2f\n' "$(cmd_today)"
    else
        echo "  (no entries yet)"
    fi

    echo
    echo "Last ${COST_LOG_RETENTION_DAYS} days (per-day totals):"
    if [[ -s "$COST_LOG" ]]; then
        local cutoff; cutoff=$(_days_ago "$COST_LOG_RETENTION_DAYS")
        jq -rs --arg c "$cutoff" '
            [.[] | select(.date >= $c)]
            | group_by(.date)
            | map({date: .[0].date,
                   total: ([.[] | ((.cost_usd - (.cost_at_day_start // 0)) | if . < 0 then 0 else . end)] | add // 0)})
            | sort_by(.date)
            | .[]
            | "  \(.date)  $\(.total | . * 100 | round / 100)"
        ' "$COST_LOG" 2>/dev/null
    else
        echo "  (no entries yet)"
    fi

    echo
    echo "Month-to-date (${month}):"
    local mp; mp=$(cmd_month)
    printf '  total: $%.2f   avg/weekday: $%.2f\n' "${mp%% *}" "${mp##* }"
    echo "  (computed as sum(cost_usd - month_day_start) — no accumulation drift possible)"

    echo
    echo "ccusage cross-check:"
    if command -v ccusage >/dev/null 2>&1; then
        local cc_today cc_month
        cc_today=$(NODE_USE_ENV_PROXY=1 ccusage daily --json --since "$(date +%Y%m%d)" --until "$(date +%Y%m%d)" 2>/dev/null \
            | jq -r '.totals.totalCost // empty' 2>/dev/null)
        cc_month=$(NODE_USE_ENV_PROXY=1 ccusage monthly --json --since "$(date +%Y%m01)" --until "$(date +%Y%m%d)" 2>/dev/null \
            | jq -r '.totals.totalCost // empty' 2>/dev/null)
        printf '  ccusage today: $%.2f\n' "${cc_today:-0}"
        printf '  ccusage month: $%.2f\n' "${cc_month:-0}"
        if [[ -z "${cc_today:-}" || "${cc_today:-0}" == "0" ]]; then
            echo "  (ccusage returned 0 — if you're behind a corporate proxy, set NODE_USE_ENV_PROXY=1)"
        fi
    else
        echo "  (ccusage not on PATH — install via brew install ccusage or npm i -g ccusage)"
    fi

    echo
    local dir base orphans
    dir=$(dirname "$COST_LOG"); base=$(basename "$COST_LOG")
    orphans=$(find "$dir" -maxdepth 1 -name "${base}.tmp.*" 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$orphans" -gt 0 ]]; then
        echo "Orphan tmp files: ${orphans} found in ${dir} (run: rm \"${COST_LOG}.tmp.\"*)"
    else
        echo "Orphan tmp files: none"
    fi
    return 0
}

# ── init ─────────────────────────────────────────────────────────────────────
# Seeds the ledger from ccusage. Backs up any existing log.
# Writes one row carrying month-to-date minus today's spend so today's live
# accumulator can grow on top.

cmd_init() {
    if ! command -v ccusage >/dev/null 2>&1; then
        echo "ccusage not on PATH — install via brew install ccusage or npm i -g ccusage" >&2
        return 2
    fi
    if ! command -v jq >/dev/null 2>&1; then
        echo "jq not on PATH" >&2
        return 2
    fi

    local month since today_y m_total t_total carry
    month=$(date +%Y-%m); since=$(date +%Y%m01); today_y=$(date +%Y%m%d)
    m_total=$(NODE_USE_ENV_PROXY=1 ccusage monthly --json --since "$since" --until "$today_y" 2>/dev/null \
        | jq -r '.totals.totalCost // 0')
    t_total=$(NODE_USE_ENV_PROXY=1 ccusage daily --json --since "$today_y" --until "$today_y" 2>/dev/null \
        | jq -r '.totals.totalCost // 0')
    carry=$(awk "BEGIN{printf \"%.6f\", $m_total - $t_total}")

    mkdir -p "$(dirname "$COST_LOG")"
    if [[ -s "$COST_LOG" ]]; then
        local bak; bak="${COST_LOG}.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$COST_LOG" "$bak" && echo "Backup: $bak"
    fi

    # Seed row: cost_usd=carry, month_day_start=0  →  month total = carry - 0 = carry.
    jq -cn --arg m "$month" --argjson carry "$carry" \
        '{session_id:"__ccusage_seed__",date:($m+"-01"),cost_at_day_start:0,cost_usd:$carry,month:$m,month_day_start:0}' \
        > "$COST_LOG"

    printf 'Seeded month carryover: $%.2f (from ccusage month $%.2f minus today $%.2f).\n' \
        "$carry" "$m_total" "$t_total"
    return 0
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
  STATUSLINE_COST_LOG          # override ledger path (default: ~/.claude/statusline-costs.jsonl)
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
