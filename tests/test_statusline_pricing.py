"""Tests for the statusline price table (plugins/statusline/assets/pricing.json)
and the jq pricer that reads it (plugins/statusline/assets/lib/pricing.sh).

Each test runs the shipped costs.sh as a subprocess, so what is checked is the
figure the statusline actually computes. session-optimizer #63.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "plugins" / "statusline" / "assets"
COSTS = ASSETS / "costs.sh"
PRICING = ASSETS / "pricing.json"

sys.path.insert(0, str(ROOT / "plugins" / "context-guard" / "tools"))

import subagent_usage as su

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None,
    reason="the statusline pricer needs bash and jq",
)

MTOK = 1_000_000


def _env(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("STATUSLINE_")}
    env["HOME"] = str(tmp_path)
    env["STATUSLINE_STATE_DIR"] = str(tmp_path / "state")
    return env


def _price_lines(tmp_path, records):
    sample = tmp_path / "sample.jsonl"
    sample.write_text("".join(json.dumps(r) + "\n" for r in records))
    result = subprocess.run(
        ["bash", str(COSTS), "__price_sample", str(sample)],
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def _line(model, **usage):
    return {"message": {"model": model, "usage": usage}}


def _rates(tmp_path, model):
    """The five per-MTok rates the pricer applies to `model`, one per line."""
    lines = _price_lines(
        tmp_path,
        [
            _line(model, input_tokens=MTOK),
            _line(model, output_tokens=MTOK),
            _line(
                model,
                cache_creation_input_tokens=MTOK,
                cache_creation={"ephemeral_5m_input_tokens": MTOK},
            ),
            _line(
                model,
                cache_creation_input_tokens=MTOK,
                cache_creation={"ephemeral_1h_input_tokens": MTOK},
            ),
            _line(model, cache_read_input_tokens=MTOK),
        ],
    )
    return tuple(lines)


# Rates read from https://platform.claude.com/docs/en/about-claude/pricing on
# 2026-11-12: (input, output, 5m write, 1h write, cache read) per MTok.
@pytest.mark.parametrize(
    "model_id, rates",
    [
        ("claude-fable-5-2", (8, 40, 10, 16, 0.2)),
        ("claude-mythos-5-2", (8, 40, 10, 16, 0.2)),
        ("claude-opus-5-5", (4, 20, 5, 8, 0.2)),
        ("claude-opus-5-5[1m]", (4, 20, 5, 8, 0.2)),
        ("claude-sonnet-5-5", (2, 10, 2.5, 4, 0.1)),
        ("claude-fable-5-1", (10, 50, 12.5, 20, 0.25)),
        ("claude-mythos-5-1", (10, 50, 12.5, 20, 0.25)),
        ("claude-haiku-4-5-20251001", (1, 5, 1.25, 2, 0.1)),
        ("claude-opus-5", (5, 25, 6.25, 10, 0.5)),
        ("claude-opus-4-8", (5, 25, 6.25, 10, 0.5)),
        ("claude-opus-4-1", (15, 75, 18.75, 30, 1.5)),
        ("claude-sonnet-5", (2, 10, 2.5, 4, 0.2)),
        ("claude-sonnet-4-6", (3, 15, 3.75, 6, 0.3)),
        ("claude-fable-5", (10, 50, 12.5, 20, 1.0)),
    ],
)
def test_each_model_version_is_priced_at_its_published_rates(tmp_path, model_id, rates):
    assert _rates(tmp_path, model_id) == pytest.approx(rates)


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-haiku-5-5",  # rates depend on each prompt's size
        "claude-opus-6",  # a future version must not fall to an older row
        "claude-opus",
        "claude-sonnet",
        "some-other-model",
        "",
    ],
)
def test_a_model_no_row_names_is_unpriced_not_guessed(tmp_path, model_id):
    assert _price_lines(tmp_path, [_line(model_id, input_tokens=MTOK)]) == ["unpriced"]


def test_every_row_names_one_version_and_there_is_no_catch_all():
    table = json.loads(PRICING.read_text())
    versions = [row["version"] for row in table["models"]]
    assert len(versions) == len(set(versions))
    for row in table["models"]:
        assert row["match"], row
        assert row["version"] in row["match"], row
    assert "sonnet5_intro_until" not in table
    assert "pricing" in table["source"] and "read 2026-11-12" in table["source"]


LEDGER_COPY = ROOT / "plugins" / "context-guard" / "tools" / "pricing.json"


def test_the_ledger_table_is_a_byte_copy_of_the_statusline_table():
    # #63: one price table. Each plugin installs on its own (CONTRIBUTING.md),
    # so context-guard ships a copy; any difference fails here.
    assert LEDGER_COPY.read_bytes() == PRICING.read_bytes()


def test_the_ledger_applies_the_same_rows_as_the_statusline_pricer():
    table = json.loads(PRICING.read_text())
    statusline = {
        row["version"]: (
            row["match"],
            row["ind"],
            row["out"],
            row["cw5"],
            row["cw1"],
            row["cr"],
        )
        for row in table["models"]
    }
    ledger = {
        version: (
            rx.pattern,
            p.input_per_mtok,
            p.output_per_mtok,
            p.cache_write_5m_per_mtok,
            p.cache_write_1h_per_mtok,
            p.cache_read_per_mtok,
        )
        for version, rx, p in su.PRICE_ROWS
    }
    assert statusline == ledger


def test_session_cost_leaves_unpriced_turns_out_of_the_sum(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    turns = [
        {
            "type": "assistant",
            "requestId": "r1",
            "message": {
                "id": "m1",
                "model": "claude-opus-5-5",
                "usage": {"input_tokens": MTOK, "output_tokens": MTOK},
            },
        },
        {
            "type": "assistant",
            "requestId": "r2",
            "message": {
                "id": "m2",
                "model": "claude-haiku-5-5",
                "usage": {"input_tokens": MTOK, "output_tokens": MTOK},
            },
        },
    ]
    transcript.write_text("".join(json.dumps(t) + "\n" for t in turns))
    payload = json.dumps(
        {
            "session_id": "s1",
            "transcript_path": str(transcript),
            "cost": {"total_duration_ms": 1000},
        }
    )
    result = subprocess.run(
        ["bash", str(COSTS), "update"],
        input=payload,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "costs.jsonl").read_text().splitlines()
    ]
    # Opus 5.5 only: 1 MTok in at $4 + 1 MTok out at $20. The Haiku 5.5 turn
    # adds nothing instead of being priced at a generic $3 / $15 row.
    assert [r["cost_usd"] for r in rows] == [pytest.approx(24.0)]
    # ...and is counted, so the figure can be shown as a lower bound.
    assert [r["unpriced_turns"] for r in rows] == [1]


# ── Unpriced turns are counted and shown (session-optimizer #63, third slice) ──

OPUS_TURN = {
    "type": "assistant",
    "requestId": "r1",
    "message": {
        "id": "m1",
        "model": "claude-opus-5-5",
        "usage": {"input_tokens": MTOK, "output_tokens": MTOK},
    },
}
HAIKU_TURN = {
    "type": "assistant",
    "requestId": "r2",
    "message": {
        "id": "m2",
        "model": "claude-haiku-5-5",
        "usage": {"input_tokens": MTOK, "output_tokens": MTOK},
    },
}
# Claude Code writes zero-usage "<synthetic>" assistant messages; no row names
# the model, but they cost nothing and must not raise the unpriced marker.
SYNTHETIC_TURN = {
    "type": "assistant",
    "requestId": "r3",
    "message": {
        "id": "m3",
        "model": "<synthetic>",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    },
}


def _write_transcript(path, turns):
    path.write_text("".join(json.dumps(t) + "\n" for t in turns))


def _payload(transcript, session="s1"):
    return json.dumps(
        {
            "session_id": session,
            "transcript_path": str(transcript),
            "cost": {"total_duration_ms": 1000},
        }
    )


def _update(tmp_path, transcript, extra_env=None):
    env = _env(tmp_path)
    env.update(extra_env or {})
    result = subprocess.run(
        ["bash", str(COSTS), "update"],
        input=_payload(transcript),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [
        json.loads(line)
        for line in (tmp_path / "state" / "costs.jsonl").read_text().splitlines()
    ]


def test_a_fully_priced_session_has_no_unpriced_turns(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, SYNTHETIC_TURN])
    rows = _update(tmp_path, transcript)
    assert [(r["cost_usd"], r["unpriced_turns"]) for r in rows] == [
        (pytest.approx(24.0), 0)
    ]


def test_a_session_with_only_unpriced_turns_still_gets_a_row(tmp_path):
    # Priced cost is zero, but the row must exist to carry the warning.
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [HAIKU_TURN])
    rows = _update(tmp_path, transcript)
    assert [(r["cost_usd"], r["unpriced_turns"]) for r in rows] == [(0, 1)]
    assert "session $0.00 +1 unpriced" in _render(tmp_path, transcript)


def test_a_session_with_nothing_spent_and_nothing_unpriced_writes_no_row(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [SYNTHETIC_TURN])
    env = _env(tmp_path)
    result = subprocess.run(
        ["bash", str(COSTS), "update"],
        input=_payload(transcript),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "state" / "costs.jsonl").exists()


def test_a_repeated_unpriced_response_is_counted_once(tmp_path):
    # The same API response re-logged (streaming) is one turn, as for pricing.
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, HAIKU_TURN, HAIKU_TURN, HAIKU_TURN])
    rows = _update(tmp_path, transcript)
    assert [r["unpriced_turns"] for r in rows] == [1]


def test_unpriced_subagent_turns_are_counted_with_the_main_thread(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, HAIKU_TURN])
    sub = tmp_path / "s1" / "subagents"
    sub.mkdir(parents=True)
    other = dict(
        HAIKU_TURN, requestId="r9", message=dict(HAIKU_TURN["message"], id="m9")
    )
    _write_transcript(sub / "agent-1.jsonl", [other])
    rows = _update(tmp_path, transcript)
    assert [r["unpriced_turns"] for r in rows] == [2]


def test_a_new_unpriced_turn_rewrites_a_row_whose_cost_did_not_move(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN])
    no_throttle = {
        "STATUSLINE_MAIN_REFRESH_SEC": "0",
        "STATUSLINE_SUBAGENT_REFRESH_SEC": "0",
    }
    assert [
        r["unpriced_turns"] for r in _update(tmp_path, transcript, no_throttle)
    ] == [0]
    _write_transcript(transcript, [OPUS_TURN, HAIKU_TURN])
    # Force a new mtime key: transcripts are re-priced on an mtime change.
    stat = transcript.stat()
    os.utime(transcript, (stat.st_atime, stat.st_mtime + 5))
    rows = _update(tmp_path, transcript, no_throttle)
    assert [(r["cost_usd"], r["unpriced_turns"]) for r in rows] == [
        (pytest.approx(24.0), 1)
    ]


def test_a_cache_line_without_the_unpriced_field_is_repriced(tmp_path):
    # A cache written before the field existed holds "<mtime> <cost> <at>"; it
    # does not prove zero unpriced turns, so it must not be reused as such.
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, HAIKU_TURN])
    mtime = int(transcript.stat().st_mtime)
    sessions = tmp_path / "state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "s1.main").write_text(f"{mtime} 24.000000 {mtime}\n")
    rows = _update(tmp_path, transcript)
    assert [r["unpriced_turns"] for r in rows] == [1]
    assert (sessions / "s1.main").read_text().split()[3] == "1"


def _render(tmp_path, transcript):
    env = _env(tmp_path)
    status = json.loads(_payload(transcript))
    status["model"] = {"display_name": "Opus"}
    status["workspace"] = {"current_dir": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(ASSETS / "statusline-command.sh")],
        input=json.dumps(status),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)


def test_the_statusline_marks_a_session_cost_with_unpriced_turns(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, HAIKU_TURN])
    assert "session $24.00 +1 unpriced" in _render(tmp_path, transcript)


def test_the_statusline_shows_no_marker_when_every_turn_is_priced(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    _write_transcript(transcript, [OPUS_TURN, SYNTHETIC_TURN])
    out = _render(tmp_path, transcript)
    assert "session $24.00" in out
    assert "unpriced" not in out
