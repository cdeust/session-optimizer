# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Clement Deust
"""Codex statusline maintenance emits typed startup output, preserving failures.

Source: native Codex invalid SessionStart output, 2026-10-02.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/statusline/hooks/codex_session_start.py"


@pytest.mark.parametrize("output", ["", "[statusline] updated costs.sh\n"])
def test_success_emits_codex_session_envelope(monkeypatch, capsys, output):
    spec = importlib.util.spec_from_file_location("statusline_codex_hook", SCRIPT)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(hook.subprocess, "run", run)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("{}"))
    assert hook.main() == 0
    actual = json.loads(capsys.readouterr().out)
    expected = (
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": output.strip(),
            }
        }
        if output
        else {}
    )
    assert actual == expected
    assert calls[0][0] == ["bash", str(ROOT / "plugins/statusline/install.sh"), "sync"]
    assert calls[0][1]["stdin"] == hook.subprocess.DEVNULL


def test_installer_failure_is_preserved(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "statusline_codex_hook_failure", SCRIPT
    )
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(
        hook.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="blocked\n", stderr="diagnostic\n"
        ),
    )
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO("{}"))
    assert hook.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "diagnostic\nblocked\n"


def test_codex_uses_separate_hook_and_claude_installer_stays_direct():
    plugin = ROOT / "plugins/statusline"
    codex = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
    hooks = json.loads((plugin / codex["hooks"]).read_text())
    command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command == 'python3 "${PLUGIN_ROOT}/hooks/codex_session_start.py"'
    claude = json.loads((plugin / "hooks/hooks.json").read_text())
    assert (
        claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        == 'bash "${CLAUDE_PLUGIN_ROOT}/install.sh" sync'
    )


def test_codex_marketplace_exposes_statusline_sync():
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    entry = next(
        plugin for plugin in marketplace["plugins"] if plugin["name"] == "statusline"
    )
    plugin = ROOT / entry["source"]["path"]
    assert (plugin / ".codex-plugin/plugin.json").is_file()
