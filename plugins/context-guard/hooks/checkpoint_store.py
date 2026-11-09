"""checkpoint_store.py — where checkpoint files live and which one `latest.md` is.

Separate concern from checkpoint_stub.py (the pure markdown render) and from
stop-context-guard.py (when a fire happens): this module owns the files under
`~/.claude/memories/checkpoints/` and the two invariants a resume relies on
(session-optimizer #64):

  * a fire writes its stub to the per-session path `<session_id>.md` only,
    and never over a per-session checkpoint that is already complete;
  * `latest.md` is only ever a copy of a complete checkpoint: one with no
    unreplaced placeholder (`checkpoint_stub.PLACEHOLDER`) left in it.

The stub is filled after the fire, by the model or the memory-writer agent,
so promotion cannot happen at fire time. The Stop hook calls `promote` on
every Stop of the session, forced continuations included: the Stop that ends
the checkpoint turn is the one that sees the filled file.
"""

import os

import checkpoint_stub

LATEST = "latest.md"


def root() -> str:
    """The checkpoints directory, resolved from HOME at call time."""
    return os.path.join(os.path.expanduser("~"), ".claude", "memories", "checkpoints")


def session_path(directory: str, session_id: str) -> str:
    return os.path.join(directory, f"{session_id}.md")


def is_complete(text: str) -> bool:
    """True when no stub placeholder is left: the checkpoint can be resumed from."""
    return checkpoint_stub.PLACEHOLDER not in text


def _read(path: str):
    """File text, or None when it is absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _write_atomic(path: str, text: str) -> None:
    """Write via a sibling temp file and rename, so a reader never sees half a file."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_stub(directory: str, session_id: str, stub: str) -> str:
    """Write `stub` to the session's checkpoint path and return that path.

    A per-session checkpoint that is already complete (a WARN-time checkpoint
    the model filled, met again by the HARD fire) is kept as is: the fire's
    instructions tell the model to update what changed, which needs the filled
    text, not a fresh stub. `latest.md` is never written here. Raises OSError.
    """
    path = session_path(directory, session_id)
    existing = _read(path)
    if existing is not None and is_complete(existing):
        return path
    _write_atomic(path, stub)
    return path


def promote(directory: str, session_id: str) -> bool:
    """Copy the session's checkpoint to `latest.md` when it is complete and
    newer than `latest.md`. Returns True when `latest.md` was rewritten.

    The mtime comparison keeps a later checkpoint of another session from
    being replaced by an older, unchanged one at this session's next Stop.
    Never raises: a Stop hook must not fail on a checkpoint it cannot read.
    """
    path = session_path(directory, session_id)
    latest = os.path.join(directory, LATEST)
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return False
    try:
        if os.stat(latest).st_mtime >= mtime:
            return False
    except OSError:
        pass  # no latest.md yet
    text = _read(path)
    if text is None or not is_complete(text):
        return False
    try:
        _write_atomic(latest, text)
    except OSError:
        return False
    return True
