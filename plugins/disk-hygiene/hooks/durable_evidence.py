"""Evidence must outlive every session: refuse what the host purgers select.

source: measured 2026-10-02. A worktree whose evidence was the context-guard
checkpoint of its own session stayed protected for ever, because session end
removed the checkpoint that `dispose` reads back.
"""

import os
from pathlib import Path

import codex_purge
import session_purge

# Every retention policy at once: a path removable under any of them is not durable.
CLAUDE_PATTERNS = (
    session_purge.HOST_PATTERNS
    + (session_purge.TRANSCRIPT_PATTERN,)
    + session_purge.ENDED_PATTERNS
)
CODEX_PATTERNS = codex_purge.ARTIFACTS + codex_purge.TRANSCRIPTS


def purge_target(evidence):
    """The session-owned path that holds this file, or None.

    Every purge selector embeds a session id, so a path without one is never
    selected. The purgers' own globs are evaluated on the file system: there is
    no second list of locations to keep in step with them.
    """
    evidence = Path(evidence)
    claude, codex = session_purge.claude_home(), codex_purge.roots()[0]
    # The Claude temp root is named after the user id, which only POSIX has.
    temp = session_purge.temp_root() if hasattr(os, "getuid") else None
    for sid in sorted(set(session_purge.SESSION_ID.findall(str(evidence)))):
        targets = session_purge.home_paths(claude, sid, CLAUDE_PATTERNS)
        targets += session_purge.home_paths(codex, sid, CODEX_PATTERNS)
        if temp:
            targets += session_purge.home_paths(
                temp, sid, (session_purge.TEMP_PATTERN,)
            )
        for target in targets:
            if target == evidence or target in evidence.parents:
                return target
    return None
