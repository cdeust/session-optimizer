# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Clement Deust
"""Codex SessionStart envelope for existing Claude statusline maintenance.

Source: native Codex invalid SessionStart output, 2026-10-02.
The existing installer syncs code only when installed and preserves user budgets.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    sys.stdin.read()  # Consume the event; installer decisions do not use it.
    installer = Path(__file__).resolve().parents[1] / "install.sh"
    result = subprocess.run(
        ["bash", str(installer), "sync"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        sys.stderr.write(result.stdout)
        return result.returncode
    context = result.stdout.strip()
    output = (
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
        if context
        else {}
    )
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
