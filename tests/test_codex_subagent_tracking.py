"""Codex rollout fixtures; no private transcript content.

Source shapes: context-guard README, Codex support evidence table.
"""

import io
import json

import pytest
from test_context_guard_hooks import HOOKS, ROOT, _load, guard, tracker

codex = _load("reviewed_subagent_codex", HOOKS / "subagent_codex.py")
spend = _load("reviewed_subagent_spend", HOOKS / "subagent_spend.py")


def usage(turn, cumulative, context):
    inputs, outputs, cached = cumulative
    return {
        "type": "token_usage_record",
        "payload": {
            "thread_id": "child",
            "turn_id": turn,
            "usage": {"input_tokens": context},
            "thread_token_usage": {
                "input_tokens": inputs,
                "output_tokens": outputs,
                "cached_input_tokens": cached,
            },
        },
    }


def fixture(tmp_path):
    path = tmp_path / "rollout-child.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "child"}},
        {"type": "session_meta", "payload": {"id": "parent"}},
        {"type": "turn_context", "payload": {"turn_id": "parent-turn"}},
        {
            "type": "response_item",
            "payload": {"type": "function_call", "call_id": "inherited"},
        },
        {"type": "turn_context", "payload": {"turn_id": "t1", "model": "gpt-6-astra"}},
        {
            "type": "response_item",
            "payload": {"type": "function_call", "call_id": "own-tool"},
        },
        usage("t1", (1500, 100, 500), 1500),
        {"type": "turn_context", "payload": {"turn_id": "t2", "model": "gpt-6-astra"}},
        usage("t2", (4000, 200, 1000), 2500),
        usage("t2", (4000, 200, 1000), 2500),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return {
        "session_id": "parent",
        "agent_id": "child",
        "agent_type": "worker",
        "agent_transcript_path": str(path),
        "transcript_path": "parent.jsonl",
    }


def test_child_cumulative_usage_ignores_inherited_meta_and_duplicate_records(tmp_path):
    entry = codex.read_entry(fixture(tmp_path))
    assert entry["input_tokens"] == 4000
    assert entry["output_tokens"] == 200
    assert entry["cache_tokens"] == 1000
    assert entry["billed_tokens"] == 4200
    assert entry["context_tokens"] == 2500
    assert entry["tool_uses"] == 1
    assert entry["cost_usd"] is None


def test_codex_stop_upserts_child_and_reports_unknown_cost(tmp_path, monkeypatch):
    payload = fixture(tmp_path)
    monkeypatch.setattr(
        tracker,
        "_state_path",
        lambda _: str(tmp_path / "zetetic-subagents-parent.json"),
    )
    for _ in range(2):
        monkeypatch.setattr(tracker.sys, "stdin", io.StringIO(json.dumps(payload)))
        with pytest.raises(SystemExit) as exc:
            tracker.main()
        assert exc.value.code == 0
    state = json.loads((tmp_path / "zetetic-subagents-parent.json").read_text())
    assert state["totals"]["count"] == 1
    assert spend.read_summary(str(tmp_path), "parent") == (1, 4200, None)
    assert "cost unavailable" in spend.render_spend_line(1, 4200, None)
    assert "$0" not in spend.render_stub_bullet(1, 4200, None)


@pytest.mark.parametrize(
    "bad", ["not-json", "[]", '{"type":"token_usage_record","payload":[]}']
)
def test_unreadable_or_unproven_child_is_nonfatal(tmp_path, bad):
    payload = fixture(tmp_path)
    from pathlib import Path

    Path(payload["agent_transcript_path"]).write_text(bad + "\n")
    assert codex.read_entry(payload) is None
    assert codex.read_entry({}) is None


def test_wrong_child_identity_is_not_attributed(tmp_path):
    payload = fixture(tmp_path)
    payload["agent_id"] = "another-child"
    assert codex.read_entry(payload) is None


def test_old_threshold_config_keeps_new_astra_default_and_explicit_override(
    tmp_path, monkeypatch
):
    bundled = ROOT / "plugins/statusline/assets/ctxguard-thresholds.json"
    table = json.loads(bundled.read_text())
    table["models"] = [row for row in table["models"] if row["match"] != "astra"]
    config = tmp_path / "thresholds.json"
    config.write_text(json.dumps(table))
    monkeypatch.setattr(guard, "CONFIG_PATH", str(config))
    assert guard._thresholds("gpt-6-astra") == (180000, 220000)
    table["models"].append({"match": "astra", "warn": 100000, "hard": 150000})
    config.write_text(json.dumps(table))
    assert guard._thresholds("gpt-6-astra") == (100000, 150000)


def test_custom_default_is_not_overridden_by_builtins(tmp_path, monkeypatch):
    config = tmp_path / "thresholds.json"
    config.write_text(
        json.dumps({"models": [], "default": {"warn": 100000, "hard": 150000}})
    )
    monkeypatch.setattr(guard, "CONFIG_PATH", str(config))
    assert guard._thresholds("gpt-6-astra") == (100000, 150000)
    assert guard._thresholds("claude-haiku-4-5") == (100000, 150000)


def test_mixed_cache_semantics_and_unknown_cost(tmp_path):
    state = {
        "agents": {
            "codex": codex.read_entry(fixture(tmp_path)),
            "claude": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_tokens": 80,
                "cost_usd": 0.01,
            },
        }
    }
    tracker._recompute_totals(state)
    assert state["totals"]["billed_tokens"] == 4400
    assert state["totals"]["cost_usd"] is None
