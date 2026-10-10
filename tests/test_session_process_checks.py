"""Process-check regression gates. source: session-optimizer issue #62."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# source: synthetic process table fixtures for issue #62; no real PID is signalled.
HOOK_PID = 42
PARENT_PID = 23


def forbid_process_call(*args, **kwargs):
    pytest.fail("unsupported platform reached a process probe")


@pytest.mark.parametrize("probe", ["alive", "host_pid"])
def test_windows_process_checks_refuse_before_any_probe(purgers, monkeypatch, probe):
    purge, _ = purgers
    monkeypatch.setattr(
        purge,
        "os",
        SimpleNamespace(
            name="nt", kill=forbid_process_call, getppid=forbid_process_call
        ),
    )
    monkeypatch.setattr(purge.subprocess, "run", forbid_process_call)
    with pytest.raises(RuntimeError, match="process checks.*unsupported on Windows"):
        getattr(purge, probe)(HOOK_PID)


@pytest.mark.parametrize("status", [1, 2])
def test_parent_lookup_rejects_failed_ps_even_with_plausible_stdout(
    purgers, monkeypatch, status
):
    purge, _ = purgers
    monkeypatch.setattr(
        purge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=status, stderr="", stdout=f"{PARENT_PID} /bin/claude"
        ),
    )
    with pytest.raises(RuntimeError, match="ps.*failed"):
        purge.host_pid(HOOK_PID)


def test_parent_lookup_reports_decoding_error(purgers, monkeypatch):
    purge, _ = purgers

    def unreadable(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid process output")

    monkeypatch.setattr(purge.subprocess, "run", unreadable)
    with pytest.raises(RuntimeError, match="ps.*decode"):
        purge.host_pid(HOOK_PID)


@pytest.mark.parametrize(
    "error", [OSError("ps unavailable"), subprocess.TimeoutExpired("ps", 5)]
)
def test_parent_lookup_reports_command_failure(purgers, monkeypatch, error):
    purge, _ = purgers

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(purge.subprocess, "run", broken)
    with pytest.raises(RuntimeError, match="ps.*failed"):
        purge.host_pid(HOOK_PID)


@pytest.mark.parametrize("output", ["", "not-a-pid /bin/sh", "23"])
def test_parent_lookup_rejects_malformed_output(purgers, monkeypatch, output):
    purge, _ = purgers
    monkeypatch.setattr(
        purge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=output),
    )
    with pytest.raises(RuntimeError, match="ps.*malformed"):
        purge.host_pid(HOOK_PID)


def test_parent_lookup_reports_stderr_on_success(purgers, monkeypatch):
    purge, _ = purgers
    monkeypatch.setattr(
        purge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stderr="unexpected diagnostic",
            stdout=f"{PARENT_PID} /bin/claude",
        ),
    )
    with pytest.raises(RuntimeError, match="ps.*unexpected diagnostic"):
        purge.host_pid(HOOK_PID)


def test_parent_lookup_walks_to_nearest_host(purgers, monkeypatch):
    purge, _ = purgers
    rows = {HOOK_PID: f"{PARENT_PID} /bin/sh", PARENT_PID: "1 /bin/claude"}
    seen = []

    def process_row(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=0, stderr="", stdout=rows[int(command[-1])])

    monkeypatch.setattr(purge.subprocess, "run", process_row)
    assert purge.host_pid(HOOK_PID) == PARENT_PID
    assert seen == [
        ["ps", "-o", "ppid=,comm=", "-p", str(pid)] for pid in (HOOK_PID, PARENT_PID)
    ]


def test_parent_lookup_without_host_reaches_init(purgers, monkeypatch):
    purge, _ = purgers
    monkeypatch.setattr(
        purge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout="1 /bin/sh"),
    )
    assert purge.host_pid(HOOK_PID) is None


def test_parent_lookup_defaults_to_parent_pid(purgers, monkeypatch):
    purge, _ = purgers
    monkeypatch.setattr(purge.os, "getppid", lambda: PARENT_PID)
    monkeypatch.setattr(
        purge.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stderr="", stdout="1 /bin/claude"
        ),
    )
    assert purge.host_pid() == PARENT_PID


@pytest.mark.parametrize(
    "error, expected", [(ProcessLookupError(), False), (PermissionError(), True)]
)
def test_posix_liveness_preserves_process_errors(purgers, monkeypatch, error, expected):
    purge, _ = purgers

    def probe(pid, signal):
        assert (pid, signal) == (HOOK_PID, 0)
        raise error

    monkeypatch.setattr(purge.os, "kill", probe)
    assert purge.alive(HOOK_PID) is expected


def test_posix_liveness_uses_signal_zero(purgers, monkeypatch):
    purge, _ = purgers
    seen = []
    monkeypatch.setattr(
        purge.os, "kill", lambda pid, signal: seen.append((pid, signal))
    )
    assert purge.alive(HOOK_PID) is True
    assert seen == [(HOOK_PID, 0)]


def test_posix_live_process(purgers):
    purge, _ = purgers
    assert purge.alive(os.getpid()) is True


# source: isolated CLI driver exercises the shipped __main__ error boundary.
CLI_DRIVER = """
import os, runpy, sys
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import session_purge as purge
if os.environ["PROCESS_TEST_MODE"] == "windows":
    purge.os = SimpleNamespace(**vars(os))
    purge.os.name = "nt"
    def forbidden(*args, **kwargs):
        raise AssertionError("Windows reached a signal probe")
    purge.os.kill = forbidden
else:
    original = purge.subprocess.run
    def failing_ps(command, **kwargs):
        if command[0] == "ps":
            return SimpleNamespace(returncode=2, stderr="probe failed",
                                   stdout="1 /bin/claude")
        return original(command, **kwargs)
    purge.subprocess.run = failing_ps
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


@pytest.mark.parametrize("mode", ["windows", "failed-ps"])
def test_hook_reports_refusal_and_retains_pending_state(tmp_path, mode):
    from test_cleanup_transcripts import SID

    scripts = Path(__file__).resolve().parents[1] / "plugins/disk-hygiene/hooks"
    home = tmp_path / "home"
    kept = home / "session-env" / SID / "file"
    kept.parent.mkdir(parents=True)
    kept.write_text("must survive a refused process check")
    state = tmp_path / "state.json"
    env = dict(
        os.environ,
        CLAUDE_CONFIG_DIR=str(home),
        CLAUDE_CODE_TMPDIR=str(tmp_path / "temp"),
        CODEX_HOME=str(tmp_path / "codex"),
        PROCESS_TEST_MODE=mode,
        DISK_HYGIENE_CLEANUP="on",
        DISK_HYGIENE_TRANSCRIPTS="keep",
    )
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            CLI_DRIVER,
            str(scripts / "disk_hygiene.py"),
            "--state",
            str(state),
            "--host",
            "claude",
            "--session",
            SID,
            "hook",
            "SessionEnd",
        ],
        env=env,
        input=json.dumps({"session_id": SID, "cwd": str(tmp_path)}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 1
    output = json.loads(run.stdout)
    expected = "unsupported on Windows" if mode == "windows" else "ps failed"
    assert expected in output["protected"]
    assert run.stderr == ""
    assert kept.read_text() == "must survive a refused process check"
    pending = json.loads(state.with_name("ended-sessions.json").read_text())
    assert SID in pending
