"""7.9 W4: consumption, meters and power.

Power with a duration is a numeric-state trigger with ``for``.  "heute" or
"diese Woche" is only answerable by a meter that restarts with that period
(the utility meter's ``meter_period`` attribute - a name is no evidence);
HomeIntent never computes it from a total counter and says which helper is
missing.  W, kW, Wh and kWh are kept apart: an amount is never a rate.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _ha_sim import World, for_trigger_fires
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

WASHER = "sensor.leistung_waschmaschine"


def _create(monkeypatch, tmp_path, text: str, entities=None) -> tuple[str, dict]:
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(text)
    assert preview.calls == [], text
    assert "Soll ich das so einrichten" in preview.speech, (text, preview.speech)
    assert house.automations() == []
    house.say("Ja.")
    [automation] = house.automations()
    return preview.speech, automation


def _refused(monkeypatch, tmp_path, text: str, entities=None) -> str:
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(text)
    assert turn.calls == [] and "Soll ich das so einrichten" not in turn.speech, (text, turn.speech)
    house.say("Ja.")
    assert house.automations() == []
    return turn.speech


_NOTIFY = ("Melde dich", "Sag mir Bescheid", "Warne mich")
# All mean: washing machine power above 3000 W for 5 minutes.
_WASHER_POWER = (
    "die Leistung der Waschmaschine länger als 5 Minuten über 3000 Watt liegt",
    "die Leistung der Waschmaschine länger als 5 Minuten über 3 kW liegt",
    "die Leistung der Waschmaschine seit mehr als 5 Minuten über 3000 W liegt",
    "die Leistung der Waschmaschine 5 Minuten lang über 3 Kilowatt liegt",
)


@pytest.mark.parametrize("event", _WASHER_POWER)
@pytest.mark.parametrize("notify", _NOTIFY)
def test_power_with_a_duration(monkeypatch, tmp_path, notify, event):
    speech, automation = _create(monkeypatch, tmp_path, f"{notify}, wenn {event}.")
    [trigger] = automation["triggers"]
    assert trigger["trigger"] == "numeric_state" and trigger["entity_id"] == WASHER
    assert trigger["above"] == 3000 and trigger["for"] == {"seconds": 300}
    assert "länger als 5 Minuten über 3000 W" in speech
    assert automation["actions"][0]["data"]["message"] == "Die Leistung Waschmaschine liegt seit 5 Minuten über 3000 W."


def test_power_fires_only_after_the_duration(monkeypatch, tmp_path):
    _, automation = _create(monkeypatch, tmp_path, f"Melde dich, wenn {_WASHER_POWER[0]}.")
    trigger = automation["triggers"][0]
    # The numeric-state trigger's "for" holds like a state trigger's.
    trigger_as_state = {**trigger, "trigger": "state", "to": "high"}
    world = World({WASHER: "high"}, since={WASHER: 200})
    assert not for_trigger_fires({"triggers": [trigger_as_state]}, world, WASHER)
    world.since[WASHER] = 300
    assert for_trigger_fires({"triggers": [trigger_as_state]}, world, WASHER)


def _with_daily_meter() -> list:
    base = house_entities()
    counter = next(entity for entity in base if entity.entity_id == "sensor.energiezaehler")
    daily = replace(
        counter, entity_id="sensor.stromverbrauch_heute", friendly_name="Stromverbrauch heute",
        aliases=(), state="4.2",
        attributes={**counter.attributes, "friendly_name": "Stromverbrauch heute",
                    "meter_period": "daily", "source": "sensor.energiezaehler"},
    )
    return [*base, daily]


@pytest.mark.parametrize("text", [
    "Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.",
    "Melde dich, wenn der Energieverbrauch heute über 10 Kilowattstunden liegt.",
    "Sag mir Bescheid, wenn heute mehr als 10000 Wh Strom verbraucht wurden.",
])
def test_today_needs_a_daily_meter_and_uses_it(monkeypatch, tmp_path, text):
    speech, automation = _create(monkeypatch, tmp_path, text, _with_daily_meter())
    [trigger] = automation["triggers"]
    assert trigger["trigger"] == "numeric_state"
    assert trigger["entity_id"] == "sensor.stromverbrauch_heute" and trigger["above"] == 10


def test_without_a_daily_meter_it_names_the_missing_helper(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.")
    assert "Verbrauchszähler" in speech and "täglichem Zyklus" in speech and "Energiezähler" in speech
    assert "Gesamtzähler rechne ich nicht selbst" in speech


@pytest.mark.parametrize("text", [
    "Sag mir Bescheid, wenn die Waschmaschine mehr als 2 kWh verbraucht hat.",
    "Sag mir Bescheid, wenn der Stromverbrauch über 10 kWh liegt.",
])
def test_an_amount_without_a_period_asks_since_when(monkeypatch, tmp_path, text):
    speech = _refused(monkeypatch, tmp_path, text)
    assert "heute, diese Woche oder diesen Monat" in speech


def test_ambiguous_power_asks(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Melde dich, wenn die Leistung länger als 5 Minuten über 3000 Watt liegt.")
    assert speech.startswith("Welche Leistung meinst du")


def test_an_amount_is_never_a_rate(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Melde dich, wenn die Leistung der Waschmaschine über 2 kWh liegt.")
    assert "Leistung (W) und Energie (kWh) sind verschiedene Größen" in speech
