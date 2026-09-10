# shellcheck shell=bash
# shellcheck disable=SC2034  # _COST_JQ / _SAMPLE_JQ / _MODELS are read by the
# functions below and by costs.sh; shellcheck analyses one file at a time.
# lib/pricing.sh: the pricing engine of the cost ledger.
#
# Single responsibility: turning transcript lines into dollars. Loads
# pricing.json, holds the two jq programs (deduplicated session pricing and the
# per-line parity seam), and the memoised per-transcript pricers with their
# on-disk caches under $COST_CACHE_DIR. Changes when the price table's shape or
# the pricing formula changes. Sourced by costs.sh; not a standalone script.
#
# Depends on: platform.sh (file_mtime);
#             _COSTS_HOME, STATUSLINE_DIR, COST_CACHE_DIR (set by costs.sh);
#             STATUSLINE_PRICING_FILE, STATUSLINE_MAIN_REFRESH_SEC and
#             STATUSLINE_SUBAGENT_REFRESH_SEC from the environment.

# ── Shared pricing (single source of truth: pricing.json beside costs.sh) ──
_PRICING="${STATUSLINE_PRICING_FILE:-${_COSTS_HOME}/pricing.json}"
[[ -f "$_PRICING" ]] || _PRICING="${STATUSLINE_DIR}/pricing.json"
if [[ ! -f "$_PRICING" ]]; then
  echo "costs.sh: pricing.json not found (looked next to script and in ${STATUSLINE_DIR})" >&2; exit 1
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
#   * mtime key: transcripts are append-only, so if the newest mtime is
#                  unchanged the priced cost is still exact; skip the parse.
#   * throttle : even when it IS changing, re-price at most once per
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
    mkdir -p "$(dirname "$cache")" 2>/dev/null
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
    local mtime; mtime=$(file_mtime "$transcript")
    local cache; cache="${COST_CACHE_DIR}/$(basename "${transcript%.jsonl}").main"
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
        | while IFS= read -r -d '' f; do file_mtime "$f"; echo; done | sort -rn | head -1)
    [[ -n "$newest" ]] || { echo 0; return; }   # dir present but empty

    local cache; cache="${COST_CACHE_DIR}/$(basename "${transcript%.jsonl}").sub"
    _price_cached "$cache" "$newest" "${STATUSLINE_SUBAGENT_REFRESH_SEC:-60}" _do_price_dir "$subdir"
}
