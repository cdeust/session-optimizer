#!/usr/bin/env python3
"""UserPromptSubmit gate: inject the /refine binding-table instruction
when the prompt contains unresolved-reference markers.

Part of the refine-gate plugin: ships with the /refine skill
(skills/refine/SKILL.md) and is wired via the plugin's hooks/hooks.json
UserPromptSubmit entry. The skill only runs when invoked; this hook
automates its core
discipline for prompts that look
like they carry unbound references — shorthand for prior work ("the SSE
solution", "like before"), repeat-failure markers ("still broken",
"back to square one"), or comparative deixis ("the same way").

Emits hookSpecificOutput.additionalContext (UserPromptSubmit JSON
contract) on match; prints nothing on no-match. Always exits 0 — a
gate that can block the user's prompt is worse than no gate.
"""

from __future__ import annotations

import json
import re
import sys

# ── Tier 1: reference markers ──
# Each pattern names ONE marker class. Matched case-insensitively
# against the raw prompt. Sourced from observed mis-bindings
# (2026-06-12 graph saga: "the sse solution", "back to square one",
# "still", "exactly as", "in between") — not invented.
_MARKERS: list[tuple[str, str]] = [
    (
        "prior-artifact shorthand",
        r"\bthe (last|previous|old|prior|original|working|same|existing)\s+\w+",
    ),
    (
        "named-but-unlocated solution",
        r"\bthe \w+ (solution|approach|fix|version|release|implementation|way)\b",
    ),
    ("comparison to unstated referent", r"\b(like|as|same as) (before|previously|last time|usual)\b"),
    ("exact-behavior reference", r"\bexactly (as|like)\b"),
    ("repeat-failure", r"\bback to square one\b"),
    (
        "repeat-failure",
        r"\b(still|again|keeps?)\b.{0,40}\b(not|n't|broken|brok|fail|stuck|deadlock|hang|wrong|missing)",
    ),
    (
        "repeat-failure",
        r"\b(not|n't|broken|fail\w*|stuck|wrong)\b.{0,40}\b(still|again)\b",
    ),
]

# ── Tier 2: ungrounded work request ──
# A prompt that asks for work (problem report or change request) is
# generic by nature — "a problem in the memory system with the heat
# variable" names entities ("memory system", "heat") that map to many
# possible code artifacts. The gate cannot enumerate every domain
# vocabulary, so the test is structural instead: the prompt requests
# work AND carries no concrete anchor (file path, commit sha, line
# ref). If the user grounded the prompt themselves, the gate stays
# out of the way.
_WORK_REQUEST = re.compile(
    r"\b(fix|debug|investigat\w*|implement|add|build|create|improve|"
    r"optimi[sz]\w*|refactor|chang\w*|updat\w*|remov\w*|delet\w*|"
    r"migrat\w*|problem|bug|issue|broken|fail\w*|wrong|error|crash\w*|"
    r"slow|deadlock|leak|regression|doesn'?t|isn'?t|won'?t|not working|"
    r"should)\b",
    re.IGNORECASE,
)
_CONCRETE_ANCHOR = re.compile(
    r"[\w./~-]+/[\w.-]+"  # a path with at least one slash
    r"|\b[\w-]+\.(py|js|ts|tsx|md|json|rs|go|java|swift|html|css|sql|"
    r"ya?ml|toml|sh|txt)\b"  # a filename with extension
    r"|\b[0-9a-f]{7,40}\b"  # a commit sha
    r"|:\d+\b",  # a :line ref
    re.IGNORECASE,
)

_INSTRUCTION_REFS = (
    "<refine-gate>This prompt contains references that may be unbound "
    "(matched: {matched}). Before executing, apply the /refine "
    "procedure (the refine-gate /refine skill): (1) build the "
    "binding table — resolve EACH vague reference to a concrete "
    "artifact (file/commit/memory/process) with evidence from "
    "git log, grep, or your memory layer's recall tool when one is "
    "installed; (2) state symptom vs goal vs "
    "non-goals; (3) select the execution strategy from the skill's "
    "research-backed table (/refine skill §5) — match the task's "
    "characteristics, never stack scaffolding a task doesn't exhibit "
    "(measured to harm simple tasks, arXiv:2410.21333); (4) define "
    "acceptance criteria as EXTERNAL signals — run the test, fetch "
    "the source, measure — never the model re-checking itself "
    "(intrinsic self-correction degrades reasoning, arXiv:2310.01798); "
    "THEN execute. If a load-bearing reference cannot be bound with "
    "evidence, ask one batch of clarifying questions listing the "
    "candidates found — do not guess silently.</refine-gate>"
)

_INSTRUCTION_GROUND = (
    "<refine-gate>This prompt requests work but names no concrete "
    "artifact (no file path, commit, or line ref). Before executing, "
    "apply the /refine procedure (the refine-gate /refine skill): "
    "(1) bind every named system, component, variable, or concept to "
    "its actual code artifact (which module IS 'the X system'? which "
    "field/code path IS that variable?) using grep, git history, and "
    "your memory layer's recall tool when one is installed "
    "— a name can map to several artifacts, "
    "and picking the wrong one solves the wrong problem; (2) recall "
    "prior decisions and failed attempts on those artifacts; (3) "
    "state symptom vs goal vs non-goals; (4) select the execution "
    "strategy from the skill's research-backed table (/refine skill §5) — "
    "match the task's characteristics, never stack scaffolding a "
    "task doesn't exhibit (measured to harm simple tasks, "
    "arXiv:2410.21333); (5) define acceptance criteria as EXTERNAL "
    "signals — run the test, fetch the source, measure — never the "
    "model re-checking itself (intrinsic self-correction degrades "
    "reasoning, arXiv:2310.01798); THEN execute. If a name binds to "
    "several candidates and the choice changes the work, ask one "
    "batch of clarifying questions listing them.</refine-gate>"
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    prompt = payload.get("prompt") or ""
    # Slash commands carry their own instructions (including /refine
    # itself); never stack the gate on top of them.
    if not prompt or prompt.lstrip().startswith("/"):
        return
    matched = sorted(
        {label for label, pat in _MARKERS if re.search(pat, prompt, re.IGNORECASE)}
    )
    if matched:
        context = _INSTRUCTION_REFS.format(matched=", ".join(matched))
    elif _WORK_REQUEST.search(prompt) and not _CONCRETE_ANCHOR.search(prompt):
        context = _INSTRUCTION_GROUND
    else:
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context,
                },
                "suppressOutput": True,
            }
        )
    )


if __name__ == "__main__":
    main()
