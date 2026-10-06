"""7.9.2 A6: the remaining gaps of the Nachtest 7.9.1 (T6).

* Markise automations: "Sonne scheint" is mapped honestly to the outdoor
  brightness sensor and the threshold is asked; "heller als N Lux" and
  "Wind über N km/h" are understood; without a wind sensor HomeIntent says
  so. Never a guessed value.
* "Erinnere mich jede Minute, bis …" asks "nur jetzt oder jedes Mal?" like
  "alle 5 Minuten".
* "… wenn die Außentemperatur schnell fällt" asks amount and period (7.9.1
  part dialog).
* "Wenn ich gehe und noch Licht an ist" - the push names the rooms with
  light on, computed at run time from entity ids.
* "irgendeine Batterie unter 20 %" - every battery sensor, the preview
  names the count, the push names the device and value.
* Pausing and stopping answer in the short form of the list.

Paraphrases are generated (clause forms x actions x values); the effect of
the generated automations is checked with ``tests/_ha_sim.py``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _ha_sim import World, fires, run
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_AWNING_OUT = ("fahre die Markise aus", "öffne die Markise", "mach die Markise auf")
_AWNING_IN = ("fahre die Markise ein", "schließe die Markise", "fahr die Markise ein")


def _house(monkeypatch, tmp_path, entities=None) -> HouseConversation:
    from homeintent.monitor_goal import MonitorGoalStore

    house = HouseConversation(monkeypatch, entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    return house


def _with_wind():
    entities = house_entities()
    base = next(e for e in entities if e.entity_id == "sensor.helligkeit_aussen")
    return [*entities, replace(base, entity_id="sensor.wind", friendly_name="Windgeschwindigkeit",
                               device_class="wind_speed", unit="km/h", state="12")]


# --- Markise -------------------------------------------------------------

@pytest.mark.parametrize("action", _AWNING_OUT)
@pytest.mark.parametrize("clause", ["Wenn die Sonne scheint,", "Sobald die Sonne scheint,", "Wenn die Sonne strahlt,"])
@pytest.mark.parametrize("answer", ["ab 30000 Lux", "30000", "über 30000 Lux"])
def test_sunshine_asks_the_threshold(monkeypatch, tmp_path, clause, action, answer):
    house = _house(monkeypatch, tmp_path)
    question = house.say(f"{clause} {action}.").speech
    assert question.startswith("Ab welcher Helligkeit"), question
    assert "Helligkeit außen" in question and "misst gerade" in question
    preview = house.say(answer).speech
    assert "Helligkeit außen über 30000 lx" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"] == [{"trigger": "numeric_state", "entity_id": "sensor.helligkeit_aussen", "above": 30000.0}]
    assert automation["actions"][0]["action"] == "cover.open_cover"


def test_sunshine_without_brightness_sensor_is_honest(monkeypatch, tmp_path):
    entities = [e for e in house_entities() if e.device_class != "illuminance"]
    house = _house(monkeypatch, tmp_path, entities)
    speech = house.say("Wenn die Sonne scheint, fahre die Markise aus.").speech
    assert "ohne Helligkeitssensor" in speech and house.automations() == []


@pytest.mark.parametrize("action", _AWNING_OUT)
@pytest.mark.parametrize("event", [
    "Wenn es draußen heller als {v} Lux ist,", "Wenn die Helligkeit draußen über {v} Lux liegt,",
    "Sobald es draußen heller als {v} Lux ist,",
])
@pytest.mark.parametrize("value", ["30000", "25000"])
def test_brightness_in_lux(monkeypatch, tmp_path, event, action, value):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"{event.format(v=value)} {action}.").speech
    assert f"Helligkeit außen über {value} lx" in preview, preview
    assert "Sensor im Bereich" not in preview


@pytest.mark.parametrize("action", _AWNING_IN)
@pytest.mark.parametrize("event", [
    "Bei Wind über {v} km/h", "Wenn der Wind über {v} km/h liegt,",
    "Wenn die Windgeschwindigkeit {v} km/h übersteigt,",
])
def test_wind(monkeypatch, tmp_path, event, action):
    house = _house(monkeypatch, tmp_path, _with_wind())
    preview = house.say(f"{event.format(v=40)} {action}.").speech
    assert "Windgeschwindigkeit über 40 km/h" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["entity_id"] == "sensor.wind"
    assert automation["actions"][0]["action"] == "cover.close_cover"


@pytest.mark.parametrize("event", ["Bei Wind über 40 km/h", "Wenn der Wind über 40 km/h liegt,"])
def test_wind_without_sensor_is_honest(monkeypatch, tmp_path, event):
    house = _house(monkeypatch, tmp_path)
    speech = house.say(f"{event} fahr die Markise ein.").speech
    assert "„Wind“" in speech and house.automations() == []
    assert "nicht verstanden" not in speech and "nicht eindeutig erkennen" not in speech


# --- Erinnerung bis … -------------------------------------------------------

@pytest.mark.parametrize("interval", ["jede Minute", "alle 2 Minuten", "alle 5 Minuten"])
@pytest.mark.parametrize("until", [
    "die Markise eingefahren ist", "das Garagentor zu ist", "die Haustür zu ist",
])
def test_reminder_until_asks_once_or_every_time(monkeypatch, tmp_path, interval, until):
    house = _house(monkeypatch, tmp_path)
    speech = house.say(f"Erinnere mich {interval}, bis {until}.").speech
    assert speech.startswith("Nur jetzt oder jedes Mal"), speech


# --- schnell fällt ------------------------------------------------------------

@pytest.mark.parametrize("subject", ["die Außentemperatur", "die Temperatur im Büro"])
@pytest.mark.parametrize("change", ["schnell fällt", "plötzlich steigt", "stark sinkt"])
@pytest.mark.parametrize("frame", ["Sag mir Bescheid, wenn {s} {c}.", "Melde dich, sobald {s} {c}."])
def test_vague_rate_asks_amount_and_period(monkeypatch, tmp_path, subject, change, frame):
    house = _house(monkeypatch, tmp_path)
    question = house.say(frame.format(s=subject, c=change)).speech
    assert question.startswith("Um wie viel und in welchem Zeitraum"), question
    preview = house.say("um 3 Grad in einer Stunde").speech
    assert ("3 °C" in preview or "3 Grad" in preview) and "Stunde" in preview, preview
    assert "das überwache ich selbst" in preview or "überwache" in preview, preview


def test_vague_rate_needs_both_parts(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Sag mir Bescheid, wenn die Außentemperatur schnell fällt.")
    again = house.say("um 3 Grad").speech
    assert "Betrag und Zeitraum" in again, again


# --- Licht beim Gehen -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Wenn ich gehe und noch Licht an ist, sag mir Bescheid.",
    "Sag mir Bescheid, wenn ich gehe und noch Licht an ist.",
])
def test_leaving_names_the_rooms_with_light(monkeypatch, tmp_path, text):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(text).speech
    assert "ist noch Licht an" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    states = {entity.entity_id: "off" for entity in house_entities()}
    states.update({"light.stehlampe": "on", "light.kuechenlicht": "on", "person.philipp": "not_home"})
    world = World(states)
    assert fires(automation, world, "person.philipp", "home")
    [sent] = run(automation["actions"], world)
    # Rooms in a fixed order (by entity id), each named once.
    assert sent.message == "Du hast das Haus verlassen; in der Küche und im Wohnzimmer ist noch Licht an."
    states["light.kuechenlicht"] = "off"
    [sent] = run(automation["actions"], World(states))
    assert sent.message == "Du hast das Haus verlassen; im Wohnzimmer ist noch Licht an."


# --- Batterien ------------------------------------------------------------------

@pytest.mark.parametrize("subject", ["irgendeine Batterie", "eine Batterie", "irgendein Akku"])
@pytest.mark.parametrize("verb", ["unter 20 Prozent fällt", "unter 20 % sinkt", "unter 20 Prozent liegt"])
def test_any_battery(monkeypatch, tmp_path, subject, verb):
    house = _house(monkeypatch, tmp_path)
    batteries = sorted(e.entity_id for e in house_entities() if e.device_class == "battery")
    preview = house.say(f"Sag mir Bescheid, wenn {subject} {verb}.").speech
    assert f"der {len(batteries)} Batterien unter 20 %" in preview, preview
    assert "Sensor" not in preview and "Auslöser eingetreten" not in preview
    house.say("Ja.")
    [automation] = house.automations()
    assert sorted(automation["triggers"][0]["entity_id"]) == batteries
    names = {e.entity_id: e.friendly_name for e in house_entities()}
    states = {entity: "60" for entity in batteries}
    states["sensor.batterie_fenster_kueche"] = "18"
    world = World(states, names=names)
    assert fires(automation, world, "sensor.batterie_fenster_kueche", "21")
    [sent] = run(automation["actions"], world, trigger_entity="sensor.batterie_fenster_kueche")
    assert sent.message == "Batterie Fenstersensor Küche: 18 %"


# --- Kurzform beim Pausieren und Stoppen ------------------------------------------

@pytest.mark.parametrize("request_text", [
    "Stopp die Garagen-Meldung.", "Schalte die Garagen-Überwachung aus.",
    "Pausiere die Garagen-Meldung bis morgen um 7 Uhr.",
])
def test_pause_and_stop_answer_in_short_form(monkeypatch, tmp_path, request_text):
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn das Garagentor länger als 10 Minuten offen ist.")
    house.say("Ja.")
    speech = house.say(request_text).speech
    assert "wenn das Garagentor länger als 10 Minuten offen ist" in speech, speech
    assert "sende ich dir" not in speech and "Push-Benachrichtigung" not in speech, speech
