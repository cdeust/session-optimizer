#!/usr/bin/env python3
"""Generate a deterministic-file inventory as a CycloneDX 1.5 SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(roots: list[str]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        path = Path(root)
        if path.is_dir():
            found.update(item for item in path.rglob("*") if item.is_file())
        elif path.is_file():
            found.add(path)
    return sorted(found)


def build_sbom(version: str, roots: list[str]) -> dict:
    components = [
        {
            "type": "file",
            "name": str(path),
            "version": version,
            "hashes": [{"alg": "SHA-256", "content": _sha256(path)}],
        }
        for path in _iter_files(roots)
    ]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "type": "application",
                "name": "session-optimizer",
                "version": version,
            },
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("roots", nargs="+")
    args = parser.parse_args(argv)
    document = build_sbom(args.version, args.roots)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
