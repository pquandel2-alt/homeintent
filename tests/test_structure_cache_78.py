"""7.8 B8: index and house graph are cached per registry/exposure/alias
state and rebuilt by any change of it; states are always live."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.structure_cache import SHARED, structure_key  # noqa: E402


def _with(entities, entity_id, **changes):
    return [replace(entity, **changes) if entity.entity_id == entity_id else entity for entity in entities]


def test_state_change_keeps_the_key_and_structure_change_changes_it():
    entities = house_entities()
    key = structure_key(entities)
    assert structure_key(_with(entities, "light.stehlampe", state="on")) == key
    for changes in (
        {"friendly_name": "Leselampe"},
        {"area_id": "buro", "area_name": "Büro"},
        {"floor_id": "og", "floor_name": "Obergeschoss"},
        {"aliases": ("Sofalicht",)},
        {"device_class": "other"},
    ):
        assert structure_key(_with(entities, "light.stehlampe", **changes)) != key, changes
    withdrawn = [entity for entity in entities if entity.entity_id != "light.stehlampe"]
    assert structure_key(withdrawn) != key


def test_a_withdrawn_device_is_gone_on_the_very_next_turn(monkeypatch):
    house = HouseConversation(monkeypatch)
    assert house.say("Schalte die Stehlampe ein.").targets == {"light.stehlampe"}
    house.entities = [entity for entity in house.entities if entity.entity_id != "light.stehlampe"]
    house.conversation_id = "after-withdrawal"
    turn = house.say("Schalte die Stehlampe ein.")
    assert turn.calls == [], turn.speech


def test_rename_area_move_and_alias_apply_immediately(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Schalte die Stehlampe ein.")
    house.entities = _with(house.entities, "light.stehlampe", friendly_name="Leselampe")
    house.conversation_id = "renamed"
    assert house.say("Schalte die Leselampe ein.").targets == {"light.stehlampe"}
    house.entities = _with(house.entities, "light.stehlampe", aliases=("Sofalicht",))
    house.conversation_id = "alias"
    assert house.say("Schalte das Sofalicht ein.").targets == {"light.stehlampe"}
    house.entities = _with(
        house.entities, "light.buerolicht", area_id="garage", area_name="Garage",
        floor_id="aussenbereich", floor_name="Außenbereich",
    )
    house.conversation_id = "moved"
    targets = house.say("Schalte das Licht in der Garage ein.").targets
    assert "light.buerolicht" in targets, targets


def test_states_are_read_live_not_from_the_cache(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.entities = _with(house.entities, "light.stehlampe", state="off")
    house.conversation_id = "off"
    assert house.say("Ist die Stehlampe an?").speech.startswith("Nein")
    builds = SHARED.builds
    house.entities = _with(house.entities, "light.stehlampe", state="on")
    house.conversation_id = "on"
    speech = house.say("Ist die Stehlampe an?").speech
    assert speech.startswith("Ja"), speech
    assert SHARED.builds == builds  # a state change rebuilt nothing
    node = house.entity._house_graph.node("entity:light.stehlampe")
    assert node is not None and node.attributes["state"] == "on"
