from __future__ import annotations

from pathlib import Path

from homeintent.storage_migration import resolve_storage_path


class _Config:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, *parts: str) -> str:
        return str(self.root.joinpath(*parts))


class _Hass:
    def __init__(self, root: Path) -> None:
        self.config = _Config(root)


def test_fresh_install_uses_canonical_path_without_creating_a_file(tmp_path):
    resolved = resolve_storage_path(
        _Hass(tmp_path),
        ".storage/homeintent_goal_runs.json",
        ".storage/ha_nlu_goal_runs.json",
    )

    assert resolved == str(tmp_path / ".storage" / "homeintent_goal_runs.json")
    assert not Path(resolved).exists()


def test_legacy_storage_is_atomically_adopted_under_homeintent_name(tmp_path):
    legacy = tmp_path / ".storage" / "ha_nlu_goal_runs.json"
    legacy.parent.mkdir()
    legacy.write_text('{"runs": []}', encoding="utf-8")

    resolved = resolve_storage_path(
        _Hass(tmp_path),
        ".storage/homeintent_goal_runs.json",
        ".storage/ha_nlu_goal_runs.json",
    )

    canonical = tmp_path / ".storage" / "homeintent_goal_runs.json"
    assert resolved == str(canonical)
    assert canonical.read_text(encoding="utf-8") == '{"runs": []}'
    assert not legacy.exists()


def test_existing_canonical_storage_wins_without_overwrite(tmp_path):
    canonical = tmp_path / "homeintent_agent_events.json"
    legacy = tmp_path / "ha_nlu_agent_events.json"
    canonical.write_text("new", encoding="utf-8")
    legacy.write_text("old", encoding="utf-8")

    resolved = resolve_storage_path(
        _Hass(tmp_path), "homeintent_agent_events.json", "ha_nlu_agent_events.json"
    )

    assert resolved == str(canonical)
    assert canonical.read_text(encoding="utf-8") == "new"
    assert legacy.read_text(encoding="utf-8") == "old"
