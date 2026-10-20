"""Hass-bridge-level tests for hass_entities.build_device_snapshots()/
build_world_model() (World Model Wave, 2026-08-13).

Reuses tests/_ha_stub.py's install() (read-only, not modified here) so the
fake ``homeassistant`` package tree is importable - exactly how
test_conversation_integration.py already imports homeintent.conversation, rather
than re-faking the whole package tree a second time (Regel 6). _ha_stub.py's
own registry classes are hardcoded to return None (they only exist so
hass_entities.py's module-level import succeeds); this file supplies real
per-test registry data by monkeypatching hass_entities.er/dr/ar/fr's
``async_get``/``async_get_area``/``async_get_floor`` with small local fakes
scoped to this file, rather than trying to make the shared None-only stub
itself configurable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent import hass_entities  # noqa: E402


class _FakeStates:
    def __init__(self, states: dict) -> None:
        self._states = states

    def get(self, entity_id: str):
        return self._states.get(entity_id)

    def async_all(self):
        return list(self._states.values())


class _FakeHass:
    def __init__(self, states: dict) -> None:
        self.states = _FakeStates(states)


class _FakeEntry:
    def __init__(self, selected_entities: list[str]) -> None:
        self.options = {"selected_entities": selected_entities}
        self.data: dict = {}


@pytest.fixture()
def registries(monkeypatch):
    """Patch hass_entities.er/dr/ar/fr's async_get* to read from the dicts
    this fixture returns, instead of _ha_stub's hardcoded-None registries."""
    entity_entries: dict[str, SimpleNamespace] = {}
    device_entries: dict[str, SimpleNamespace] = {}
    area_entries: dict[str, SimpleNamespace] = {}
    floor_entries: dict[str, SimpleNamespace] = {}

    monkeypatch.setattr(hass_entities.er, "async_get", lambda hass: SimpleNamespace(
        async_get=lambda entity_id: entity_entries.get(entity_id)
    ))
    monkeypatch.setattr(hass_entities.dr, "async_get", lambda hass: SimpleNamespace(
        async_get=lambda device_id: device_entries.get(device_id)
    ))
    monkeypatch.setattr(hass_entities.ar, "async_get", lambda hass: SimpleNamespace(
        async_get_area=lambda area_id: area_entries.get(area_id)
    ))
    monkeypatch.setattr(hass_entities.fr, "async_get", lambda hass: SimpleNamespace(
        async_get_floor=lambda floor_id: floor_entries.get(floor_id)
    ))
    return SimpleNamespace(
        entities=entity_entries, devices=device_entries, areas=area_entries, floors=floor_entries
    )


def _entity_entry(
    *, device_id: str | None = None, area_id: str | None = None,
    platform: str | None = None, config_entry_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        device_id=device_id, area_id=area_id, platform=platform, config_entry_id=config_entry_id,
    )


def _device_entry(
    *, area_id: str | None = None, name: str = "Gerät", name_by_user: str | None = None,
    manufacturer: str | None = None, model: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        area_id=area_id, name=name, name_by_user=name_by_user, manufacturer=manufacturer, model=model,
    )


