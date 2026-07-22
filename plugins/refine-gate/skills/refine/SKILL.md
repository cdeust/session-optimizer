---
name: refine
description: >
  Crossover layer between the user's intent and LLM execution: takes a raw
  prompt (terse, frustrated, ambiguous, or shorthand), resolves every vague
  reference to a concrete artifact, recalls past decisions and lessons, and
  compiles a verifiable execution contract BEFORE any code is touched.
  Invoke as /refine <raw prompt>, or on any prompt where intent might be
  misread. Prevents: solving the wrong problem, dead/unwired code,
  band-aid fixes, non-scalable hot paths, unreadable output.
category: engineering
trigger: >
  The user describes a task or bug in shorthand ("make it work like before",
  "the X solution", "it's still broken"), references prior work without
  naming files/commits, or a previous attempt missed their intent.
input: >
  The raw prompt verbatim (skill args; if empty, the user's last message).
output: >
  A compiled execution contract (goal, resolved references, constraints,
  acceptance criteria, non-goals) — then execution under that contract.
---

## Automation

A `UserPromptSubmit` hook (`hooks/refine_gate.py`, wired by this
plugin's `hooks/hooks.json`) applies this skill automatically on two
tiers:

* **Tier 1 — reference markers**: prior-artifact shorthand, "exactly
  as/like", repeat-failure phrasing → inject the full binding-table
  instruction naming the matched marker classes.
* **Tier 2 — ungrounded work request** (the generic net): the prompt
  asks for work (fix/build/improve/problem/bug/should/…) but contains
  NO concrete anchor — no file path, no commit sha, no line ref. Any
  named system/variable/concept ("the memory system", "the heat
  variable") must then be bound to its actual code artifact before
  reasoning. Prompts the user grounded themselves (a path in the
  prompt) pass through untouched.

Explicit `/refine` remains for prompts both heuristics miss.

## Purpose

Communication failures cost more than code failures. A terse prompt like
"it must work exactly as the SSE solution" carries a precise intent that
the model can mis-bind to the wrong artifact and then build the wrong
thing — correctly. This skill makes the binding explicit and cheap to
correct BEFORE work starts, instead of expensive to redo after.

## Procedure

### 1. Capture — never paraphrase away the original

Quote the raw prompt verbatim at the top of the contract. The user's
exact words are evidence; frustration markers ("still", "again", "back
to square one") signal a REPEATED failure — treat those as pointers to
prior attempts that must be recalled, not as noise to sanitize away.

### 2. Bind every reference — the core move

Two reference classes need binding, and BOTH go in the table:

**Deictic/temporal** — "it", "the X solution", "like before", "the
last release", "that file", "the same way": shorthand for prior work.

**Domain-entity** — "the memory system", "the wiki system", "the heat
variable", "recall scores": names of systems, components, variables,
and concepts. These feel concrete to the user but map to MANY possible
code artifacts ("heat" alone: the memory row field, thermodynamics.py,
decay_cycle.py, the WRRF heat signal, the viz heat display). Picking
the wrong one solves the wrong problem correctly.

For EACH reference of either class, bind it to a concrete artifact
with evidence:

- the memory system when available (e.g. `cortex:recall`, scoped to
  the project) for past decisions, RCAs, and lessons mentioning the term
- `git log --oneline -15` + `git log -S"<term>"` for "the last X",
  "before", "the version that worked"
- Grep/Glob for named-but-unlocated code ("the SSE solution" →
  the actual module, route, and client file)
- Running processes / installed versions when the prompt says
  "currently", "still", "on my machine"

Binding table (mandatory in the contract):

| Reference in prompt | Bound to | Evidence |
|---|---|---|
| "the sse solution" | `/api/graph/events` + `graph_event_stream.{py,js}` | recall #4197485, grep |

A load-bearing reference that cannot be bound with evidence is a STOP:
ask ONE batch of clarifying questions (AskUserQuestion, ≤3 questions,
each offering the concrete candidates found). Never guess silently on
a load-bearing binding; never ask about bindings the evidence already
settles.

### 3. Separate symptom from goal

State, in one sentence each:
- **Symptom** — what the user observes (e.g. "L6 never finishes").
- **Goal** — the end-state they want (e.g. "the full galaxy streams in
  real time over one connection and every node is clickable").
- **Non-goals** — what the prompt does NOT ask for (resist scope creep;
  list the tempting adjacent fixes being deliberately skipped).

If the prompt is a question or a problem description with no change
request, the contract's goal is a DIAGNOSIS, not a fix — say so.

### 4. Compile the constraints

Always binding (do not restate, reference): the active coding
standards — the project's CLAUDE.md / lint config, plus the user's
global rules when present (e.g. `~/.claude/rules/coding-standards.md`:
SOLID, Clean Architecture layers, size limits, no dead/unwired code,
root-cause-only fixes, local reasoning, zetetic source discipline).

Add prompt-specific constraints extracted from the user's words and
from recalled lessons (e.g. "no in-between interface" → polling/
pagination layers are forbidden, not just disfavored). Quote the
user's phrase next to each derived constraint so the mapping is
auditable.

### 5. Select the execution strategy — research-backed, not habitual

Lineage: ported from the AIPRDStrategyEngine (ai-architect-prd-builder,
`packages/AIPRDStrategyEngine/Sources/Core/ResearchEvidenceDatabase*`,
15 strategies, evidence 2020–2025), then re-verified against the
2024–2026 literature (web research 2026-06-12). Zetetic corrections
from that pass: the engine's CoVe attribution ("Stanford/Anthropic")
is wrong — arXiv:2309.11495 is Dhuliawala et al., Meta AI/ETH; and
several 2022–2023 techniques now carry measured COUNTER-evidence that
the engine predates. Statuses below: **native** (the harness already
does it), **confirmed** (still works as described), **caveated**
(works only under conditions the original paper glossed over),
**weakened** (marginal or harmful on current reasoning models).

Read the bound prompt's characteristics (multi-step logic? branch
exploration? accuracy-critical? example-based output? external
knowledge?) and pick the matching row(s) — usually 1–2, never all:

| Strategy | Use when | Evidence (2026 status) | Realization in this harness |
|---|---|---|---|
| context_engineering | always — the frame the others fit in | Anthropic eng. blog 2025-09-29; ACE arXiv:2510.04618 (+10.6% agents) (confirmed, current SOTA frame) | curate WHAT is in context — binding table, recalled lessons, real file contents — over wording; §§1–4 of this skill ARE context engineering |
| recursive_refinement | hard reasoning, high precision | DeepSeek-R1 arXiv:2501.12948; Snell arXiv:2408.03314; survey arXiv:2512.02008: no test-time strategy universally optimal (confirmed, native) | extended thinking / higher effort on the hard subtask only |
| self_consistency | one answer, several plausible derivations | Wang arXiv:2203.11171; still effective as parallel test-time scaling (arXiv:2512.02008, ST-BoN arXiv:2503.01422) (confirmed) | N independent attempts + majority/judge via parallel subagents |
| verified_reasoning | accuracy-critical claims | CoVe arXiv:2309.11495 (Meta AI, not Stanford/Anthropic); BUT intrinsic self-correction degrades reasoning: Huang ICLR'24 arXiv:2310.01798, Self-Correction Bench arXiv:2507.02778 (caveated) | verify against EXTERNAL ground truth — run the test, fetch the source, measure — never by re-asking the model to double-check itself |
| reflexion | retrying after failed attempts | Shinn arXiv:2303.11366; reflection without external signal is mostly confirmatory, rarely flips the answer ("First Try Matters" arXiv:2510.08308) (caveated) | feed REAL feedback — test failures, benchmark deltas, recalled failures (recall past attempts first, store the lesson after, via your memory layer when one is installed) |
| tree_of_thoughts | genuinely branching solution space | Yao arXiv:2305.10601; ~100× compute vs one CoT on Game-of-24, gains shown on puzzle-class tasks (arXiv:2401.14295) (caveated: cost) | judge panel of N approaches ONLY when designs genuinely diverge; otherwise skip |
| graph_of_thoughts | decompose-and-merge structure | Besta arXiv:2308.09687 (confirmed, niche) | build the dependency graph explicitly (query_workflow_graph / get_impact) before editing |
| meta_prompting | task spans expert roles | Suzgun arXiv:2401.12954; BUT multi-agent often ≤ single agent — 14 failure modes, spec issues + misalignment + weak verification (MAST arXiv:2503.13657) (caveated) | route to ONE specialist with a complete role spec + verification step; don't fan out by default |
| plan_and_solve | ordering-heavy multi-step work | Wang arXiv:2305.04091 (confirmed, absorbed into agentic planning) | plan mode / written plan with checkable steps |
| react | interleaved reasoning + tool use | Yao arXiv:2210.03629 (native) | the agent loop IS ReAct; just act |
| many_shot (was few_shot) | output format/style must match examples | Brown arXiv:2005.14165 → Many-Shot ICL arXiv:2404.11018 (NeurIPS'24 spotlight): more real examples keep helping with long context (confirmed, evolved) | paste real examples from THIS repo; scale count with context budget |
| generate_knowledge | missing domain facts | Liu arXiv:2110.08387 (weakened: parametric generation hallucinates; superseded by retrieval) | RETRIEVE, don't generate: your memory layer's recall (when installed), read the actual paper/source — knowledge from a vacuum is the failure mode §8 of coding-standards forbids |
| multimodal_cot | visual evidence | Zhang arXiv:2302.00923 (native multimodality) | read the actual image/screenshot; verify UI claims by looking |
| chain_of_thought | explicit audit trail wanted | Wei-era CoT now marginal on reasoning models: +2.9–3.1% at 20–80% time cost (Wharton GAIL 2025); HARMS some task classes, −36.3% o1-preview on implicit statistical learning (arXiv:2410.21333); models reason unprompted (arXiv:2402.10200) (weakened) | don't inject "think step by step"; write the derivation out only when the USER needs the audit trail |
| problem_analysis | tangled symptom, unclear decomposition | engine-internal (no arXiv); closest sourced umbrella: decomposition class in The Prompt Report arXiv:2406.06608 (weakest row) | §§2–3 of this skill: binding + symptom/goal split IS the decomposition |
| auto_optimization | a RECURRING prompt/pipeline worth tuning | GEPA arXiv:2507.19457 (ICLR'26 oral): reflective prompt evolution beats MIPROv2 by 10–14%, 35× fewer rollouts than GRPO (confirmed, new) | for repeated prompts (hooks, pipelines, skills): optimize against an eval set programmatically, don't hand-tune by vibes |
| zero_shot | trivial, single-step, well-specified | baseline; scaffolding simple tasks degrades output (arXiv:2410.21333) | just do it |

Selection rules (engine's ResearchWeightedSelector, updated 2026):
1. Match characteristics; never stack strategies the task doesn't
   exhibit — scaffolding is now MEASURED to harm some task classes,
   not just waste tokens.
2. Prefer external feedback over intrinsic reflection — the model
   re-checking itself without new evidence degrades reasoning
   (arXiv:2310.01798). Tests, benchmarks, sources, and tools are the
   feedback channel.
3. No test-time strategy is universally optimal (arXiv:2512.02008);
   when evidence conflicts or the task is trivial, zero_shot wins.
4. This table is a snapshot (verified 2026-06-12). Newer technique
   with better evidence → use it AND update this table with the
   citation.

### 6. Define acceptance criteria — external signals or it doesn't count

Each criterion is a command or observation that can pass/fail
INDEPENDENTLY of the model's own judgment. Intrinsic self-checking
("re-read your answer and confirm it's right") measurably degrades
reasoning (arXiv:2310.01798) and reflection without new evidence is
mostly confirmatory (arXiv:2510.08308) — so a criterion the model
grades by introspection is not a criterion:
- the reproduction case, and the command that proves it fixed
- tests that must pass (name the suites)
- measured numbers where performance is the complaint (before/after)
- a fetched source / document for factual claims
- "would a staff engineer approve this?" is the floor, not the bar

### 7. Echo, then execute

Print the compiled contract compactly (≤30 lines). If every
load-bearing reference is bound and no blocking ambiguity remains,
EXECUTE immediately under the contract — do not ask for permission to
do what was asked. If executing, verify each acceptance criterion at
the end and report pass/fail explicitly.

### 8. Close the loop

After execution (or after the user corrects a binding), store the raw
phrase → correct binding pair via your memory layer's remember tool
when one is installed (e.g. `cortex:remember`, tags: ["archival",
"lesson", "prompt-binding"], agent_topic scoped), so the next session
binds it instantly; without a memory layer, note it in the project's
own records (CLAUDE.md or a docs note). Mis-bindings the user had to
correct are the highest-value memories this skill produces.

## Failure modes this skill exists to prevent

| Failure | Gate |
|---|---|
| Solving the wrong problem confidently | §2 binding table + §3 symptom/goal split |
| Dead or unwired code | §4 standards + §6 "must be called" criterion |
| Band-aid at the throw site | §4 root-cause rule; RCA before fix in §3 |
| Non-scalable hot path | §6 measured numbers when perf is the complaint |
| Unreadable output | §4 size limits / naming rules |
| Re-losing corrected intent | §8 phrase→binding memory |
| Self-verification theater (model grading itself) | §6 external-signal criteria (arXiv:2310.01798) |
| Habitual scaffolding on simple tasks | §5 rules 1+3 — zero_shot wins (arXiv:2410.21333) |

## Example

Raw: *"we're back to square one, graph L6 not finishing, must work
exactly as the sse solution, not in between interface"*

Bound: "square one" → prior session RCA (recall); "the sse solution" →
`/api/graph/events` + `GraphEventStream` (grep + recall); "in between
interface" → the polling phase loader in `unified-viz.html` (git log).
Goal: SSE is the ONLY delivery path, L6 completes, nodes browsable.
Acceptance: build reaches `full_ready` with symbols; SSE replay ends in
`done`; `/api/graph/node` returns `found:true` for a symbol id.
