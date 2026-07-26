# shellcheck shell=bash
# shellcheck disable=SC2034  # This module's constants and outputs are consumed by
# sibling modules and by the composition root that sources them all; shellcheck
# analyses one file at a time and cannot follow that boundary. What actually
# proves each name is live is tests/statusline (which sources the whole set and
# asserts the exports exist) and the golden render diff, not this warning.
# statusline-lib/palette.sh — colour tokens and the heat-track bar.
#
# Single responsibility: what things look like. This module changes when the
# design system changes, and for no other reason — it knows nothing about
# quotas, tokens, terminals or git. Everything here is pure: no I/O, no reads of
# any global defined elsewhere.
#
# Depends on: nothing.
#
# --- Colors: AI Architect DS — ink (instrument) surface palette (no DIM) ---
# source: AI Architect Design System tokens/colors.css (:root ink-surface
# primitives), each oklch value converted with CSS Color 4 math (scripted
# oklch->srgb). DS gate G3/G4 (SKILL.md): chrome is GREYSCALE — colour comes
# only from data tokens (heat/stage/valence/tool families) or the single
# terracotta accent, never both, never decoratively. None of this statusline's
# segments (model/dir/worktree/subagent-count/throughput/compaction-count) are
# DS "data families" — they are chrome — so they render in the neutral fg
# scale (TEXT/SUBTEXT/OVERLAY), not in stage/valence hues. Status colours
# (ok/warn/danger) are reserved for actual threshold-driven state. Terracotta
# is used in exactly one place (the effort badge — the one user-selected,
# accent-worthy dial) per G4 ("accent = selection only, never a category").
# Fixes a prior 1:1 Catppuccin-Mocha->token remap (PR #2) that kept 12
# competing hues (rainbow chrome) instead of DS's neutral-chrome-plus-one-accent
# hierarchy — see session-optimizer PR "design(statusline): AI Architect DA
# compliance — neutral chrome, single accent, no data-hue borrowing".
#
# No DIM attribute anywhere — it renders unreadable on black terminals.
# Secondary text uses light grey; primary uses bright white.
RESET="\033[0m"
TEXT="\033[38;2;243;241;238m"     # #f3f1ee — primary text · DS --fg-0 oklch(96% 0.005 80) · scripted oklch->srgb
SUBTEXT="\033[38;2;192;189;186m"  # #c0bdba — secondary text / labels · DS --fg-1 oklch(80% 0.006 70) · scripted oklch->srgb
OVERLAY="\033[38;2;136;134;130m"  # #888682 — separators / muted / decorative icons · DS --fg-2 oklch(62% 0.006 70) · scripted oklch->srgb
GREEN="\033[38;2;101;201;140m"    # #65c98c — DS --ok oklch(76% 0.13 155) · scripted oklch->srgb
YELLOW="\033[38;2;232;170;78m"    # #e8aa4e — DS --warn oklch(78% 0.13 75) · scripted oklch->srgb
RED="\033[38;2;232;97;84m"        # #e86154 — DS --danger oklch(66% 0.17 28) · scripted oklch->srgb
PEACH="\033[38;2;207;110;57m"     # #cf6e39 — DS --accent (terracotta) oklch(64% 0.14 47) · scripted oklch->srgb · two legitimate consumers: the effort badge (G4 selection-only accent) AND the heat-track palier 4 (HEAT_4 below, G6 heat-track exception) — never a third, decorative use
# back-compat aliases used by the renderer
WHITE="$TEXT"; LGREY="$SUBTEXT"
SEP="${OVERLAY}│${RESET}"

# Semantic roles for the two kinds of word the renderer prints. Defined here
# rather than at the call site so a theme change is one file, not two.
LABEL="$SUBTEXT"     # the leading concern word of a line
KEY="$OVERLAY"       # the word naming an individual value inside a line

