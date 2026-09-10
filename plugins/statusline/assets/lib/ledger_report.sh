# shellcheck shell=bash
# lib/ledger_report.sh: the reporting and seeding verbs of the cost ledger.
#
# Single responsibility: what a human reads about the ledger (info, debug) and
# the one-time ccusage seed (init). Nothing here is on the renderer's refresh
# path; costs.sh keeps update/today/month, which are. Sourced by costs.sh; not
# a standalone script.
#
# Depends on: COST_LOG, COST_CACHE_DIR, COST_LOG_RETENTION_DAYS, cmd_today,
#             cmd_month, _days_ago (all defined by costs.sh).

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
# One section per helper; cmd_debug only sequences them.

_debug_ledger_summary() {
    echo "claude-statusline costs debug"
    echo "  cost log:  $COST_LOG"
    echo "  caches:    $COST_CACHE_DIR"
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
}

_debug_today() {
    local today="$1"
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
}

_debug_history() {
    local month="$1"
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
    echo "  (computed as sum(cost_usd - month_day_start): no accumulation drift possible)"
}

_debug_ccusage() {
    echo
    echo "ccusage cross-check:"
    if ! command -v ccusage >/dev/null 2>&1; then
        echo "  (ccusage not on PATH: install via brew install ccusage or npm i -g ccusage)"
        return 0
    fi
    local cc_today cc_month
    cc_today=$(NODE_USE_ENV_PROXY=1 ccusage daily --json --since "$(date +%Y%m%d)" --until "$(date +%Y%m%d)" 2>/dev/null \
        | jq -r '.totals.totalCost // empty' 2>/dev/null)
    cc_month=$(NODE_USE_ENV_PROXY=1 ccusage monthly --json --since "$(date +%Y%m01)" --until "$(date +%Y%m%d)" 2>/dev/null \
        | jq -r '.totals.totalCost // empty' 2>/dev/null)
    printf '  ccusage today: $%.2f\n' "${cc_today:-0}"
    printf '  ccusage month: $%.2f\n' "${cc_month:-0}"
    if [[ -z "${cc_today:-}" || "${cc_today:-0}" == "0" ]]; then
        echo "  (ccusage returned 0: if you're behind a corporate proxy, set NODE_USE_ENV_PROXY=1)"
    fi
}

_debug_files() {
    echo
    local dir base orphans caches
    dir=$(dirname "$COST_LOG"); base=$(basename "$COST_LOG")
    orphans=$(find "$dir" -maxdepth 1 -name "${base}.tmp.*" 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$orphans" -gt 0 ]]; then
        echo "Orphan tmp files: ${orphans} found in ${dir} (run: rm \"${COST_LOG}.tmp.\"*)"
    else
        echo "Orphan tmp files: none"
    fi
    caches=$(find "$COST_CACHE_DIR" -maxdepth 1 -type f \( -name '*.sub' -o -name '*.main' \) 2>/dev/null | wc -l | tr -d ' ')
    echo "Per-session price caches: ${caches} in ${COST_CACHE_DIR} (expire after ${COST_LOG_RETENTION_DAYS} days)"
}

cmd_debug() {
    local today month
    today="${STATUSLINE_TODAY_OVERRIDE:-$(date +%Y-%m-%d)}"
    month="${STATUSLINE_MONTH_OVERRIDE:-$(date +%Y-%m)}"
    _debug_ledger_summary
    _debug_today "$today"
    _debug_history "$month"
    _debug_ccusage
    _debug_files
    return 0
}

# ── init ─────────────────────────────────────────────────────────────────────
# Seeds the ledger from ccusage. Backs up any existing log.
# Writes one row carrying month-to-date minus today's spend so today's live
# accumulator can grow on top.

cmd_init() {
    if ! command -v ccusage >/dev/null 2>&1; then
        echo "ccusage not on PATH: install via brew install ccusage or npm i -g ccusage" >&2
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
        # Backups go under the ledger's backup/ directory, never beside it:
        # a *.bak.* file at the ledger's level is exactly the clutter #33 removed.
        local bak_dir bak; bak_dir="$(dirname "$COST_LOG")/backup"
        mkdir -p "$bak_dir"
        bak="${bak_dir}/$(basename "$COST_LOG").$(date +%Y%m%d-%H%M%S)"
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