def _area_entry(*, name: str, floor_id: str | None = None, aliases: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(name=name, floor_id=floor_id, aliases=aliases)


def _floor_entry(*, name: str, level: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, level=level)


def test_build_device_snapshots_only_selected_entities(registries):
    registries.entities["light.a"] = _entity_entry(device_id="device_1")
    registries.entities["light.b"] = _entity_entry(device_id="device_1")
    registries.devices["device_1"] = _device_entry(area_id="wohnzimmer", name="Lampe", manufacturer="Acme", model="X1")
    registries.areas["wohnzimmer"] = _area_entry(name="Wohnzimmer")

    hass = _FakeHass(states={})
    entry = _FakeEntry(selected_entities=["light.a"])  # light.b exists but isn't selected

    snapshots = hass_entities.build_device_snapshots(hass, entry)

    assert len(snapshots) == 1
    device = snapshots[0]
    assert device.device_id == "device_1"
    assert device.entity_ids == ("light.a",)
    assert device.area_id == "wohnzimmer"
    assert device.area_name == "Wohnzimmer"
    assert device.manufacturer == "Acme"
    assert device.model == "X1"


def test_build_device_snapshots_name_by_user_wins(registries):
    registries.entities["light.a"] = _entity_entry(device_id="device_1")
    registries.devices["device_1"] = _device_entry(name="Lampe (Werksname)", name_by_user="Wohnzimmerlampe")

    hass = _FakeHass(states={})
    entry = _FakeEntry(selected_entities=["light.a"])

    snapshots = hass_entities.build_device_snapshots(hass, entry)

    assert snapshots[0].name == "Wohnzimmerlampe"


def test_build_device_snapshots_device_with_no_selected_entities_absent(registries):
    registries.entities["light.a"] = _entity_entry(device_id="device_1")
    registries.devices["device_1"] = _device_entry()

    hass = _FakeHass(states={})
    entry = _FakeEntry(selected_entities=[])  # nothing selected

    assert hass_entities.build_device_snapshots(hass, entry) == []


def test_build_device_snapshots_unregistered_entity_skipped_without_error(registries):
    hass = _FakeHass(states={})
    entry = _FakeEntry(selected_entities=["light.unknown"])  # no entity_entries entry at all

    assert hass_entities.build_device_snapshots(hass, entry) == []


def test_build_world_model_end_to_end(registries):
    registries.entities["light.a"] = _entity_entry(device_id="device_1")
    registries.devices["device_1"] = _device_entry(area_id="wohnzimmer")
    registries.areas["wohnzimmer"] = _area_entry(name="Wohnzimmer", floor_id="eg")
    registries.floors["eg"] = _floor_entry(name="Erdgeschoss", level=0)

    hass = _FakeHass(states={
        "light.a": hass_entities.State("light.a", "on", {"friendly_name": "Wohnzimmerlicht"}),
    })
    entry = _FakeEntry(selected_entities=["light.a"])

    world_model = hass_entities.build_world_model(hass, entry)

    assert len(world_model.entities) == 1
    assert len(world_model.devices) == 1
    assert world_model.entities[0].entity_id == "light.a"
    assert world_model.entities[0].floor_name == "Erdgeschoss"

    device = world_model.device_for_entity("light.a")
    assert device is not None
    assert device.device_id == "device_1"
    assert world_model.entities_for_device("device_1") == world_model.entities


def test_entity_snapshot_carries_registry_integration_provenance(registries):
    registries.entities["media_player.box"] = _entity_entry(
        platform="music_assistant", config_entry_id="mass-entry"
    )
    hass = _FakeHass(states={
        "media_player.box": hass_entities.State(
            "media_player.box", "idle", {"friendly_name": "Box"}
        ),
    })

    snapshot = hass_entities.build_entity_snapshots(
        hass, _FakeEntry(selected_entities=["media_player.box"])
    )[0]

    assert snapshot.integration == "music_assistant"
    assert snapshot.config_entry_id == "mass-entry"


def test_live_cover_feature_mask_reaches_group_percentage_command(registries, engine):
    for entity_id, area_id, area_name in (
        ("cover.wohnzimmer", "wohnzimmer", "Wohnzimmer"),
        ("cover.kueche", "kueche", "Küche"),
    ):
        registries.entities[entity_id] = _entity_entry(area_id=area_id)
        registries.areas[area_id] = _area_entry(name=area_name, floor_id="eg")
    registries.entities["cover.garage"] = _entity_entry(area_id="garage")
    registries.areas["garage"] = _area_entry(name="Garage", floor_id="eg")
    registries.floors["eg"] = _floor_entry(name="Erdgeschoss", level=0)

    hass = _FakeHass(states={
        "cover.wohnzimmer": hass_entities.State(
            "cover.wohnzimmer", "closed",
            {"friendly_name": "Rollladen Wohnzimmer", "supported_features": 4},
        ),
        "cover.kueche": hass_entities.State(
            "cover.kueche", "closed",
            {"friendly_name": "Rollladen Küche", "supported_features": 4},
        ),
        "cover.garage": hass_entities.State(
            "cover.garage", "closed",
            {"friendly_name": "Garagentor", "supported_features": 3},
        ),
    })
    entry = _FakeEntry(selected_entities=[
        "cover.wohnzimmer", "cover.kueche", "cover.garage",
    ])

    snapshots = hass_entities.build_entity_snapshots(hass, entry)
    result = engine.match("Fahre alle Rolladen im Erdgeschoss halb runter", snapshots)

    assert result is not None
    assert result.plan.service == "set_cover_position"
    assert sorted(result.plan.entity_id) == ["cover.kueche", "cover.wohnzimmer"]
    assert result.plan.data == {"position": 50}
