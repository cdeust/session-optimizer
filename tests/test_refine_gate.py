"""Refine-gate contract: fires on the right prompts, stays silent on the
rest, and its context cost is BOUNDED so the gate can never become a
token-usage problem itself.

Token accounting model (explicit, so the numbers are auditable):
  * tokens are estimated at 4 characters/token — the published
    rule-of-thumb for English text (OpenAI tokenizer docs; Anthropic
    glossary gives 1 token ≈ 3.5 chars EN). We use 4 chars/token,
    which UNDER-estimates token count by ~12% vs 3.5 — acceptable
    because the assertions below leave >10x headroom.
  * the gate's worst case is ONE injection per user prompt; silent
    prompts cost zero context.

The benefit side (prevented wrong-direction work) is counterfactual and
NOT asserted here — see README §3. What this suite pins is that the
overhead side of the ledger is structurally small: a few hundred tokens
per gated prompt vs the ~10⁵-token cost of one mis-bound implementation
cycle.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "refine-gate" / "hooks" / "refine_gate.py"
)

# 4 chars/token heuristic — see module docstring for source + bias note.
CHARS_PER_TOKEN = 4

# Hard ceiling on the gate's per-prompt context cost. The current
# instructions measure ~160-190 estimated tokens; the ceiling leaves
# room for wording changes but blocks runaway instruction growth
# (a gate that injects thousands of tokens per prompt would damage the
# very budget session-optimizer protects).
MAX_INJECTED_TOKENS = 400


def run_gate(prompt: str) -> dict | None:
    """Pipe one prompt through the hook exactly as Claude Code does."""
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"gate must always exit 0: {proc.stderr}"
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def injected_tokens(payload: dict) -> int:
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    return len(ctx) // CHARS_PER_TOKEN


# ── Firing behaviour ────────────────────────────────────────────────────

TIER1_PROMPTS = [
    "the graph must work exactly as the sse solution",
    "we are back to square one, it is still not finishing",
    "make it behave like before",
    "restore the last release behaviour",
]

TIER2_PROMPTS = [
    "there is a problem in the memory system with the heat variable",
    "recall scores should be improved for temporal queries",
    "fix the deadlock in the wiki system",
]

SILENT_PROMPTS = [
    # Self-grounded: concrete anchors present.
    "fix the off-by-one in mcp_server/core/decay_cycle.py",
    "revert commit bac01b2f23516448094eded3b9d7c420c431251d",
    # No work requested.
    "what does the consolidation engine do?",
    "thanks, looks good",
    # Slash commands are never gated.
    "/refine fix the thing like before",
]


def test_tier1_fires_with_marker_names():
    for p in TIER1_PROMPTS:
        out = run_gate(p)
        assert out is not None, f"tier1 must fire: {p!r}"
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "matched:" in ctx, f"tier1 names its markers: {p!r}"


def test_tier2_fires_on_ungrounded_work():
    for p in TIER2_PROMPTS:
        out = run_gate(p)
        assert out is not None, f"tier2 must fire: {p!r}"
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "names no concrete artifact" in ctx, p


def test_silent_on_grounded_questions_and_slash():
    for p in SILENT_PROMPTS:
        assert run_gate(p) is None, f"must stay silent: {p!r}"


def test_malformed_stdin_is_harmless():
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ── Token-budget contract ───────────────────────────────────────────────


def test_injection_cost_is_bounded():
    """Every possible injection stays under MAX_INJECTED_TOKENS — the
    gate's worst-case context cost per prompt is a known small number,
    not an open-ended one."""
    worst = 0
    for p in TIER1_PROMPTS + TIER2_PROMPTS:
        out = run_gate(p)
        worst = max(worst, injected_tokens(out))
    assert worst <= MAX_INJECTED_TOKENS, (
        f"injection grew to ~{worst} tokens (> {MAX_INJECTED_TOKENS}); "
        "the gate is eating the budget it exists to protect"
    )


def test_silent_prompts_cost_zero():
    for p in SILENT_PROMPTS:
        assert run_gate(p) is None  # no payload → zero context cost
