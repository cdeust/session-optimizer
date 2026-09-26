"""Global on/off switch for every cleanup action of this plugin."""

import os


def cleanup_enabled() -> bool:
    """DISK_HYGIENE_CLEANUP=off disables all hooks and mutating commands.

    Any other value than on/off is rejected so a typo never silently keeps
    or drops cleanup.
    """
    value = os.environ.get("DISK_HYGIENE_CLEANUP", "on")
    if value not in ("on", "off"):
        raise ValueError("DISK_HYGIENE_CLEANUP must be on or off")
    return value == "on"
