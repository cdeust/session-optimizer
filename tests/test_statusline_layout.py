"""On-disk layout contracts for the statusline plugin (issue #33).

Everything the plugin leaves on disk lives under ``~/.claude/statusline/``:
the code at the top, the runtime state under ``state/``. The only file it
keeps at the root of ``~/.claude`` is ``ctxguard-thresholds.json``, which the
context-guard plugin reads from that exact path as well
(``plugins/context-guard/hooks/stop-context-guard.py``).

Every test runs the shipped scripts as subprocesses under a temporary HOME,
never against the real ``~/.claude``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "statusline"
INSTALLER = PLUGIN / "install.sh"
ASSETS = PLUGIN / "assets"
CONTEXT_GUARD_HOOK = ROOT / "plugins" / "context-guard" / "hooks" / "stop-context-guard.py"

CODE_FILES = ("statusline-command.sh", "costs.sh", "pricing.json", "transcript.py")
MODULES = (
    "platform", "palette", "fit", "severity", "format",
    "config", "gitctx", "session_state", "layout", "render",
    "pricing", "ledger_report",
)
# What Claude Code itself keeps at the root of ~/.claude in the fixture. The
# migration must not touch any of it.
CLAUDE_OWNED = {"settings.json", "projects", "plugins"}
# The one statusline file that stays at the root, because context-guard reads
# it there too (see module docstring).
SHARED = {"ctxguard-thresholds.json"}
BACKUP_KEEP = 3
SESSION = "0072d0e1-0264-4b5a-8367-61e1a4cd9fbc"
OTHER_SESSIONS = ("006ab18f-47a8-43e8-a9e0-8573c3c32c1e", "00b10a42-2ad8-4c63-a7ff-9e057a2fb530")


def _env(home: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("STATUSLINE_")}
    env.pop("COST_LOG_RETENTION_DAYS_OVERRIDE", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(home)
    env["COLUMNS"] = "200"
    env.update(extra)
    return env


def _installer(home: Path, verb: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER), verb], env=_env(home, **extra),
        capture_output=True, text=True, cwd=str(home),
    )


def _costs(home: Path, verb: str, stdin: str = "", **extra: str) -> subprocess.CompletedProcess:
    # capture_output holds the stdout pipe open until every process that
    # inherited it has exited, the backgrounded cleaner included. That EOF is
    # what makes the post-conditions below deterministic: no clock, no polling.
    return subprocess.run(
        ["bash", str(home / ".claude" / "statusline" / "costs.sh"), verb],
        input=stdin, env=_env(home, **extra), capture_output=True, text=True, cwd=str(home),
    )


def _status_json(home: Path, transcript: Path) -> str:
    return json.dumps({
        "session_id": SESSION,
        "transcript_path": str(transcript),
        "model": {"display_name": "Opus 4.8"},
        "workspace": {"current_dir": str(home)},
        "context_window": {"used_percentage": 20, "total_input_tokens": 200000},
        "cost": {"total_cost_usd": 1.5, "total_duration_ms": 1000},
    })


def _transcript(home: Path, session: str = SESSION) -> Path:
    """A synthetic session transcript with one priced subagent."""
    project = home / ".claude" / "projects" / "-tmp-project"
    project.mkdir(parents=True, exist_ok=True)
    line = json.dumps({
        "type": "assistant", "requestId": "req-1",
        "message": {"id": "msg-1", "model": "claude-opus-4-8",
                    "usage": {"input_tokens": 1000, "output_tokens": 500}},
    })
    main = project / f"{session}.jsonl"
    main.write_text(line + "\n")
    sub = project / session / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(line.replace("msg-1", "msg-2") + "\n")
    return main


def _tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
    return out


def _flat_fixture(home: Path) -> Path:
    """The pre-#33 layout as measured on 2026-09-10: every runtime file flat at
    the root of ~/.claude, backups from the prose hook beside them."""
    claude = home / ".claude"
    claude.mkdir()
    (claude / "projects").mkdir()
    (claude / "plugins").mkdir()
    (claude / "settings.json").write_text(json.dumps({
        "model": "opus",
        "statusLine": {"type": "command",
                       "command": f"bash {claude}/statusline-command.sh",
                       "padding": 1, "refreshInterval": 10},
    }, indent=2) + "\n")
    _flat_code(claude)
    _flat_state(claude)
    return claude


def _flat_code(claude: Path) -> None:
    """Old code, one module out of date, a stale backup inside the module dir,
    the prose hook's *.bak.* copies, and the user-tuned config files."""
    (claude / "statusline-command.sh").write_text("#!/usr/bin/env bash\necho old renderer\n")
    (claude / "costs.sh").write_text("#!/usr/bin/env bash\necho old ledger\n")
    (claude / "pricing.json").write_text('{"models": []}\n')
    (claude / "statusline-transcript.py").write_text("print('old')\n")
    (claude / "README-statusline.md").write_text("old readme\n")
    (claude / "statusline-costs.py").write_text("print('superseded')\n")
    lib = claude / "statusline-lib"
    lib.mkdir()
    for module in MODULES:
        (lib / f"{module}.sh").write_text((ASSETS / "lib" / f"{module}.sh").read_text())
    (lib / "format.sh").write_text("# out of date\n")
    (lib / "format.sh.bak.20260803231416").write_text("# older still\n")
    (claude / "costs.sh.bak.20260803231416").write_text("echo even older\n")
    (claude / "statusline-command.sh.bak.20260803231416").write_text("echo even older\n")
    (claude / "statusline-budget.json").write_text('{"size": "xl", "cache_ttl_min": 60}\n')
    (claude / "ctxguard-thresholds.json").write_text(
        '{"models": [{"match": "opus", "warn": 1, "hard": 2}], "default": {"warn": 3, "hard": 4}}\n')


