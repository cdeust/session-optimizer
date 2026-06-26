"""Tests for tools/subagent_usage.py — parsing, pricing, dedup, discovery."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import subagent_usage as su  # noqa: E402


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _assistant(msg_id, model, usage, tool_uses=0):
    content = [{"type": "text", "text": "hi"}]
    content += [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}
                for i in range(tool_uses)]
    return {
        "type": "assistant",
        "isSidechain": True,
        "message": {"id": msg_id, "model": model, "content": content, "usage": usage},
    }


def test_price_for_matches_by_substring():
    assert su.price_for("claude-opus-4-8").input_per_mtok == 5.0
    assert su.price_for("claude-sonnet-4-6").output_per_mtok == 15.0
    assert su.price_for("claude-haiku-4-5").input_per_mtok == 1.0
    assert su.price_for("claude-fable-5").output_per_mtok == 50.0


def test_price_for_unknown_falls_back_to_opus_tier():
    assert su.price_for("totally-unknown").input_per_mtok == 5.0
    assert su.price_for(None).input_per_mtok == 5.0


def test_cost_applies_cache_tier_multipliers():
    # 1M input, 1M output, 1M 5m-write, 1M 1h-write, 1M read on Opus ($5/$25).
    u = su.Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                 cache_write_5m=1_000_000, cache_write_1h=1_000_000,
                 cache_read=1_000_000, model="claude-opus-4-8")
    # 5 + 25 + 5*1.25 + 5*2.0 + 5*0.10 = 46.75
    assert abs(su.cost_usd(u) - 46.75) < 1e-9


def test_parse_dedups_relogged_message_by_id(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    usage = {"input_tokens": 100, "output_tokens": 10}
    # Same message id logged twice (fork re-log) -> counted once.
    _write_jsonl(path, [
        _assistant("m1", "claude-opus-4-8", usage),
        _assistant("m1", "claude-opus-4-8", usage),
        _assistant("m2", "claude-opus-4-8", {"input_tokens": 50, "output_tokens": 5}),
    ])
    u = su.parse_transcript_usage(path)
    assert u.input_tokens == 150   # 100 (deduped) + 50
    assert u.output_tokens == 15


def test_parse_splits_cache_creation_by_ttl(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(path, [_assistant("m1", "claude-opus-4-8", {
        "input_tokens": 1, "cache_creation_input_tokens": 30,
        "cache_creation": {"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 10},
    })])
    u = su.parse_transcript_usage(path)
    assert u.cache_write_5m == 20
    assert u.cache_write_1h == 10


def test_parse_without_ttl_breakdown_defaults_to_5m(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(path, [_assistant("m1", "claude-opus-4-8",
                                   {"input_tokens": 1, "cache_creation_input_tokens": 40})])
    u = su.parse_transcript_usage(path)
    assert u.cache_write_5m == 40
    assert u.cache_write_1h == 0


def test_parse_counts_tool_uses_and_server_tools(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(path, [_assistant("m1", "claude-opus-4-8", {
        "input_tokens": 1,
        "server_tool_use": {"web_search_requests": 3, "web_fetch_requests": 2},
    }, tool_uses=2)])
    u = su.parse_transcript_usage(path)
    assert u.tool_uses == 2
    assert u.web_search_requests == 3
    assert u.web_fetch_requests == 2


def test_parse_missing_file_returns_zero():
    u = su.parse_transcript_usage("/nonexistent/agent-x.jsonl")
    assert u.input_tokens == 0 and su.cost_usd(u) == 0.0


def test_subagent_record_reads_meta(tmp_path):
    sub = tmp_path / "subagents"
    sub.mkdir()
    path = str(sub / "agent-abc123.jsonl")
    _write_jsonl(path, [_assistant("m1", "claude-haiku-4-5", {"input_tokens": 10, "output_tokens": 2})])
    with open(str(sub / "agent-abc123.meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"agentType": "Explore", "description": "look", "toolUseId": "toolu_1"}, fh)
    rec = su.subagent_record(path)
    assert rec.agent_id == "abc123"
    assert rec.agent_type == "Explore"
    assert rec.description == "look"
    assert rec.usage.model == "claude-haiku-4-5"


def test_subagent_record_without_meta_is_unknown(tmp_path):
    sub = tmp_path / "subagents"
    sub.mkdir()
    path = str(sub / "agent-nometa.jsonl")
    _write_jsonl(path, [_assistant("m1", "claude-opus-4-8", {"input_tokens": 1})])
    rec = su.subagent_record(path)
    assert rec.agent_type == "unknown"


def test_discover_finds_nested_workflow_subagents(tmp_path):
    session = tmp_path / "sess"
    nested = session / "subagents" / "workflows" / "wf_1"
    nested.mkdir(parents=True)
    (session / "subagents" / "agent-a.jsonl").write_text("{}\n")
    (nested / "agent-b.jsonl").write_text("{}\n")
    (session / "subagents" / "agent-a.meta.json").write_text("{}")  # not a transcript
    found = su.discover_subagents(str(session))
    bases = sorted(os.path.basename(p) for p in found)
    assert bases == ["agent-a.jsonl", "agent-b.jsonl"]


def test_session_dir_for_resolves_owning_session(tmp_path):
    p = "/x/proj/sessid/subagents/workflows/wf_1/agent-b.jsonl"
    assert su.session_dir_for(p) == "/x/proj/sessid"
    p2 = "/x/proj/sessid/subagents/agent-a.jsonl"
    assert su.session_dir_for(p2) == "/x/proj/sessid"
