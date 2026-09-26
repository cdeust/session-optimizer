"""Transcript deletion requires explicit owner configuration. source: disk-hygiene design"""

import os


def delete_enabled() -> bool:
    """Reject typos before any cleanup rather than silently changing retention."""
    value = os.environ.get("DISK_HYGIENE_TRANSCRIPTS", "keep")
    if value not in ("keep", "delete"):
        raise ValueError("DISK_HYGIENE_TRANSCRIPTS must be keep or delete")
    return value == "delete"
