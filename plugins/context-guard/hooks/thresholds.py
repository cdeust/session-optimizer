"""thresholds.py — per-model checkpoint threshold table lookup.

Pure config-table logic, separated from stop-context-guard.py's checkpoint
policy: this module only knows how to load a threshold table from a JSON
config path (falling back to a caller-supplied default table when the file
is absent/unreadable/malformed) and pick the entry matching a model id. It
has no opinion about WARN/HARD semantics or int validity — the caller
(stop-context-guard.py's `_thresholds`) owns that.
"""

import json


def load_table(config_path: str, fallback: dict) -> dict:
    """Return the configured threshold table at `config_path`, or `fallback`
    when the file is absent, unreadable, or structurally invalid (not a
    dict, or "models" is not a list)."""
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return fallback
    if isinstance(loaded, dict) and isinstance(loaded.get("models"), list):
        return loaded
    return fallback


def upgrade_bundled_table(table: dict, fallback: dict) -> dict:
    """Upgrade only the unchanged pre-Codex bundled table.

    Source: plugins/statusline/assets/ctxguard-thresholds.json before 2.1.0.
    Custom rows or a custom default are user policy and remain authoritative.
    Ignore descriptive metadata when comparing the shipped policy.
    """
    legacy = [row for row in fallback["models"] if row["match"] != "astra"]
    if table.get("models") == legacy and table.get("default") == fallback["default"]:
        return {**table, "models": fallback["models"]}
    return table


def matching_entry(table: dict, model_id: str, fallback_default: dict) -> dict:
    """First model entry whose "match" substring is in the lowercased
    model_id, else the table's own "default" entry (itself possibly
    malformed), else `fallback_default`."""
    mid = (model_id or "").lower()
    for entry in table.get("models", []):
        try:
            if entry["match"] in mid:
                return entry
        except (KeyError, TypeError):
            continue
    return table.get("default") or fallback_default
