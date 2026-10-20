"""End-to-end composition of scope, property, degree and discourse."""

from __future__ import annotations

import _ha_stub
from dataclasses import replace

from _testhaus import HouseConversation, house_entities

_ha_stub.install()


def test_room_light_degree_and_pronoun_compose_across_turns(monkeypatch):
    house = HouseConversation(monkeypatch)

    brighter = house.say("Mach bitte im Wohnzimmer das Licht etwas heller.")
    off = house.say("Mach es dort wieder aus.")

    expected = {
        "light.stehlampe",
        "light.wohnzimmer_deckenlicht",
        "light.wohnzimmer_led_streifen",
    }
    assert brighter.speech == "3 Lichter um 5 Prozent heller gestellt."
    assert set(brighter.calls[0][2]["entity_id"]) == expected
    assert brighter.calls[0][2]["brightness_step_pct"] == 5
    assert off.speech == "3 Lichter ausgeschaltet."
    assert set(off.calls[0][2]["entity_id"]) == expected


def test_cover_step_with_weiter_is_relative_to_observed_position(monkeypatch):
    entities = [
        replace(entity, attributes={**entity.attributes, "current_position": 45})
        if entity.entity_id == "cover.buero_raffstore" else entity
        for entity in house_entities()
    ]
    house = HouseConversation(monkeypatch, entities=entities)

    turn = house.say("Fahre den Büro Rollladen 10 Prozent weiter hoch.")

    assert turn.calls == [
        (
            "cover",
            "set_cover_position",
            {"position": 55, "entity_id": "cover.buero_raffstore"},
        )
    ]


def test_cover_step_with_weiter_uses_bounded_default_step(monkeypatch):
    entities = [
        replace(entity, attributes={**entity.attributes, "current_position": 45})
        if entity.entity_id == "cover.buero_raffstore" else entity
        for entity in house_entities()
    ]
    house = HouseConversation(monkeypatch, entities=entities)

    turn = house.say("Fahre den Büro Rollladen etwas weiter herunter.")

    assert turn.calls == [
        (
            "cover",
            "set_cover_position",
            {"position": 35, "entity_id": "cover.buero_raffstore"},
        )
    ]


def test_cover_step_without_observed_position_fails_closed(monkeypatch):
    entities = [
        replace(
            entity,
            attributes={
                key: value
                for key, value in entity.attributes.items()
                if key != "current_position"
            },
        )
        if entity.entity_id == "cover.buero_raffstore" else entity
        for entity in house_entities()
    ]
    house = HouseConversation(monkeypatch, entities=entities)

    turn = house.say("Fahre den Büro Rollladen 10 Prozent weiter hoch.")

    assert turn.calls == []
    assert "nichts ausgeführt" in turn.speech
