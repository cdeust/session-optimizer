"""transcript_scan.py — bounded chunked line scanning over a JSONL transcript.

Separate concern from stop-context-guard.py's checkpoint policy: this module
only knows how to walk a transcript file backward or forward in bounded
chunks, yielding complete decoded lines and signaling how the scan stopped
(EOF vs the byte cap). It does not know what a "usage record" or a
"tool_use block" is — the caller supplies that as a predicate.

Both directions share the same budget/failure vocabulary:
  - `ScanBudget(chunk, cap)` — how many bytes to read per chunk, and the hard
    total-bytes ceiling (see stop-context-guard.py's TAIL_CHUNK/TAIL_MAX_BYTES
    docstring for the sourced numbers).
  - `ScanResult.reason` — set to "eof" (reached the start/end of the file
    cleanly, within the cap) or "cap" (the byte ceiling was hit first).
    Callers with different failure semantics (fail-closed vs fail-open) read
    this field differently; this module only reports it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanBudget:
    """Bytes read per chunk, and the hard total-bytes ceiling for one scan."""

    chunk: int
    cap: int


@dataclass(frozen=True)
class ByteRange:
    """The [start, end) byte window a forward scan covers."""

    start: int
    end: int


class ScanResult:
    """Mutable sentinel a chunked-line generator sets once exhausted: "eof"
    or "cap". See module docstring."""

    __slots__ = ("reason",)

    def __init__(self):
        self.reason = None


def first_match(lines, predicate):
    """Return predicate(line) for the first line where it is not None, else
    None. Small enough to keep callers at a shallow nesting depth."""
    for line in lines:
        hit = predicate(line)
        if hit is not None:
            return hit
    return None


def _split_head(buf: str, at_file_start: bool):
    """Split `buf` into (carry, complete_lines) for the backward scan.

    If `at_file_start`, buf begins at a true line boundary (the start of the
    file), so it splits cleanly and carry is "". Otherwise the first segment
    of buf may be a partial line whose true beginning is in an earlier
    (not-yet-read) chunk; that partial segment is held back as carry and only
    the complete lines after it are returned. If buf has no newline at all
    yet, carry holds the whole buffer and there are no complete lines yet.
    """
    if at_file_start:
        return "", buf.split("\n")
    nl = buf.find("\n")
    if nl == -1:
        return buf, []
    return buf[:nl], buf[nl + 1:].split("\n")


def iter_lines_backward(fh, size: int, result: ScanResult, budget: ScanBudget):
    """Yield complete lines from EOF backward toward the start of `fh`
    (opened in binary mode), decoded as UTF-8 with errors='replace' so a
    chunk boundary splitting a multi-byte sequence cannot raise. Stops after
    `budget.cap` total bytes or at the start of the file, whichever comes
    first, and sets `result.reason` to "eof" or "cap" accordingly.
    """
    carry = ""
    pos = size
    scanned = 0
    while pos > 0 and scanned < budget.cap:
        read_size = min(budget.chunk, pos)
        pos -= read_size
        scanned += read_size
        fh.seek(pos)
        chunk = fh.read(read_size).decode("utf-8", errors="replace")
        carry, lines = _split_head(chunk + carry, at_file_start=(pos == 0))
        yield from reversed(lines)
    if carry:
        yield carry
    result.reason = "eof" if pos <= 0 else "cap"


def iter_lines_forward(fh, rng: ByteRange, result: ScanResult, budget: ScanBudget):
    """Yield complete lines from byte `rng.start` forward to `rng.end` of
    `fh` (opened in binary mode, positioned by this generator), decoded as
    UTF-8 with errors='replace'. Stops after `budget.cap` total bytes or at
    `rng.end`, whichever comes first, and sets `result.reason` to "eof" or
    "cap" accordingly.
    """
    fh.seek(rng.start)
    pos = rng.start
    scanned = 0
    carry = ""
    while pos < rng.end and scanned < budget.cap:
        chunk_bytes = fh.read(min(budget.chunk, rng.end - pos))
        if not chunk_bytes:
            break
        pos += len(chunk_bytes)
        scanned += len(chunk_bytes)
        lines = (carry + chunk_bytes.decode("utf-8", errors="replace")).split("\n")
        carry = lines[-1]
        yield from lines[:-1]
    if carry:
        yield carry
    result.reason = "eof" if pos >= rng.end else "cap"
