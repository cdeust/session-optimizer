"""Codex session artifacts, with optional completion-receipt protection.

source: disk-hygiene design (native Codex source hook trial 2026-09-26).
No shared databases or generated images are disposable.
Only explicitly ended session IDs enter the retry ledger.
"""

import hashlib
import json
import os
from pathlib import Path

import session_purge
import transcript_policy

ARTIFACTS = (
    "shell_snapshots/{sid}.*.sh",
    "shell_snapshots/{sid}.sh",
    "tui-thread-reference-capabilities/{sid}",
)
TRANSCRIPTS = (
    "sessions/*/*/*/rollout-*-{sid}.jsonl",
    "archived_sessions/rollout-*-{sid}.jsonl",
)


def roots():
    home = (
        Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        .expanduser()
        .resolve()
    )
    claude = (
        Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        .expanduser()
        .resolve()
    )
    # Receipts are written by an optional session-end reader (Cortex does).
    # Without that directory no receipt exists and Codex transcripts are kept.
    return home, claude / "methodology/session-end-queue/completed"


def completed(queue, session, transcript):
    """A reader that imported the rollout leaves the original event as a receipt."""
    key = hashlib.sha256(session.encode()).hexdigest()
    try:
        receipt = queue / (key + ".json")
        event = json.loads(receipt.read_text())["event"]
        return (
            event["session_id"] == session
            and Path(event["transcript_path"]).absolute() == transcript.absolute()
            and receipt.stat().st_mtime_ns >= transcript.stat().st_mtime_ns
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def purge(locations, session, guard, ended=True):
    home, queue = locations
    sid = session_purge.checked(session)
    results = []
    lock = home / "thread-writer-locks" / (sid + ".lock")
    try:
        check_writer(lock, guard)
    except RuntimeError as exc:
        return [{"path": str(lock), "protected": str(exc)}]
    patterns = ARTIFACTS + (TRANSCRIPTS if transcript_policy.delete_enabled() else ())
    for pattern in patterns:
        for path in home.glob(pattern.format(sid=sid)):
            # Never follow a symlinked ancestor out of the session namespace.
            if path.parent.resolve() != path.parent.absolute():
                results.append({"path": str(path), "protected": "symlink parent"})
                continue
            if pattern in TRANSCRIPTS and (
                not ended or not completed(queue, sid, path)
            ):
                results.append(
                    {
                        "path": str(path),
                        "protected": "no completion receipt for this rollout",
                    }
                )
                continue
            try:
                results.extend(session_purge.remove_tree(path, guard))
            except OSError as exc:
                results.append({"path": str(path), "protected": str(exc)})
    return results


def settle(args, registry, guard):
    """Retry ended sessions on each event; resume cancels that session's entry."""
    session_purge.checked(args.session)
    home, queue = roots()
    ledger = Path(args.state).with_name("codex-ended-sessions.json")
    results = []
    with registry(ledger) as pending:
        scope = {"home": str(home), "queue": str(queue)}
        matching = args.session not in pending or pending[args.session] == scope
        if args.event == "SessionStart" and matching:
            pending.pop(args.session, None)
        if args.event == "SessionEnd":
            if not matching:
                return [{"session": args.session, "protected": "different Codex roots"}]
            pending[args.session] = scope
            return []  # Native Codex SessionEnd is limited to three seconds; disk-hygiene design.
        for sid, record in list(pending.items()):
            if sid == args.session and args.event != "SessionEnd":
                continue
            if record != {"home": str(home), "queue": str(queue)}:
                continue  # Another configured home owns this entry.
            done = purge((home, queue), sid, guard)
            results.extend(done)
            if not any("protected" in r for r in done):
                del pending[sid]
    return results


def check_writer(lock, guard):
    if lock.exists():
        guard(str(lock))
