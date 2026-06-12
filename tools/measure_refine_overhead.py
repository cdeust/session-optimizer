#!/usr/bin/env python3
"""Measure the refine gate's real context overhead on YOUR prompt history.

Replays user prompts from local Claude Code transcripts
(~/.claude/projects/*/*.jsonl) through hooks/refine_gate.py and reports
firing rate + injected-token overhead. Nothing leaves the machine.

Token estimate: 4 chars/token (English rule-of-thumb; Anthropic's
glossary gives ~3.5 chars/token, so this UNDER-counts by ~12% — the
conclusions hold with margin).

Measured 2026-06-12 on 117 prompts / 40 transcripts (cdeust):
  fired 44/117 (38%) — tier1 17, tier2 27, silent 73
  ~275 est. tokens per fired prompt; ~103 per prompt overall
  ≈ 1.3% of a 160k-token session budget per 20-prompt session.

The benefit side is counterfactual (prevented mis-bound work) and is
NOT claimed as measured. Break-even framing: one mis-bound
implementation cycle costs roughly a full session (10^5 tokens); at
~2k injected tokens per 20-prompt session the gate pays for itself if
it prevents one mis-binding per ~80 sessions.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "refine_gate.py"
CHARS_PER_TOKEN = 4
MAX_TRANSCRIPTS = 40


def collect_prompts() -> list[str]:
    projects = Path.home() / ".claude" / "projects"
    files = sorted(
        projects.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:MAX_TRANSCRIPTS]
    prompts: list[str] = []
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "user":
                        continue
                    content = (d.get("message") or {}).get("content")
                    if not isinstance(content, str):
                        continue  # tool_result arrays, not typed prompts
                    t = content.strip()
                    # Skip harness artifacts: command wrappers, hook
                    # echoes, caveat banners, empty lines.
                    if not t or t[0] in "</" or t.startswith("Caveat:"):
                        continue
                    prompts.append(t)
        except OSError:
            continue
    return prompts


def main() -> None:
    prompts = collect_prompts()
    tier1 = tier2 = silent = 0
    injected_chars = 0
    for p in prompts:
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"prompt": p}),
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = proc.stdout.strip()
        if not out:
            silent += 1
            continue
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        injected_chars += len(ctx)
        if "matched:" in ctx:
            tier1 += 1
        else:
            tier2 += 1

    n = len(prompts)
    fired = tier1 + tier2
    tok = injected_chars // CHARS_PER_TOKEN
    print(f"corpus: {n} real user prompts")
    print(
        f"fired: {fired}/{n} ({100 * fired / max(n, 1):.0f}%) — "
        f"tier1 {tier1}, tier2 {tier2}, silent {silent}"
    )
    print(f"total injected: ~{tok:,} est. tokens")
    print(f"mean per fired prompt: ~{tok // max(fired, 1)} est. tokens")
    print(f"mean per prompt (all): ~{tok // max(n, 1)} est. tokens")


if __name__ == "__main__":
    main()
