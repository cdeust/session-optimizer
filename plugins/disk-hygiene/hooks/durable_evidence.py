"""Evidence must outlive every session: refuse what the host purgers select.

source: measured 2026-10-02. A worktree whose evidence was the context-guard
checkpoint of its own session stayed protected for ever, because session end
removed the checkpoint that `dispose` reads back.
"""

import os
import re
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
# Hosts write session ids in lower case; a file system that ignores letter case
# opens the same file under any other spelling of the id.
ANY_CASE_ID = re.compile(session_purge.SESSION_ID.pattern, re.IGNORECASE)


def entry(path):
    """A directory entry by identity: its own file and its directory's.

    source: measured 2026-10-02 on APFS. `.CLAUDE/memories/checkpoints/<sid>.md`
    opens the file that session end removes and equals no purge target as a
    string. A hard link kept in another directory is another entry: removing
    one name leaves the other.
    """
    own, directory = os.lstat(path), os.stat(Path(path).parent)
    return own.st_dev, own.st_ino, directory.st_dev, directory.st_ino


def chain(path):
    """The entries a path goes through: itself and every ancestor."""
    path = Path(path)
    return {entry(step) for step in (path, *path.parents)}


def purge_target(evidence):
    """The session-owned path that holds this file, or None.

    Every purge selector embeds a session id, so a path without one is never
    selected. The purgers' own globs are evaluated on the file system: there is
    no second list of locations to keep in step with them. A selected path is
    matched by file identity, not by spelling.
    """
    evidence = Path(evidence)
    through = chain(evidence)
    claude, codex = session_purge.claude_home(), codex_purge.roots()[0]
    # The Claude temp root is named after the user id, which only POSIX has.
    temp = session_purge.temp_root() if hasattr(os, "getuid") else None
    for sid in sorted({sid.lower() for sid in ANY_CASE_ID.findall(str(evidence))}):
        targets = session_purge.home_paths(claude, sid, CLAUDE_PATTERNS)
        targets += session_purge.home_paths(codex, sid, CODEX_PATTERNS)
        if temp:
            targets += session_purge.home_paths(
                temp, sid, (session_purge.TEMP_PATTERN,)
            )
        for target in targets:
            if entry(target) in through:
                return target
    return None
