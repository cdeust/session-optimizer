"""Shared fixtures for the disk-hygiene tests. source: disk-hygiene design"""

import importlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks"


@pytest.fixture
def purgers(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delenv("DISK_HYGIENE_TRANSCRIPTS", raising=False)
    return tuple(importlib.import_module(n) for n in ("session_purge", "codex_purge"))
