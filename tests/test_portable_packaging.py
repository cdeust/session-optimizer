"""Cross-host packaging contracts for the portable refine-gate skill and the
Codex-installable context-guard plugin."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "refine-gate"
CONTEXT_GUARD = ROOT / "plugins" / "context-guard"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_codex_plugin_is_skills_only_and_points_to_real_content():
    manifest = _json(PLUGIN / ".codex-plugin" / "plugin.json")

    assert manifest["name"] == PLUGIN.name
    assert manifest["skills"] == "./skills/"
    assert (PLUGIN / manifest["skills"]).is_dir()
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest


def test_codex_marketplace_resolves_refine_gate_from_repo_root():
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = next(p for p in marketplace["plugins"] if p["name"] == "refine-gate")

    assert entry["source"] == {
        "source": "local",
        "path": "./plugins/refine-gate",
    }
    assert (ROOT / entry["source"]["path"]).resolve() == PLUGIN.resolve()
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"


def test_refine_skill_uses_portable_agent_skills_frontmatter():
    text = (PLUGIN / "skills" / "refine" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    keys = set(re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", frontmatter, re.MULTILINE))

    assert keys == {"name", "description"}
    assert "name: refine" in frontmatter


def test_codex_plugin_manifest_for_context_guard_has_no_skills_field():
    """context-guard ships hooks, not skills -- the manifest must not claim a
    skills directory that does not exist (the inverse of refine-gate's
    skills-only shape above)."""
    manifest = _json(CONTEXT_GUARD / ".codex-plugin" / "plugin.json")

    assert manifest["name"] == CONTEXT_GUARD.name
    assert (
        manifest["version"]
        == _json(CONTEXT_GUARD / ".claude-plugin" / "plugin.json")["version"]
    )
    assert "skills" not in manifest
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest


def test_codex_marketplace_resolves_context_guard_from_repo_root():
    """Codex reads the plugin's own hooks/hooks.json (same file, same Stop/
    SubagentStop keys Claude uses) -- verified against the owner's real
    ~/.codex/config.toml trust keys `context-guard@session-optimizer-
    marketplace:hooks/hooks.json:stop:0:0` and `...:subagent_stop:0:0`; no
    Codex-specific hooks file is required, so this marketplace entry only
    needs to resolve the plugin directory correctly."""
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = next(p for p in marketplace["plugins"] if p["name"] == "context-guard")

    assert entry["source"] == {
        "source": "local",
        "path": "./plugins/context-guard",
    }
    assert (ROOT / entry["source"]["path"]).resolve() == CONTEXT_GUARD.resolve()
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"
    assert (CONTEXT_GUARD / "hooks" / "hooks.json").is_file()
