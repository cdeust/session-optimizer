"""Regressions for the review of the first packaging of the cleanup hooks."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/disk-hygiene"
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))

import cleanup_hooks  # noqa: E402
import cleanup_intake  # noqa: E402
import cleanup_registry  # noqa: E402
import host_cleanup  # noqa: E402
import session_purge  # noqa: E402

SID = "3824c7e3-e1e4-4a30-942d-0d65f2f78745"


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn 'git push' docs/",
        "git commit -m 'document git push flow'",
        "git push --dry-run",
        "git push -n origin main",
        "echo git push",
        "git status && echo 'gh pr create'",
        "git log --grep='git push'",
        "cat > notes.md <<'EOF'\ngit push origin main\ngh pr create\nEOF",
        "git commit -m \"$(cat <<'EOF'\nrun git push later\nEOF\n)\"",
    ],
)
def test_text_that_mentions_a_push_is_not_a_push(command):
    assert not cleanup_hooks.pushed(bash(command))


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git -C /repo push origin branch",
        "cd /repo && git push -u origin branch",
        "FOO=1 git push",
        "git -c push.default=current push",
        "gh pr create --title x --body y",
        "gh -R owner/repo pr create",
        "bash -lc 'git push origin branch'",
        "git status; git push",
    ],
)
def test_real_push_and_pr_creation_are_recognised(command):
    assert cleanup_hooks.pushed(bash(command))


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Write", "tool_input": {"content": "run git push"}},
        {"tool_name": "Edit", "tool_input": {"new_string": "gh pr create"}},
        {"tool_name": "Read", "tool_input": {"file_path": "git push"}},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": "git push"},
    ],
)
def test_non_shell_tools_and_bad_inputs_never_count(payload):
    assert not cleanup_hooks.pushed(payload)


def test_codex_and_mcp_push_forms_are_recognised():
    assert cleanup_hooks.pushed(
        {"tool_name": "exec_command", "tool_input": {"cmd": "git push"}}
    )
    assert cleanup_hooks.pushed(
        {"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "git push"]}}
    )
    assert cleanup_hooks.pushed(
        {"tool_name": "mcp__github__create_pull_request", "tool_input": {}}
    )


def test_false_positive_does_not_purge_the_live_session(tmp_path, monkeypatch):
    home = tmp_path / ".claude"
    scratch = home / f"session-env/{SID}/env"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("live")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "tmp"))
    args = SimpleNamespace(
        host="claude", session=SID, event="PostToolUse", state=str(tmp_path / "s.json")
    )
    payload = {"tool_name": "Write", "tool_input": {"content": "git push"}, "cwd": "."}
    assert host_cleanup.after_hook(args, payload, []) == []
    assert scratch.read_text() == "live"


def holder(lock_path, seconds):
    """Hold the ledger lock from another thread's file descriptor."""
    handle = open(lock_path, "a+")
    fcntl.flock(handle, fcntl.LOCK_EX)

    def release():
        time.sleep(seconds)
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    thread = threading.Thread(target=release)
    thread.start()
    return thread


def test_registry_waits_for_a_concurrent_holder(tmp_path):
    ledger = tmp_path / "ledger.json"
    thread = holder(str(ledger) + ".lock", 0.4)
    started = time.monotonic()
    with cleanup_registry.registry(ledger) as state:
        state["_ended"] = {}
    thread.join()
    assert time.monotonic() - started >= 0.3
    assert json.loads(ledger.read_text()) == {"_ended": {}}


def test_registry_gives_up_with_a_protected_error_after_the_bound(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cleanup_registry, "LOCK_WAIT_SECONDS", 0.2)
    ledger = tmp_path / "ledger.json"
    thread = holder(str(ledger) + ".lock", 0.8)
    with pytest.raises(cleanup_registry.Protected, match="ledger busy"):
        with cleanup_registry.registry(ledger):
            pass
    thread.join()


def test_unchanged_ledger_is_not_rewritten(tmp_path):
    ledger = tmp_path / "ledger.json"
    with cleanup_registry.registry(ledger) as state:
        state["_ended"] = {"claude:x": True}
    before = ledger.stat().st_mtime_ns
    with cleanup_registry.registry(ledger):
        pass
    assert ledger.stat().st_mtime_ns == before


def run_cli(tmp_path, *argv, stdin="", extra=None):
    base = {k: v for k, v in os.environ.items() if k != "DISK_HYGIENE_CLEANUP"}
    env = {
        **base,
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "CLAUDE_CODE_TMPDIR": str(tmp_path / "tmp"),
        "DISK_HYGIENE_TRANSCRIPTS": "keep",
        **(extra or {}),
    }
    return subprocess.run(
        [sys.executable, "-B", str(HOOKS / "disk_hygiene.py"), *argv],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
    )


def test_ordinary_tool_call_takes_no_ledger_lock_and_writes_nothing(tmp_path):
    state = tmp_path / "ledger.json"
    thread = holder(str(state) + ".lock", 1.5)
    started = time.monotonic()
    result = run_cli(
        tmp_path,
        "--state",
        str(state),
        "--host",
        "claude",
        "--session",
        SID,
        "hook",
        "PostToolUse",
        stdin=json.dumps(bash("ls -la")),
    )
    elapsed = time.monotonic() - started
    thread.join()
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == []
    assert elapsed < 1.4
    assert not state.exists()


