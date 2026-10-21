"""The shipped Python runs under the host's bare `python3`, which can be 3.9.

Every plugins/*/hooks/hooks.json starts its hooks with a bare `python3`, and on
macOS that resolves to /usr/bin/python3, which is Python 3.9.6 (the Command
Line Tools build). A module whose top level evaluates 3.10-only syntax, such as
a `float | None` annotation without `from __future__ import annotations`,
raises at import time there and the hook dies. This test imports every shipped
module with a real 3.9 interpreter; ruff's `target-version = "py39"` in
pyproject.toml is the static half of the same floor.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SHIPPED = sorted(
    [
        *ROOT.glob("plugins/*/hooks/*.py"),
        *ROOT.glob("plugins/*/tools/*.py"),
        *ROOT.glob("plugins/statusline/assets/*.py"),
    ]
)

# Runs inside the 3.9 interpreter: import each file under a private module
# name (so `if __name__ == "__main__"` blocks stay inert), with its directory
# first on sys.path as it is when the hook runs, and print one line per failure.
IMPORTER = """
import importlib.util, sys, traceback
failed = 0
for path in sys.argv[1:]:
    directory = path.rsplit("/", 1)[0]
    sys.path.insert(0, directory)
    try:
        spec = importlib.util.spec_from_file_location("_py39_floor_probe", path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    except BaseException:
        failed += 1
        last = traceback.format_exc().strip().splitlines()[-1]
        print("FAIL " + path + ": " + last)
    finally:
        sys.path.remove(directory)
        sys.modules.pop("_py39_floor_probe", None)
sys.exit(1 if failed else 0)
"""


def _python39():
    """A Python < 3.10 to import with: /usr/bin/python3 first (what macOS
    hooks run), then any python3.9 on PATH. None when there is none."""
    candidates = ["/usr/bin/python3", shutil.which("python3.9")]
    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        probe = subprocess.run(
            [exe, "-c", "import sys; print(sys.version_info < (3, 10))"],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "True":
            return exe
    return None


def test_shipped_modules_import_under_python39():
    exe = _python39()
    if exe is None:
        pytest.skip(
            "no Python < 3.10 interpreter (/usr/bin/python3 or python3.9) "
            "on this machine; the 3.9 import floor cannot be exercised here"
        )
    assert SHIPPED, "no shipped modules found"
    proc = subprocess.run(
        [exe, "-B", "-c", IMPORTER, *map(str, SHIPPED)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"{exe}:\n{proc.stdout}{proc.stderr}"
