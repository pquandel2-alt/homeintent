"""7.9.1 A4: a spoken floor, room or "draußen" always limits.

Nachtest 7.9.0 B3: "Melde dich, wenn sich im Keller fünf Stunden nichts
bewegt." watched the hallway detector on the ground floor. Causes: the
inactivity reading rebuilt its subject without the place, and the event
grounding fell back to every candidate (``placed or candidates``). Now a
place filters always, also with a single candidate; without a device there
the answer says so. The same holds for named devices in commands.

Generated: places x inactivity phrasings x durations, places x measured
quantities. The expectation is derived from the house, not listed: the
chosen device is at the spoken place, or HomeIntent says there is none.
"""

from __future__ import annotations

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_PLACES = {
    "im Keller": "keller", "im Obergeschoss": "obergeschoss", "im Erdgeschoss": "erdgeschoss",
    "oben": "obergeschoss", "unten": "erdgeschoss", "draußen": "aussenbereich",
    "in der Garage": "garage", "im Schlafzimmer": "schlafzimmer", "im Wohnzimmer": "wohnzimmer",
}
_INACTIVITY = (
    "Melde dich, wenn sich {place} {duration} nichts bewegt.",
    "Sag mir Bescheid, wenn {place} {duration} keine Bewegung erkannt wird.",
)
_DURATIONS = ("fünf Stunden", "2 Stunden", "30 Minuten")


def _house(monkeypatch, tmp_path) -> HouseConversation:
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)


def _at(entity, key: str) -> bool:
    return key in {entity.area_id, entity.floor_id}


def _detectors_at(key: str) -> set[str]:
    return {
        entity.entity_id for entity in house_entities()
        if entity.domain == "binary_sensor"
        and entity.device_class in {"motion", "occupancy", "presence"} and _at(entity, key)
    }


@pytest.mark.parametrize("duration", _DURATIONS)
@pytest.mark.parametrize("template", _INACTIVITY)
@pytest.mark.parametrize("place", list(_PLACES))
def test_inactivity_watches_a_detector_at_the_spoken_place(monkeypatch, tmp_path, place, template, duration):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(template.format(place=place, duration=duration))
    expected = _detectors_at(_PLACES[place])
    if not expected:
        assert "gibt es keinen Bewegungs- oder Präsenzmelder" in turn.speech, turn.speech
        house.say("Ja.")
        assert house.automations() == []
        return
    assert "Soll ich das so einrichten?" in turn.speech, turn.speech
    house.say("Ja.")
    [automation] = house.automations()
    watched = {
        entity_id
        for trigger in automation["triggers"]
        for entity_id in ([trigger["entity_id"]] if isinstance(trigger["entity_id"], str) else trigger["entity_id"])
    }
    assert watched and watched <= expected, (watched, expected)


_QUANTITIES = {
    "die Temperatur": ("temperature", "über 25 Grad"),
    "die Luftfeuchtigkeit": ("humidity", "über 80 Prozent"),
    "das CO2": ("carbon_dioxide", "über 1200 ppm"),
}


@pytest.mark.parametrize("quantity", list(_QUANTITIES))
@pytest.mark.parametrize("place", ["im Keller", "draußen", "im Obergeschoss", "in der Garage"])
def test_a_measured_quantity_is_read_at_the_spoken_place(monkeypatch, tmp_path, quantity, place):
    device_class, value = _QUANTITIES[quantity]
    key = _PLACES[place]
    expected = {
        entity.entity_id for entity in house_entities()
        if entity.domain == "sensor" and entity.device_class == device_class and _at(entity, key)
    }
    house = _house(monkeypatch, tmp_path)
    turn = house.say(f"Melde dich, wenn {quantity} {place} {value} liegt.")
    house.say("Ja.")
    automations = house.automations()
    if not expected:
        assert automations == [], turn.speech
        assert "kein passendes Gerät" in turn.speech or "Welche" in turn.speech, turn.speech
        return
    if len(expected) > 1:
        # Several at the place: asked - and only devices at the place are offered.
        assert automations == [] and turn.speech.startswith("Welche"), turn.speech
        names = {entity.friendly_name for entity in house_entities()}
        offered = {name for name in names if name in turn.speech}
        allowed = {entity.friendly_name for entity in house_entities() if entity.entity_id in expected}
        assert offered and offered <= allowed, (offered, allowed)
        return
    [automation] = automations
    watched = {trigger["entity_id"] for trigger in automation["triggers"]}
    assert watched <= expected, (watched, expected)


@pytest.mark.parametrize(("command", "place"), [
    ("Schalte die Stehlampe im Keller ein.", "im Keller"),
    ("Mach die Stehlampe oben an.", "oben"),
    ("Schalte die Stehlampe draußen ein.", "draußen"),
])
def test_a_named_device_elsewhere_is_said_not_chosen(monkeypatch, tmp_path, command, place):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(command)
    assert turn.calls == [] or all(service not in {"turn_on", "toggle"} for _, service, _ in turn.calls), turn.calls
    assert "Stehlampe" in turn.speech, turn.speech


def test_the_named_device_at_its_place_still_works(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Schalte die Stehlampe im Wohnzimmer ein.")
    assert "light.stehlampe" in turn.targets