def _flat_state(claude: Path) -> None:
    """Runtime state: ledger, lock, stamp, orphan tmp, fragments, hidden caches."""
    ledger = claude / "statusline-costs.jsonl"
    ledger.write_text(json.dumps({"session_id": SESSION, "date": "2026-09-10",
                                  "cost_at_day_start": 0, "cost_usd": 1.25,
                                  "month": "2026-09", "month_day_start": 0}) + "\n")
    (claude / "statusline-costs.jsonl.lock").mkdir()
    (claude / "statusline-costs.jsonl.cleanup-stamp").write_text("")
    (claude / "statusline-costs.jsonl.tmp.4242").write_text("orphan\n")
    (claude / "statusline-costs.jsonl.bak.20260801-120000").write_text("seed backup\n")
    for sid in (SESSION, *OTHER_SESSIONS):
        (claude / f"statusline-costs.jsonl.main.{sid}").write_text(f"1700000000 0.5 1700000001 {sid}\n")
    for sid in OTHER_SESSIONS:
        (claude / f"statusline-costs.jsonl.sub.{sid}").write_text(f"1700000000 0.1 1700000001 {sid}\n")
    (claude / ".statusline-cost-cache.json").write_text("{}\n")
    (claude / ".statusline-transcript-cache.json").write_text('{"path": "/x", "compactions": 3}\n')
    (claude / ".statusline-transcript.lock").write_text("")


# --- Migration ---------------------------------------------------------------

