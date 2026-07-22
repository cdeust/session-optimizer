# refine-gate

A prompt-binding gate for [Claude Code](https://code.claude.com):
a `UserPromptSubmit` hook (`hooks/refine_gate.py`) plus the `/refine` skill
(`skills/refine/`).

Communication failures cost more than code failures: "make it work
exactly like the SSE solution" carries precise intent that the model
can bind to the wrong artifact and then build the wrong thing —
correctly. The refine gate makes that binding explicit and cheap to
correct BEFORE work starts.

## Install

```
/plugin marketplace add cdeust/session-optimizer
/plugin install refine-gate@session-optimizer-marketplace
```

The plugin wires the `UserPromptSubmit` hook and registers the `/refine`
skill automatically. Requires Python 3.

## How it works

The hook inspects every prompt on two tiers:

* **Tier 1 — reference markers**: prior-artifact shorthand ("the last
  release"), comparisons to unstated referents ("like before",
  "exactly as"), repeat-failure phrasing ("still broken", "back to
  square one") → inject the full binding-table instruction.
* **Tier 2 — ungrounded work request**: the prompt asks for work
  (fix/build/improve/problem/…) but contains no concrete anchor — no
  file path, commit sha, or line ref. Named systems/variables ("the
  memory system", "the heat variable") must then be bound to their
  actual code artifacts before reasoning. Prompts the user grounded
  themselves pass through untouched. Slash commands are never gated;
  the hook always exits 0 (it can inform, never block).

The injected instruction points at the bundled `/refine` skill: build
a binding table (reference → artifact, with evidence from grep, git
history, and your memory layer's recall tool when one is installed),
separate symptom from goal, select an execution strategy from a
research-backed table (15 strategies re-verified against the 2024–2026
literature, counter-evidence included — e.g. intrinsic self-correction
degrades reasoning, arXiv:2310.01798; CoT prompting is marginal on
reasoning models), and define acceptance criteria as EXTERNAL signals:
run the test, fetch the source, measure — never the model re-checking
itself.

Test it directly:

```bash
echo '{"prompt":"there is a problem in the memory system with the heat variable"}' \
  | python3 hooks/refine_gate.py | python3 -m json.tool
```

Expected: a `hookSpecificOutput.additionalContext` payload carrying the
binding instruction. A grounded prompt (one naming a real file path)
produces no output.

## Token-usage ledger (measured, reproducible)

The gate must not eat the budget it exists to protect. Two instruments
keep that honest:

* `tests/test_refine_gate.py` (repo root) pins the contract: silent
  prompts cost ZERO context, and any injection stays under a 400-token
  ceiling (estimated at 4 chars/token — Anthropic's glossary gives ~3.5
  chars/token EN, so the estimate under-counts ~12%; the ceiling has
  >10x headroom over the current ~275).
* `tools/measure_refine_overhead.py` replays your OWN prompt history
  (local transcripts, nothing leaves the machine). Measured 2026-06-12
  on 117 real prompts across 40 transcripts: fired on 38% (tier 1: 17,
  tier 2: 27, silent: 73), ~275 est. tokens per fired prompt, ~103 per
  prompt overall ≈ **1.3% of a 160k-token session budget per 20-prompt
  session**.

The benefit side is counterfactual and is NOT claimed as measured: a
mis-bound prompt costs roughly a full wrong-direction session
(~10⁵ tokens — e.g. the delivery-layer machinery this gate's design
case produced and later deleted). At ~2k injected tokens per
20-prompt session, the gate breaks even if it prevents one mis-binding
per ~80 sessions.

## Where it runs

Hooks run wherever Claude Code plugins run: **CLI, desktop app, IDE
extensions, and Cowork**. The claude.ai / Claude Desktop **chat** surface
has no hook mechanism — there, upload `skills/refine/` as an Agent
Skill (same SKILL.md format): the `/refine` procedure travels; the
automatic per-prompt gate does not.

## License

[MIT](../../LICENSE) © Clement Deust
