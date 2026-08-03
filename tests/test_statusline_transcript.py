"""Behavioral tests for the statusline transcript cache builder."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugins" / "statusline" / "assets" / "statusline-transcript.py"
SPEC = importlib.util.spec_from_file_location("session_optimizer_statusline_transcript", SCRIPT)
telemetry = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(telemetry)


def _record(kind, timestamp, *, output=0, model="opus", **extra):
    record = {"type": kind, "timestamp": timestamp, **extra}
    if kind == "assistant":
        record["message"] = {
            "role": "assistant", "model": model,
            "usage": {"output_tokens": output},
        }
    return record


def _jsonl(path: Path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_epoch_and_compaction_detection():
    assert telemetry._epoch("2026-01-01T00:00:00Z") is not None
    assert telemetry._epoch(None) is None
    assert telemetry._epoch("bad") is None
    assert telemetry._is_compaction(json.dumps({"isCompactSummary": True}))
    assert telemetry._is_compaction(json.dumps({"type": "summary"}))
    assert telemetry._is_compaction(json.dumps({"subtype": "compact"}))
    assert telemetry._is_compaction(json.dumps({"message": {"subtype": "compact"}}))
    assert not telemetry._is_compaction('{"text":"type summary"}')
    assert not telemetry._is_compaction('"type":"summary" not-json')


def test_count_tail_and_last_turn(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    records = [
        _record("user", "2026-01-01T00:00:00Z"),
        _record("assistant", "2026-01-01T00:00:10Z", output=50, model="sonnet"),
        {"type": "summary", "timestamp": "2026-01-01T00:00:11Z"},
    ]
    _jsonl(path, records)
    assert telemetry._count_compactions(str(path), 0) == 1
    assert telemetry._count_compactions(str(tmp_path / "missing"), 0) == 0
    parsed = telemetry._tail_records(str(path), path.stat().st_size)
    last_ts, rate, model = telemetry._last_turn(parsed)
    assert last_ts is not None and rate == 5.0 and model == "sonnet"
    assert telemetry._last_turn([]) == (None, None, None)
    assert telemetry._tail_records(str(tmp_path / "missing"), 1) == []

    # Exercise the partial-leading-line branch used for large transcripts.
    monkeypatch.setattr(telemetry, "TAIL_WINDOW", 256)
    path.write_text("x" * 200 + "\n" + json.dumps(records[1]) + "\n")
    assert telemetry._tail_records(str(path), path.stat().st_size)[-1]["asst"] is True


def test_last_turn_degenerate_and_model_fallback():
    records = [
        {"ts": 10.0, "asst": False, "out": 0, "model": None},
        {"ts": 10.0, "asst": True, "out": 20, "model": None},
        {"ts": 12.0, "asst": True, "out": 0, "model": "opus"},
    ]
    assert telemetry._last_turn(records) == (12.0, None, "opus")


def test_cache_load_and_build_incremental_paths(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(telemetry, "CACHE_PATH", str(cache))
    assert telemetry._load_cache() == {}
    cache.write_text("bad")
    assert telemetry._load_cache() == {}

    path = tmp_path / "session.jsonl"
    assert telemetry.build(str(path)) == {}
    path.write_text("")
    assert telemetry.build(str(path)) == {}
    _jsonl(path, [
        _record("user", "2026-01-01T00:00:00Z"),
        _record("assistant", "2026-01-01T00:00:04Z", output=20),
        {"isCompactSummary": True},
    ])
    first = telemetry.build(str(path))
    assert first["compactions"] == 1 and first["tok_per_s"] == 5.0

    cache.write_text(json.dumps(first))
    unchanged = telemetry.build(str(path))
    assert unchanged["compactions"] == 1
    with path.open("a") as fh:
        fh.write(json.dumps({"subtype": "compact"}) + "\n")
    grown = telemetry.build(str(path))
    assert grown["compactions"] == 2
    path.write_text(json.dumps(_record("assistant", "2026-01-01T00:00:05Z", output=1)) + "\n")
    shrunk = telemetry.build(str(path))
    assert shrunk["compactions"] == 0


def test_main_writes_cache_and_prints_json(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    _jsonl(transcript, [_record("assistant", "2026-01-01T00:00:01Z", output=1)])
    cache = tmp_path / "cache.json"
    stdout = io.StringIO()
    monkeypatch.setattr(telemetry, "CACHE_PATH", str(cache))
    monkeypatch.setattr(telemetry.sys, "argv", [str(SCRIPT), str(transcript)])
    monkeypatch.setattr(telemetry.sys, "stdout", stdout)
    telemetry.main()
    emitted = json.loads(stdout.getvalue())
    assert emitted["path"] == str(transcript)
    assert json.loads(cache.read_text()) == emitted