def test_migration_leaves_the_root_to_claude_code_and_places_the_code(tmp_path):
    claude = _flat_fixture(tmp_path)
    old_budget = (claude / "statusline-budget.json").read_text()
    old_thresholds = (claude / "ctxguard-thresholds.json").read_text()

    result = _installer(tmp_path, "install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "migrated" in result.stdout

    # The root holds only what Claude Code owns, the shared file, and one dir.
    assert {p.name for p in claude.iterdir()} == CLAUDE_OWNED | SHARED | {"statusline"}

    target = claude / "statusline"
    for name in CODE_FILES:
        assert (target / name).read_bytes() == (ASSETS / name).read_bytes(), name
    assert os.access(target / "statusline-command.sh", os.X_OK)
    assert os.access(target / "costs.sh", os.X_OK)
    assert {p.name for p in (target / "lib").iterdir()} == {f"{m}.sh" for m in MODULES}
    assert (target / "lib" / "format.sh").read_bytes() == (ASSETS / "lib" / "format.sh").read_bytes()
    assert (target / "README.md").read_bytes() == (PLUGIN / "README.md").read_bytes()

    # User-tuned files moved (budget) or stayed (shared thresholds), byte for byte.
    assert (target / "statusline-budget.json").read_text() == old_budget
    assert (claude / "ctxguard-thresholds.json").read_text() == old_thresholds

    # settings.json points at the new renderer; every other field survives.
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["model"] == "opus"
    assert settings["statusLine"] == {
        "type": "command",
        "command": f"bash {target}/statusline-command.sh",
        "padding": 1, "refreshInterval": 10,
    }


def test_migration_moves_runtime_state_and_backs_up_what_it_supersedes(tmp_path):
    claude = _flat_fixture(tmp_path)
    old_ledger = (claude / "statusline-costs.jsonl").read_text()
    old_cache = (claude / ".statusline-transcript-cache.json").read_text()

    result = _installer(tmp_path, "install")
    assert result.returncode == 0, result.stdout + result.stderr

    # Runtime state moved under state/, fragments into their own directory,
    # named by session id so the directory reads at a glance.
    state = claude / "statusline" / "state"
    assert (state / "costs.jsonl").read_text() == old_ledger
    assert (state / "transcript-cache.json").read_text() == old_cache
    assert not (state / "costs.jsonl.lock").exists()
    assert not list(state.glob("costs.jsonl.tmp.*"))
    fragments = {p.name for p in (state / "sessions").iterdir()}
    assert fragments == {f"{sid}.main" for sid in (SESSION, *OTHER_SESSIONS)} | {
        f"{sid}.sub" for sid in OTHER_SESSIONS}
    assert (state / "sessions" / f"{SESSION}.main").read_text().rstrip().endswith(SESSION)

    # The superseded code went to ONE backup run under state/backup, and no
    # *.bak.* file survives anywhere else.
    runs = sorted(p for p in (state / "backup").iterdir() if p.is_dir())
    assert len(runs) == 1
    backed_up = {str(p.relative_to(runs[0])) for p in runs[0].rglob("*") if p.is_file()}
    assert "statusline-command.sh" in backed_up
    assert "statusline-costs.py" in backed_up
    assert "statusline-lib/format.sh.bak.20260803231416" in backed_up
    assert "costs.sh.bak.20260803231416" in backed_up
    assert "statusline-costs.jsonl.bak.20260801-120000" in backed_up
    assert ".statusline-cost-cache.json" in backed_up
    stray = [p for p in claude.rglob("*.bak.*") if state / "backup" not in p.parents]
    assert stray == []


def test_installer_seeds_a_fresh_home_without_touching_the_root(tmp_path):
    result = _installer(tmp_path, "install")
    assert result.returncode == 0, result.stdout + result.stderr
    claude = tmp_path / ".claude"
    assert {p.name for p in claude.iterdir()} == {"settings.json", "statusline"} | SHARED
    target = claude / "statusline"
    assert (target / "statusline-budget.json").read_bytes() == (ASSETS / "statusline-budget.json").read_bytes()
    assert (claude / "ctxguard-thresholds.json").read_bytes() == (ASSETS / "ctxguard-thresholds.json").read_bytes()
    assert (target / "state" / "sessions").is_dir()
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["statusLine"]["command"] == f"bash {target}/statusline-command.sh"
    assert settings["statusLine"]["refreshInterval"] == 10


def test_sync_is_idempotent(tmp_path):
    _flat_fixture(tmp_path)
    assert _installer(tmp_path, "install").returncode == 0
    before = _tree(tmp_path / ".claude")

    result = _installer(tmp_path, "sync")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
    assert _tree(tmp_path / ".claude") == before


def test_sync_does_nothing_where_nothing_is_installed(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}\n")
    result = _installer(tmp_path, "sync")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
    assert {p.name for p in (tmp_path / ".claude").iterdir()} == {"settings.json"}
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == {}


def test_sync_updates_changed_code_with_a_bounded_backup_history(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    target = tmp_path / ".claude" / "statusline"
    for i in range(BACKUP_KEEP + 3):
        (target / "costs.sh").write_text(f"#!/usr/bin/env bash\necho drifted {i}\n")
        (target / "lib" / "obsolete.sh").write_text("# not shipped any more\n")
        result = _installer(tmp_path, "sync")
        assert result.returncode == 0, result.stderr
        assert "costs.sh" in result.stdout
    assert (target / "costs.sh").read_bytes() == (ASSETS / "costs.sh").read_bytes()
    assert os.access(target / "costs.sh", os.X_OK)
    assert not (target / "lib" / "obsolete.sh").exists()
    runs = [p for p in (target / "state" / "backup").iterdir() if p.is_dir()]
    assert len(runs) == BACKUP_KEEP
    # Config is never part of a sync, even when it differs from the bundle.
    (target / "statusline-budget.json").write_text('{"size": "xs"}\n')
    assert _installer(tmp_path, "sync").returncode == 0
    assert (target / "statusline-budget.json").read_text() == '{"size": "xs"}\n'


def test_sync_rewires_a_settings_command_that_still_names_the_old_path(tmp_path):
    _flat_fixture(tmp_path)
    assert _installer(tmp_path, "install").returncode == 0
    claude = tmp_path / ".claude"
    settings = json.loads((claude / "settings.json").read_text())
    settings["statusLine"]["command"] = f"bash {claude}/statusline-command.sh"
    settings["statusLine"]["padding"] = 0
    (claude / "settings.json").write_text(json.dumps(settings) + "\n")
    result = _installer(tmp_path, "sync")
    assert result.returncode == 0, result.stderr
    rewired = json.loads((claude / "settings.json").read_text())
    assert rewired["statusLine"]["command"] == f"bash {claude}/statusline/statusline-command.sh"
    assert rewired["statusLine"]["padding"] == 0


def test_sync_refuses_to_rewrite_an_unparseable_settings_file(tmp_path):
    _flat_fixture(tmp_path)
    (tmp_path / ".claude" / "settings.json").write_text("{not json\n")
    result = _installer(tmp_path, "sync")
    assert result.returncode == 0
    assert (tmp_path / ".claude" / "settings.json").read_text() == "{not json\n"
    assert "settings.json" in result.stdout


# --- Runtime paths -----------------------------------------------------------

def test_costs_update_writes_its_fragments_under_state_and_nothing_at_the_root(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    claude = tmp_path / ".claude"
    transcript = _transcript(tmp_path)
    root_before = {p.name for p in claude.iterdir()}

    result = _costs(tmp_path, "update", _status_json(tmp_path, transcript))
    assert result.returncode == 0, result.stderr

    state = claude / "statusline" / "state"
    rows = [json.loads(line) for line in (state / "costs.jsonl").read_text().splitlines()]
    assert [r["session_id"] for r in rows] == [SESSION]
    assert rows[0]["cost_usd"] > 0
    assert {p.name for p in (state / "sessions").iterdir()} == {f"{SESSION}.main", f"{SESSION}.sub"}
    assert {p.name for p in state.iterdir()} == {
        "costs.jsonl", "costs.jsonl.cleanup-stamp", "sessions", "backup"}
    assert {p.name for p in claude.iterdir()} == root_before
    assert _costs(tmp_path, "today").stdout.strip() != "0"


def test_cost_log_override_relocates_the_ledger_and_its_fragments_together(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    transcript = _transcript(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "ledger.jsonl"
    result = _costs(tmp_path, "update", _status_json(tmp_path, transcript),
                    STATUSLINE_COST_LOG=str(elsewhere))
    assert result.returncode == 0, result.stderr
    assert elsewhere.is_file()
    assert {p.name for p in (elsewhere.parent / "sessions").iterdir()} == {
        f"{SESSION}.main", f"{SESSION}.sub"}
    state = tmp_path / ".claude" / "statusline" / "state"
    assert not (state / "costs.jsonl").exists()
    assert list((state / "sessions").iterdir()) == []


def test_cleanup_still_expires_fragments_past_retention(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    transcript = _transcript(tmp_path)
    sessions = tmp_path / ".claude" / "statusline" / "state" / "sessions"
    expired = sessions / "11111111-1111-1111-1111-111111111111.main"
    fresh = sessions / "22222222-2222-2222-2222-222222222222.sub"
    for path in (expired, fresh):
        path.write_text("1700000000 0.5 1700000001\n")
    forty_days_ago = time.time() - 40 * 86400
    os.utime(expired, (forty_days_ago, forty_days_ago))

    result = _costs(tmp_path, "update", _status_json(tmp_path, transcript))
    assert result.returncode == 0, result.stderr
    assert not expired.exists()
    assert fresh.exists()
    assert (sessions.parent / "costs.jsonl.cleanup-stamp").exists()


def test_renderer_runs_from_the_installed_layout_and_writes_state_only(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    claude = tmp_path / ".claude"
    transcript = _transcript(tmp_path)
    root_before = {p.name for p in claude.iterdir()}
    renderer = claude / "statusline" / "statusline-command.sh"

    result = subprocess.run(
        ["bash", str(renderer)], input=_status_json(tmp_path, transcript),
        env=_env(tmp_path), capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "model" in result.stdout
    state = claude / "statusline" / "state"
    assert (state / "costs.jsonl").is_file()
    cache = json.loads((state / "transcript-cache.json").read_text())
    assert cache["path"] == str(transcript)
    assert {p.name for p in claude.iterdir()} == root_before


def test_verify_reports_the_installed_layout(tmp_path):
    assert _installer(tmp_path, "install").returncode == 0
    result = _installer(tmp_path, "verify")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ERROR" not in result.stdout
    (tmp_path / ".claude" / "statusline" / "lib" / "render.sh").unlink()
    broken = _installer(tmp_path, "verify")
    assert broken.returncode != 0
    assert "render.sh" in broken.stdout


# --- Contracts pinned in the shipped files -----------------------------------

def test_session_start_hook_runs_the_deterministic_sync():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    (entry,) = hooks["hooks"]["SessionStart"]
    assert entry["matcher"] == "startup|resume"
    (hook,) = entry["hooks"]
    assert hook["type"] == "command"
    assert hook["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/install.sh" sync'
    assert "Read" not in hook["command"]


def test_shared_thresholds_file_stays_where_both_plugins_read_it():
    config = (ASSETS / "lib" / "config.sh").read_text()
    guard = CONTEXT_GUARD_HOOK.read_text()
    assert 'CTXGUARD_CONFIG="${HOME}/.claude/ctxguard-thresholds.json"' in config
    assert '".claude", "ctxguard-thresholds.json"' in guard
    installer = INSTALLER.read_text()
    assert "ctxguard-thresholds.json" in installer


@pytest.mark.parametrize("name", ["costs.sh", "statusline-command.sh", "transcript.py",
                                  "lib/session_state.sh", "lib/config.sh"])
def test_shipped_scripts_name_no_root_level_runtime_file(name):
    text = (ASSETS / name).read_text()
    for legacy in ("/.claude/statusline-costs", "/.claude/costs.sh", "/.claude/statusline-lib",
                   "/.claude/statusline-transcript", "/.claude/.statusline-", "/.claude/pricing.json",
                   "/.claude/statusline-budget.json"):
        assert legacy not in text, f"{name} still names {legacy}"
