"""Tests for plugins/context-guard/tools/subagent_usage.py — parsing, pricing,
dedup, discovery."""

import json
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "plugins", "context-guard", "tools"),
)

import subagent_usage as su


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in records)


def _assistant(msg_id, model, usage, tool_uses=0):
    content = [{"type": "text", "text": "hi"}]
    content += [
        {"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}
        for i in range(tool_uses)
    ]
    return {
        "type": "assistant",
        "isSidechain": True,
        "message": {"id": msg_id, "model": model, "content": content, "usage": usage},
    }


# Rates read from https://platform.claude.com/docs/en/about-claude/pricing on
# 2026-11-07: (input, output, 5m write, 1h write, cache read) per MTok.
@pytest.mark.parametrize(
    "model_id, rates",
    [
        ("claude-fable-5-1", (10.0, 50.0, 12.5, 20.0, 0.25)),
        ("claude-opus-5-5", (4.0, 20.0, 5.0, 8.0, 0.20)),
        ("claude-sonnet-5-5", (2.0, 10.0, 2.5, 4.0, 0.10)),
        ("claude-haiku-4-5", (1.0, 5.0, 1.25, 2.0, 0.10)),
        ("claude-mythos-5-1", (10.0, 50.0, 12.5, 20.0, 0.25)),
        ("claude-fable-5", (10.0, 50.0, 12.5, 20.0, 1.0)),
        ("claude-opus-5", (5.0, 25.0, 6.25, 10.0, 0.50)),
        ("claude-opus-4-8", (5.0, 25.0, 6.25, 10.0, 0.50)),
        ("claude-opus-4-1", (15.0, 75.0, 18.75, 30.0, 1.50)),
        ("claude-opus-4", (15.0, 75.0, 18.75, 30.0, 1.50)),
        ("claude-sonnet-5", (2.0, 10.0, 2.5, 4.0, 0.20)),
        ("claude-sonnet-4-6", (3.0, 15.0, 3.75, 6.0, 0.30)),
    ],
)
def test_price_for_each_model_version(model_id, rates):
    p = su.price_for(model_id)
    assert (
        p.input_per_mtok,
        p.output_per_mtok,
        p.cache_write_5m_per_mtok,
        p.cache_write_1h_per_mtok,
        p.cache_read_per_mtok,
    ) == rates


def test_price_for_tells_a_version_from_its_successor():
    # The family-substring table priced claude-opus-5-5 as Opus 5 (#63).
    assert su.price_for("claude-opus-5-5").input_per_mtok == 4.0
    assert su.price_for("claude-opus-5").input_per_mtok == 5.0
    assert su.price_for("claude-fable-5-1").cache_read_per_mtok == 0.25
    assert su.price_for("claude-fable-5").cache_read_per_mtok == 1.0


def test_price_for_accepts_snapshot_date_and_context_suffix():
    assert su.price_for("claude-haiku-4-5-20251001").input_per_mtok == 1.0
    assert su.price_for("claude-opus-5-5[1m]").input_per_mtok == 4.0
    assert su.price_for("claude-opus-4-20250514").input_per_mtok == 15.0
    assert su.price_for("CLAUDE-OPUS-5-5").input_per_mtok == 4.0


@pytest.mark.parametrize(
    "model_id",
    [
        "totally-unknown",
        None,
        "",
        "opus",
        "claude-opus-6",
        "claude-opus-4-80",
        "claude-haiku-5-5",  # rates depend on prompt size: not one figure
    ],
)
def test_price_for_unknown_model_is_unpriced(model_id):
    assert su.price_for(model_id) is None


def test_cost_of_an_unpriced_model_is_none_not_a_guess():
    u = su.Usage(input_tokens=1_000, output_tokens=10, model="claude-opus-6")
    assert su.cost_usd(u) is None


def test_cost_of_nothing_billed_is_zero_whatever_the_model():
    assert su.cost_usd(su.Usage(model="claude-opus-6")) == 0.0


def test_cost_applies_cache_tier_multipliers():
    # 1M of each token kind on Opus 4.8: 5 + 25 + 6.25 + 10 + 0.50 = 46.75
    u = su.Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_5m=1_000_000,
        cache_write_1h=1_000_000,
        cache_read=1_000_000,
        model="claude-opus-4-8",
    )
    assert abs(su.cost_usd(u) - 46.75) < 1e-9