# Heat-track palette (DS gate G6: "No gradients except the heat track" —
# this bar IS the heat track, so a 4-palier discrete ramp is the compliant
# form; no interpolation). Thresholds HEAT_T2/T3/T4 are named constants,
# format matches the TEXT/SUBTEXT/OVERLAY/PEACH block above: hex + DS token +
# derivation on each line.
HEAT_T2=50   # palier 1->2 boundary (%)
HEAT_T3=75   # palier 2->3 boundary (%)
HEAT_T4=90   # palier 3->4 boundary (%); matches WARN/SAVE ratio for
             # sonnet/opus (180000/200000 = 0.90) in ctxguard-thresholds.json
             # (see config.sh) — the heat track's critical palier starts
             # exactly where the checkpoint hook's own hard-cap ratio does, so
             # the visual and the enforcement threshold cannot drift apart by
             # design.
HEAT_1="136;134;130"  # #888682 — DS --fg-2 oklch(62% 0.006 70) · scripted oklch->srgb · reused from OVERLAY (0-49%)
HEAT_2="192;189;186"  # #c0bdba — DS --fg-1 oklch(80% 0.006 70) · scripted oklch->srgb · reused from SUBTEXT (50-74%)
HEAT_3="181;125;98"   # #b57d62 — terracotta atténué, source: scripted oklch->srgb, oklch(64% 0.08 47), Ottosson OKLab (75-89%)
HEAT_4="207;110;57"   # #cf6e39 — DS --accent (terracotta) oklch(64% 0.14 47) · scripted oklch->srgb · reused from PEACH (90-100%)

# heat_rgb — pure heat-track color selector.
# pre: $1 is an integer (any sign, any magnitude); non-integer input is a
#      contract violation and the function fails (exit 1, no output).
# post: on success, prints exactly one of {HEAT_1, HEAT_2, HEAT_3, HEAT_4}
#       as "r;g;b" (never interpolated) and returns 0. Input is clamped to
#       [0,100] before palier lookup, so out-of-range integers always
#       succeed and resolve to the nearest boundary palier (HEAT_1 or
#       HEAT_4). No I/O, no global mutation beyond reading the HEAT_*
#       constants above — total function once input is validated numeric.
heat_rgb() {
  local p="$1"
  case "$p" in
    ''|*[!0-9-]*) return 1 ;;   # reject empty / non-numeric (incl. "abc")
  esac
  [ "$p" -lt 0 ] && p=0
  [ "$p" -gt 100 ] && p=100
  if   [ "$p" -lt "$HEAT_T2" ]; then printf '%s' "$HEAT_1"
  elif [ "$p" -lt "$HEAT_T3" ]; then printf '%s' "$HEAT_2"
  elif [ "$p" -lt "$HEAT_T4" ]; then printf '%s' "$HEAT_3"
  else                                printf '%s' "$HEAT_4"
  fi
}

# render a heat-track block bar of $2 cells filled to $1 percent (clamped
# 0..width). Each filled cell is colored by its POSITION along the full
# width via heat_rgb (4 discrete paliers, no interpolation — G6 heat-track
# exception), so a longer (more-full) bar visibly accumulates paliers left
# to right. Empty cells stay muted. The bar is self-coloring: callers must
# NOT wrap the result in a single color (trailing RESET closes it).
make_bar() {
  local p="$1" w="$2" filled empty i pos rgb e b=""
  filled=$(( (p * w + 50) / 100 ))   # round to nearest cell
  [ "$filled" -gt "$w" ] && filled="$w"
  [ "$filled" -lt 0 ] && filled=0
  empty=$(( w - filled ))
  i=0
  while [ "$i" -lt "$filled" ]; do
    pos=$(( w > 1 ? i * 100 / (w - 1) : 0 ))   # cell position 0..100
    rgb=$(heat_rgb "$pos")
    b="${b}\033[38;2;${rgb}m█"
    i=$(( i + 1 ))
  done
  [ "$filled" -gt 0 ] && b="${b}${RESET}"
  [ "$empty" -gt 0 ] && { printf -v e "%${empty}s"; b="${b}${OVERLAY}${e// /░}${RESET}"; }
  printf '%s' "$b"
}
