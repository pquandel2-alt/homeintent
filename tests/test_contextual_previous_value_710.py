"""A previous setpoint is typed, scoped and never guessed."""

from __future__ import annotations

import _ha_stub
from _testhaus import HouseConversation, house_entities

_ha_stub.install()


def _living_area() -> str:
    return next(
        entity.area_id
        for entity in house_entities()
        if entity.entity_id == "climate.heizung_wohnzimmer"
    )


def _climate_calls(turn):
    return [
        (service, data)
        for domain, service, data in turn.calls
        if domain == "climate"
    ]


def test_relative_temperature_can_restore_verified_previous_setpoint(monkeypatch):
    house = HouseConversation(monkeypatch, area=_living_area())

    warmer = house.say("Mach die Heizung zwei Grad wärmer.")
    restored = house.say("Stell sie wieder auf den Wert von vorher.")

    assert _climate_calls(warmer) == [
        ("set_temperature", {"temperature": 23.0, "entity_id": "climate.heizung_wohnzimmer"})
    ]
    assert _climate_calls(restored) == [
        ("set_temperature", {"temperature": 21.0, "entity_id": "climate.heizung_wohnzimmer"})
    ]
    assert restored.speech == "Heizung Wohnzimmer auf 21 Grad gestellt."


def test_previous_setpoint_is_not_shared_between_users(monkeypatch):
    house = HouseConversation(monkeypatch, area=_living_area())
    house.say("Mach die Heizung zwei Grad wärmer.")

    house.user = "anna"
    foreign = house.say("Stell sie wieder auf den Wert von vorher.")

    assert _climate_calls(foreign) == []


def test_previous_setpoint_without_evidence_never_executes(monkeypatch):
    house = HouseConversation(monkeypatch, area=_living_area())

    turn = house.say("Stell die Heizung wieder auf den Wert von vorher.")

    assert _climate_calls(turn) == []