def test_cost_uses_the_published_cache_read_rate_not_a_multiplier():
    # Opus 5.5: 4 + 20 + 5 + 8 + 0.20 = 37.20 (a 0.1x read multiplier gave 0.40).
    u = su.Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_5m=1_000_000,
        cache_write_1h=1_000_000,
        cache_read=1_000_000,
        model="claude-opus-5-5",
    )
    assert abs(su.cost_usd(u) - 37.20) < 1e-9


def test_parse_dedups_relogged_message_by_id(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    usage = {"input_tokens": 100, "output_tokens": 10}
    # Same message id logged twice (fork re-log) -> counted once.
    _write_jsonl(
        path,
        [
            _assistant("m1", "claude-opus-4-8", usage),
            _assistant("m1", "claude-opus-4-8", usage),
            _assistant(
                "m2", "claude-opus-4-8", {"input_tokens": 50, "output_tokens": 5}
            ),
        ],
    )
    u = su.parse_transcript_usage(path)
    assert u.input_tokens == 150  # 100 (deduped) + 50
    assert u.output_tokens == 15


def test_parse_splits_cache_creation_by_ttl(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(
        path,
        [
            _assistant(
                "m1",
                "claude-opus-4-8",
                {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": 30,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 20,
                        "ephemeral_1h_input_tokens": 10,
                    },
                },
            )
        ],
    )
    u = su.parse_transcript_usage(path)
    assert u.cache_write_5m == 20
    assert u.cache_write_1h == 10


def test_parse_without_ttl_breakdown_defaults_to_5m(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(
        path,
        [
            _assistant(
                "m1",
                "claude-opus-4-8",
                {"input_tokens": 1, "cache_creation_input_tokens": 40},
            )
        ],
    )
    u = su.parse_transcript_usage(path)
    assert u.cache_write_5m == 40
    assert u.cache_write_1h == 0


def test_parse_counts_tool_uses_and_server_tools(tmp_path):
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(
        path,
        [
            _assistant(
                "m1",
                "claude-opus-4-8",
                {
                    "input_tokens": 1,
                    "server_tool_use": {
                        "web_search_requests": 3,
                        "web_fetch_requests": 2,
                    },
                },
                tool_uses=2,
            )
        ],
    )
    u = su.parse_transcript_usage(path)
    assert u.tool_uses == 2
    assert u.web_search_requests == 3
    assert u.web_fetch_requests == 2


def test_parse_missing_file_returns_zero():
    u = su.parse_transcript_usage("/nonexistent/agent-x.jsonl")
    assert u.input_tokens == 0 and su.cost_usd(u) == 0.0


def test_parse_raises_on_malformed_record_usage(tmp_path):
    """A missing/unreadable FILE zeroes out (test above); a malformed
    per-record usage payload is a different failure and must not be
    silently swallowed into the same zeroed Usage() -- that would turn a
    detectable data-quality bug into a silent cost under-report."""
    path = str(tmp_path / "agent-x.jsonl")
    _write_jsonl(
        path,
        [_assistant("m1", "claude-opus-4-8", {"input_tokens": "abc"})],
    )
    with pytest.raises(ValueError):
        su.parse_transcript_usage(path)


def test_subagent_record_reads_meta(tmp_path):
    sub = tmp_path / "subagents"
    sub.mkdir()
    path = str(sub / "agent-abc123.jsonl")
    _write_jsonl(
        path,
        [
            _assistant(
                "m1", "claude-haiku-4-5", {"input_tokens": 10, "output_tokens": 2}
            )
        ],
    )
    with open(str(sub / "agent-abc123.meta.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {"agentType": "Explore", "description": "look", "toolUseId": "toolu_1"}, fh
        )
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


def test_usage_add_context_and_dict():
    left = su.Usage(
        input_tokens=1,
        output_tokens=2,
        cache_write_5m=3,
        cache_write_1h=4,
        cache_read=5,
        model="",
    )
    right = su.Usage(
        input_tokens=10,
        output_tokens=20,
        cache_write_5m=30,
        cache_write_1h=40,
        cache_read=50,
        tool_uses=2,
        web_search_requests=3,
        web_fetch_requests=4,
        model="sonnet",
        models={"sonnet"},
    )
    left.add(right)
    assert left.context_tokens == 143
    payload = left.to_dict()
    assert payload["output_tokens"] == 22
    assert payload["models"] == ["sonnet"]


def test_parse_uses_largest_duplicate_and_uuid_fallback(tmp_path):
    path = str(tmp_path / "agent-dups.jsonl")
    records = [
        {"uuid": "u1", "message": {"usage": {"input_tokens": 1}, "content": []}},
        {"uuid": "u1", "message": {"usage": {"input_tokens": 9}, "content": []}},
        {
            "message": {
                "model": "sonnet",
                "usage": {"output_tokens": 2},
                "content": ["text", {"type": "tool_use"}],
            }
        },
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("bad\n\n")
        fh.writelines(json.dumps(record) + "\n" for record in records)
    usage = su.parse_transcript_usage(path)
    assert usage.input_tokens == 9
    assert usage.output_tokens == 2
    assert usage.tool_uses == 1
    assert usage.model == "sonnet"


def test_meta_agent_id_and_discovery_fallbacks(tmp_path):
    bad = tmp_path / "bad.meta.json"
    bad.write_text("bad")
    assert su._read_meta(str(bad)) == ("", "", "")
    assert su._agent_id_from_path("/x/not-an-agent.log") == "not-an-agent.log"
    assert su.discover_subagents(str(tmp_path / "missing")) == []
    assert su.session_dir_for("/x/no-subagents/agent-a.jsonl") == "/x/no-subagents"


def test_token_format_and_session_iteration(tmp_path):
    assert su._fmt_tokens(999) == "999"
    assert su._fmt_tokens(1_000) == "1k"
    assert su._fmt_tokens(1_500_000) == "1.5M"
    assert list(su._iter_session_dirs(str(tmp_path / "missing"))) == []
    session = tmp_path / "s1" / "subagents"
    session.mkdir(parents=True)
    (tmp_path / "file").write_text("x")
    assert list(su._iter_session_dirs(str(tmp_path))) == [str(tmp_path / "s1")]


def test_build_report_groups_records(monkeypatch):
    usage = su.Usage(
        input_tokens=100, output_tokens=20, model="haiku", models={"haiku"}
    )
    rec = su.SubagentRecord("a", "Explore", "look", "tool", usage, 0.001, "/a")
    monkeypatch.setattr(su, "_encoded_project_dir", lambda _cwd: "/project")
    monkeypatch.setattr(su, "_iter_session_dirs", lambda _root: ["/project/s"])
    monkeypatch.setattr(su, "discover_subagents", lambda _session: ["/a", "/b"])
    monkeypatch.setattr(su, "subagent_record", lambda _path: rec)
    report = su.build_report("/cwd")
    assert report["subagent_count"] == 2
    assert report["totals"]["cost_usd"] == round(2 * rec.cost_usd, 4)
    assert report["by_agent_type"]["Explore"]["count"] == 2
    assert report["totals"]["usage"]["input_tokens"] == 200


def test_print_table_and_cli_modes(monkeypatch, capsys, tmp_path):
    empty = {
        "project_dir": "/p",
        "subagent_count": 0,
        "by_agent_type": {},
        "totals": {"cost_usd": 0.0},
    }
    su._print_table(empty)
    assert "no subagent transcripts" in capsys.readouterr().out

    report = {
        "project_dir": "/p",
        "subagent_count": 1,
        "by_agent_type": {
            "Explore": {
                "count": 1,
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 2,
                    "cache_write_5m": 3,
                    "cache_write_1h": 4,
                    "cache_read": 5,
                },
                "cost_usd": 1.25,
            }
        },
        "totals": {"cost_usd": 1.25},
    }
    su._print_table(report)
    assert "Explore" in capsys.readouterr().out

    monkeypatch.setattr(su, "build_report", lambda _cwd: report)
    assert su.main(["--json", str(tmp_path)]) == 0
    assert '"subagent_count": 1' in capsys.readouterr().out
    assert su.main([str(tmp_path)]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_build_report_marks_a_total_with_an_unpriced_run_as_unpriced(
    monkeypatch, capsys
):
    priced = su.Usage(input_tokens=1_000_000, model="claude-opus-5-5")
    unpriced = su.Usage(input_tokens=1_000_000, model="claude-opus-6")
    recs = {
        "/a": su.SubagentRecord("a", "Explore", "", "", priced, 4.0, "/a"),
        "/b": su.SubagentRecord("b", "Plan", "", "", unpriced, None, "/b"),
    }
    monkeypatch.setattr(su, "_iter_session_dirs", lambda _p: ["/s"])
    monkeypatch.setattr(su, "discover_subagents", lambda _s: ["/a", "/b"])
    monkeypatch.setattr(su, "subagent_record", lambda path: recs[path])
    report = su.build_report("/cwd")
    assert report["by_agent_type"]["Explore"]["cost_usd"] == 4.0
    assert report["by_agent_type"]["Plan"]["cost_usd"] is None
    assert report["totals"]["cost_usd"] is None
    su._print_table(report)
    out = capsys.readouterr().out
    assert "unpriced" in out and "$    4.00" in out
