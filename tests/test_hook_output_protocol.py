"""Codex output regression. Source: https://learn.chatgpt.com/docs/hooks"""

import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "host,command,event,result,expected",
    [
        ("codex", "hook", "PostToolUse", [], {}),
        ("codex", "hook", "Stop", [], {}),
        (
            "codex",
            "hook",
            "PostToolUse",
            [{"protected": "active process"}],
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": '[{"protected": "active process"}]',
                }
            },
        ),
        (
            "codex",
            "hook",
            "Stop",
            [{"removed": "owned path"}],
            {"systemMessage": '[{"removed": "owned path"}]'},
        ),
        (
            "codex",
            "hook",
            "Stop",
            {"decision": "block", "reason": "pending"},
            {"decision": "block", "reason": "pending"},
        ),
        ("claude", "hook", "PostToolUse", [], []),
        ("codex", "status", None, [], []),
    ],
)
def test_main_emits_host_protocol(
    monkeypatch, capsys, host, command, event, result, expected
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks")
    )
    module = importlib.import_module("disk_hygiene")
    args = SimpleNamespace(
        host=host, command=command, event=event, session="test-session"
    )
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "cleanup_enabled", lambda: True)
    monkeypatch.setattr(module.transcript_policy, "delete_enabled", lambda: False)
    monkeypatch.setattr(module, "run_command", lambda *args: result)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO("{}"))
    module.main()
    assert json.loads(capsys.readouterr().out) == expected
