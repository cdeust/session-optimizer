"""Durable ownership ledger; fail closed on invalid data or unavailable locks."""

# Source: disk-hygiene design: disk-hygiene atomic ledger protocol.
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProtectedError(RuntimeError):
    pass


# Operational wait for a concurrent hook to release the ledger, not an
# eligibility threshold. Hook timeouts are 120 s (3 s only for Codex SessionEnd,
# which never takes this lock).
LOCK_WAIT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05


Protected = ProtectedError

# The session roots a registration records (cleanup_intake.roots): the Claude
# home, the Claude temp directory and the Codex home. A record with another key
# set cannot answer the durability check and is refused as malformed.
ROOT_KEYS = frozenset(("claude_home", "claude_tmp", "codex_home"))


def valid_roots(roots):
    return (
        isinstance(roots, dict)
        and set(roots) == ROOT_KEYS
        and all(isinstance(v, str) for v in roots.values())
    )


def validate(state):
    if not isinstance(state, dict):
        raise Protected("registry must be an object")
    for path, record in state.items():
        if not isinstance(record, dict):
            raise Protected("registry records must be objects")
        if path == "_ended":
            if not all(isinstance(k, str) and v is True for k, v in record.items()):
                raise Protected("invalid ended owners")
            continue
        if not Path(path).is_absolute() or record.get("kind") not in (
            "temp",
            "worktree",
        ):
            raise Protected("invalid registered path or kind")
        if not isinstance(record.get("owner"), str) or not isinstance(
            record.get("identity"), list
        ):
            raise Protected("invalid registered owner or identity")
        if "roots" in record and not valid_roots(record["roots"]):
            raise Protected("invalid registered session roots")
        if record["kind"] == "worktree" and not all(
            isinstance(record.get(k), str) for k in ("repo", "branch")
        ):
            raise Protected("invalid worktree record")


def acquire(handle):
    """Try the non-blocking lock until the bounded wait ends."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise Protected(
                    f"ledger busy after {LOCK_WAIT_SECONDS}s: {exc}"
                ) from exc
            time.sleep(LOCK_POLL_SECONDS)


@contextmanager
def lock_file(handle):
    if os.name == "nt":
        handle.seek(0)
        if not handle.read(1):
            handle.write("0")
            handle.flush()
    acquire(handle)
    try:
        yield
    finally:
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)


def save(path, state):
    fd, pending = tempfile.mkstemp(prefix=".hygiene-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(state, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


@contextmanager
def registry(path, validator=validate):
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+") as handle, lock_file(handle):
        state = json.loads(path.read_text()) if path.exists() else {}
        validator(state)
        before = json.dumps(state, sort_keys=True)
        yield state
        validator(state)
        # An unchanged ledger is not rewritten: read-only hooks cost no fsync.
        if json.dumps(state, sort_keys=True) != before or not path.exists():
            save(path, state)
