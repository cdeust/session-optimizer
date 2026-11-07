"""Every directory of shipped Python is measured by coverage.

`[tool.coverage.run] source` in pyproject.toml is a hand-kept list. A plugin
directory missing from it is neither reported nor held by the coverage floor:
`plugins/disk-hygiene/hooks`, the code that deletes files, and
`plugins/statusline/hooks` were left out (session-optimizer #61). This test
derives the set of directories holding shipped `.py` files from the tree and
requires `source` to name each of them.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# tomllib is standard from Python 3.11; older interpreters skip this module.
tomllib = pytest.importorskip("tomllib")


def _shipped_python_dirs():
    return sorted(
        {
            path.parent.relative_to(ROOT).as_posix()
            for path in (ROOT / "plugins").rglob("*.py")
            if "__pycache__" not in path.parts
        }
    )


def test_coverage_source_names_every_shipped_python_directory():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    source = set(config["tool"]["coverage"]["run"]["source"])
    shipped = _shipped_python_dirs()
    assert shipped, "no shipped Python found under plugins/"
    missing = [directory for directory in shipped if directory not in source]
    assert missing == [], f"not measured by coverage: {missing}"
