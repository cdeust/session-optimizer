"""checkpoint_stub.py — the pure markdown render for the mechanical
checkpoint stub.

Separate concern from stop-context-guard.py's I/O (git subprocess calls, the
subagent-aggregate read, the actual file writes) and from its FireEvent
value object: this module only turns already-gathered facts into the
summary-schema markdown text. No I/O, no dependency on anything monkeypatched
at the guard module's boundary. `ev` is duck-typed (FireEvent's shape:
session_id/cwd/ctx/model_id/level) so this module does not need to import it.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class GitInfo:
    """The three `git` facts the stub's "Current state" section reports."""

    branch: str
    last_commit: str
    modified: str


def render(ev, git_info: GitInfo, sub_state: str) -> str:
    """Render the checkpoint-stub markdown body for one fire event. Pure."""
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""---
description: "Auto-checkpoint ({ev.level}) at {ev.ctx:,} tokens — session {ev.session_id[:8]} on {git_info.branch or "unknown branch"}"
---
## Auto-checkpoint stub ({ev.level}) — {iso}

> Mechanical state captured for free by stop-context-guard at {ev.ctx:,} context
> tokens (model: {ev.model_id or "unknown"}). The semantic fields below follow the
> summary schema. Budget: <=500 words total across all sections; clip any
> quoted tool output to 2,000 chars.

### Goals
<to be filled: what this session is trying to achieve, in priority order>

### File references
(paths + line ranges the resumed session will need; seeded from git status —
replace with the load-bearing files and add `path:start-end` line ranges)
{_modified_lines(git_info.modified)}

### Errors and fixes
<to be filled: each error hit this session and how it was fixed or worked around>

### Current state
- session_id: {ev.session_id}
- model: {ev.model_id or "unknown"} · context tokens at trigger: {ev.ctx:,}
- working dir: {ev.cwd}
- branch: {git_info.branch or "(unknown)"} · last commit: {git_info.last_commit or "(none)"}
{sub_state}<to be filled: one paragraph — where the work stands right now>

### Next steps
<to be filled: exact ordered actions for the resumed session; first one must be
executable without re-deriving anything>

### Resume contract
Read this checkpoint + at most ONE targeted search. Do NOT re-read files this
checkpoint already summarizes — trust the file references above and verify with
targeted Reads only when editing.
"""


def _modified_lines(modified: str) -> str:
    if not modified:
        return "- (working tree clean)"
    return os.linesep.join("- " + line.strip() for line in modified.splitlines())