def test_global_switch_off_disables_hooks_and_commands(tmp_path):
    state = tmp_path / "ledger.json"
    off = {"DISK_HYGIENE_CLEANUP": "off"}
    end = run_cli(
        tmp_path,
        "--state",
        str(state),
        "--host",
        "claude",
        "--session",
        SID,
        "hook",
        "SessionEnd",
        stdin=json.dumps({"session_id": SID, "cwd": str(tmp_path)}),
        extra=off,
    )
    assert end.returncode == 0
    assert "disabled" in json.loads(end.stdout)
    assert not Path(str(state) + ".ended").exists()
    register = run_cli(
        tmp_path,
        "--state",
        str(state),
        "--host",
        "claude",
        "--session",
        SID,
        "register-worktree",
        "--repo",
        str(tmp_path),
        "--path",
        str(tmp_path / "w"),
        extra=off,
    )
    assert register.returncode == 0
    assert "disabled" in json.loads(register.stdout)
    assert not state.exists()


def test_global_switch_typo_is_rejected(tmp_path):
    result = run_cli(
        tmp_path,
        "--host",
        "claude",
        "--session",
        SID,
        "status",
        extra={"DISK_HYGIENE_CLEANUP": "of"},
    )
    assert result.returncode == 1
    assert "DISK_HYGIENE_CLEANUP must be on or off" in result.stdout


def test_checkpoint_follows_the_transcript_policy(tmp_path, monkeypatch):
    home, root = tmp_path / ".claude", tmp_path / "tmp/claude-x"
    checkpoint = home / f"memories/checkpoints/{SID}.md"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("resume material")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    guard = lambda path: None  # noqa: E731
    monkeypatch.setenv("DISK_HYGIENE_TRANSCRIPTS", "keep")
    session_purge.purge_ended((home, root), SID, guard, wait=lambda: True)
    assert checkpoint.read_text() == "resume material"
    monkeypatch.setenv("DISK_HYGIENE_TRANSCRIPTS", "delete")
    session_purge.purge_ended((home, root), SID, guard, wait=lambda: True)
    assert not checkpoint.exists()


def test_symlinked_config_directory_is_still_cleaned(tmp_path, monkeypatch):
    real = tmp_path / "dotfiles"
    scratch = real / f"session-env/{SID}/env"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("x")
    link = tmp_path / ".claude"
    link.symlink_to(real)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link))
    monkeypatch.setenv("DISK_HYGIENE_TRANSCRIPTS", "keep")
    home = session_purge.claude_home()
    results = session_purge.purge_ended(
        (home, tmp_path / "tmp"), SID, lambda path: None, wait=lambda: True
    )
    assert not scratch.exists()
    assert not any("symlink parent" in r.get("protected", "") for r in results)


def event(host, session, roots):
    return {"host": host, "session": session, "roots": roots}


def test_corrupt_or_foreign_intake_does_not_block_other_sessions(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("DISK_HYGIENE_TRANSCRIPTS", "keep")
    state = str(tmp_path / "ledger.json")
    folder = cleanup_intake.directory(state, "host")
    folder.mkdir(parents=True)
    (folder / "a-corrupt.json").write_text("{not json")
    (folder / "b-invalid.json").write_text(json.dumps({"host": "vim"}))
    (folder / "c-foreign.json").write_text(
        json.dumps(event("claude", SID, {"claude_home": "/elsewhere"}))
    )
    valid = event("claude", SID, cleanup_intake.roots())
    valid["session"] = "4824c7e3-e1e4-4a30-942d-0d65f2f78745"
    (folder / "d-valid.json").write_text(json.dumps(valid))

    events = cleanup_intake.read_pending(state, "host")
    assert [path.name for path, _ in events] == [
        "c-foreign.json",
        "d-valid.json",
    ]
    assert "a-corrupt.json" in capsys.readouterr().err

    args = SimpleNamespace(host="claude", session=SID, event="PostToolUse", state=state)
    host_cleanup.import_ended(args)
    ledger = json.loads((tmp_path / "ended-sessions.json").read_text())
    assert valid["session"] in ledger
    assert (folder / "c-foreign.json").exists()
    assert not (folder / "d-valid.json").exists()


def test_claude_matcher_only_selects_shell_and_pr_creation_tools():
    import re

    hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    matcher = hooks["PostToolUse"][0]["matcher"]
    for name in ("Bash", "mcp__github__create_pull_request"):
        assert re.fullmatch(matcher, name)
    for name in ("Write", "Edit", "Read", "mcp__github__get_file_contents"):
        assert not re.fullmatch(matcher, name)


def test_codex_session_end_stays_within_its_three_second_budget():
    hooks = json.loads((HOOKS / "codex-hooks.json").read_text())["hooks"]
    assert hooks["SessionEnd"][0]["hooks"][0]["timeout"] <= 3


def test_manifests_and_marketplaces_agree_on_the_plugin():
    claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    assert claude["name"] == codex["name"] == "disk-hygiene"
    assert claude["version"] == codex["version"]
    assert (PLUGIN / codex["hooks"]).is_file()
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "disk-hygiene")
    assert entry["source"] == "./plugins/disk-hygiene"
    assert entry["version"] == claude["version"]
    agents = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    codex_entry = next(p for p in agents["plugins"] if p["name"] == "disk-hygiene")
    assert (ROOT / codex_entry["source"]["path"]).resolve() == PLUGIN.resolve()


def test_hook_commands_point_at_the_shipped_entry_point():
    for name, root in (
        ("hooks.json", "CLAUDE_PLUGIN_ROOT"),
        ("codex-hooks.json", "PLUGIN_ROOT"),
    ):
        hooks = json.loads((HOOKS / name).read_text())["hooks"]
        assert set(hooks) == {"SessionStart", "PostToolUse", "Stop", "SessionEnd"}
        for groups in hooks.values():
            for group in groups:
                for hook in group["hooks"]:
                    assert "${" + root + "}/hooks/disk_hygiene.py" in hook["command"]
    assert (HOOKS / "disk_hygiene.py").is_file()
