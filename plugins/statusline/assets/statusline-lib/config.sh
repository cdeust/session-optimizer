# shellcheck shell=bash
# statusline-lib/config.sh — the two JSON files this renderer reads.
#
# Single responsibility: knowing the on-disk config schema. Changes when a
# config file's shape changes. Each reader validates what it read and falls back
# to a documented default, so a missing, unreadable or malformed config degrades
# to the built-in behaviour rather than rendering a wrong number.
#
# Depends on: nothing (jq).

# Single source of truth for the checkpoint thresholds, shared with the
# stop-context-guard.py hook so passive display and active enforcement cannot
# drift.
CTXGUARD_CONFIG="${HOME}/.claude/ctxguard-thresholds.json"
BUDGET_CONFIG="${HOME}/.claude/statusline-budget.json"
# The host's own settings file. Read for exactly one field — statusLine.padding
# — because that padding is applied by the host AROUND this script's output and
# therefore comes out of the width available to it (see fit.sh, fit_budget).
SETTINGS_CONFIG="${HOME}/.claude/settings.json"

# resolve_ctx_thresholds — context-window thresholds for the model named $1.
# pre:  $1 the model's display name (any case).
# post: sets WARN_TOKENS and SAVE_TOKENS to positive integers, always. Returns 0.
#       WARN = checkpoint threshold (save now); SAVE = soft cap (save + recall,
#       fresh session).
# The case block is the embedded fallback and MUST mirror the hook's
# FALLBACK_THRESHOLDS table:
#   Fable 5 / Mythos : warn 120K, save 160K  (2x rent + 2x cache-expiry penalty)
#   Opus 4.x         : warn 180K, save 200K  (cost discipline; window is 1M)
#   Sonnet 4.6       : warn 180K, save 200K  (cost discipline; window is 1M)
#   Haiku 4.5        : warn 120K, save 170K  (200K IS the window; keep headroom)
resolve_ctx_thresholds() {
  local model_lc
  model_lc=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')

  WARN_TOKENS=""
  SAVE_TOKENS=""
  if [ -r "$CTXGUARD_CONFIG" ]; then
    # First entry whose .match is a substring of the lowercased display name;
    # falls back to .default. Emits "warn save" or nothing on any jq failure.
    read -r WARN_TOKENS SAVE_TOKENS < <(
      jq -r --arg m "$model_lc" '
        ( [ .models[]? | select(.match != null)
            | select(.match as $p | $m | contains($p)) ][0]
          // .default // empty )
        | select(.warn != null and .hard != null)
        | "\(.warn) \(.hard)"
      ' "$CTXGUARD_CONFIG" 2>/dev/null
    ) || true
  fi
  case "$WARN_TOKENS" in ''|*[!0-9]*) WARN_TOKENS="" ;; esac
  case "$SAVE_TOKENS" in ''|*[!0-9]*) SAVE_TOKENS="" ;; esac

  if [ -z "$WARN_TOKENS" ] || [ -z "$SAVE_TOKENS" ]; then
    case "$model_lc" in
      *fable*|*mythos*) WARN_TOKENS=120000; SAVE_TOKENS=160000 ;;
      *haiku*)          WARN_TOKENS=120000; SAVE_TOKENS=170000 ;;
      *)                WARN_TOKENS=180000; SAVE_TOKENS=200000 ;;
    esac
  fi
}

# read_cache_ttl_min — the prompt-cache lifetime in minutes.
# post: prints 5 (Pro default) or the configured value. Source for the default:
#       docs.anthropic.com/.../prompt-caching — 5-minute default TTL; 60 on Max.
read_cache_ttl_min() {
  local b_ttl=""
  if [ -r "$BUDGET_CONFIG" ]; then
    read -r b_ttl < <(jq -r '"\(.cache_ttl_min // 5)"' "$BUDGET_CONFIG" 2>/dev/null) || true
  fi
  case "$b_ttl" in ''|*[!0-9]*) printf '5' ;; *) printf '%s' "$b_ttl" ;; esac
}

# read_statusline_padding — the host's configured statusLine.padding.
# post: prints a non-negative integer, always. 0 when the setting is absent,
#       unreadable, negative or non-numeric — which is also the host's own
#       default, so an unreadable settings file degrades to the host default
#       rather than to a guess.
# source: code.claude.com/docs/en/statusline — "The optional `padding` field
#         adds extra horizontal spacing (in characters) to the status line
#         content. Defaults to `0`."
# Only the user-level file is read. A project or local settings file can also
# carry statusLine, and the host merges them; this reader does not, so a
# project-level padding is not seen. The cost of that miss is bounded: the
# budget is off by twice the difference, and fit_line trims a segment early or
# late by that much. Merging the full settings precedence would put the host's
# resolution rules in this renderer, which is a larger contract than the width
# budget needs.
# The type test is done in jq, not on the printed text: the setting is a JSON
# number for the host, and a JSON string "2" is NOT one — the host would hand it
# to a layout engine that expects a number. Filtering on the shell side alone
# would accept "2" and charge two columns the host never indents.
read_statusline_padding() {
  local p=""
  [ -r "$SETTINGS_CONFIG" ] && {
    p=$(jq -r '
      .statusLine.padding as $p
      | if ($p | type) == "number" and $p >= 0 and ($p | floor) == $p
        then ($p | tostring) else "0" end
    ' "$SETTINGS_CONFIG" 2>/dev/null) || p=""
  }
  case "$p" in ''|*[!0-9]*) printf '0' ;; *) printf '%s' "$p" ;; esac
}

# read_configured_size — the verbosity preset the config asks for.
# post: prints one of xs|s|m|l|xl, or nothing when the config is absent or does
#       not name a valid preset. It is a PREFERENCE: layout.sh caps it by width.
read_configured_size() {
  local s=""
  [ -r "$BUDGET_CONFIG" ] && { s=$(jq -r '.size // empty' "$BUDGET_CONFIG" 2>/dev/null) || s=""; }
  case "$s" in xs|s|m|l|xl) printf '%s' "$s" ;; esac
}
