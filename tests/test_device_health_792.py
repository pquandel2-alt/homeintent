"""7.9.2 B3: batteries and unreachable devices.

* questions (no "Ja"): "Welche Batterien sind schwach?", "… unter 30 %",
  "Welche Geräte sind nicht erreichbar?" - only released sensors and actors,
  never helpers or scenes whose state is ``unknown`` (that was 7.9.1's
  answer: it listed buttons, scenes and phones);
* monitoring: "Melde dich, wenn ein Gerät nicht mehr erreichbar ist" /
  "… wenn der Bewegungsmelder im Flur ausfällt" - state ``unavailable`` for
  at least 10 minutes (said in the preview), the preview names the count,
  the push names the device; a restart never floods;
* report: "Sag mir jeden Sonntag, welche Batterien unter 30 % sind" - a
  recurring automation whose push lists the batteries at run time.

Effects are checked with ``tests/_ha_sim.py``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _ha_sim import World, for_trigger_fires, run
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities


def _house(monkeypatch, tmp_path, entities=None) -> HouseConversation:
    return HouseConversation(monkeypatch, entities, tmp_path=tmp_path, options=PUSH_OPTIONS)


def _batteries():
    return {e.entity_id: e for e in house_entities() if e.device_class == "battery"}


@pytest.mark.parametrize("question", [
    "Welche Batterien sind schwach?", "Welche Akkus muss ich wechseln?", "Sind Batterien fast leer?",
    "Welche Batterie ist schwach?", "Zeig mir die schwachen Batterien.",
])
def test_weak_batteries(monkeypatch, tmp_path, question):
    turn = _house(monkeypatch, tmp_path).say(question)
    assert turn.speech == (
        "Unter 20 % liegen Batterie Rauchmelder oben (9 %) und Batterie Fenstersensor Bad (14 %)."
    ), turn.speech
    assert turn.calls == []


@pytest.mark.parametrize(("limit", "expected"), [(10, 1), (30, 2), (70, 3), (95, 4)])
@pytest.mark.parametrize("frame", ["Welche Batterien sind unter {n} Prozent?", "Welche Akkus liegen unter {n} %?"])
def test_batteries_below_a_spoken_limit(monkeypatch, tmp_path, limit, expected, frame):
    turn = _house(monkeypatch, tmp_path).say(frame.format(n=limit))
    values = sorted(float(e.state) for e in _batteries().values())
    assert sum(1 for value in values if value < limit) == expected
    assert turn.speech.startswith(f"Unter {limit} %"), turn.speech
    assert turn.speech.count("(") == expected


def test_no_weak_battery(monkeypatch, tmp_path):
    entities = [replace(e, state="80") if e.device_class == "battery" else e for e in house_entities()]
    turn = _house(monkeypatch, tmp_path, entities).say("Welche Batterien sind schwach?")
    assert turn.speech.startswith("Keine Batterie liegt unter 20 %; am niedrigsten"), turn.speech


@pytest.mark.parametrize("question", [
    "Welche Geräte sind nicht erreichbar?", "Ist etwas offline?", "Welche Geräte sind ausgefallen?",
    "Welche Sensoren sind nicht verfügbar?",
])
def test_unreachable_devices(monkeypatch, tmp_path, question):
    entities = [
        replace(e, state="unavailable") if e.entity_id in {"binary_sensor.bewegung_flur", "light.stehlampe"} else e
        for e in house_entities()
    ]
    turn = _house(monkeypatch, tmp_path, entities).say(question)
    assert turn.speech == "Nicht erreichbar sind: Bewegungsmelder Flur und Stehlampe.", turn.speech


def test_unknown_helpers_are_no_unreachable_devices(monkeypatch, tmp_path):
    turn = _house(monkeypatch, tmp_path).say("Welche Geräte sind nicht erreichbar?")
    assert "alle " in turn.speech and "Geräte sind erreichbar" in turn.speech, turn.speech


# --- monitoring -------------------------------------------------------------

@pytest.mark.parametrize("frame", ["Melde dich, wenn {e}.", "Sag mir Bescheid, sobald {e}.", "Wenn {e}, benachrichtige mich."])
@pytest.mark.parametrize("event", [
    "ein Gerät nicht mehr erreichbar ist", "irgendein Gerät ausfällt", "ein Gerät offline geht",
])
def test_any_device_unreachable(monkeypatch, tmp_path, frame, event):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(frame.format(e=event)).speech
    devices = [e for e in house_entities() if e.domain in {
        "sensor", "binary_sensor", "light", "switch", "cover", "climate", "fan", "lock", "valve",
        "media_player", "vacuum", "lawn_mower", "humidifier", "water_heater", "alarm_control_panel",
        "camera", "assist_satellite",
    }]
    assert f"eines der {len(devices)} Geräte länger als 10 Minuten nicht erreichbar" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert trigger["to"] == ["unavailable"] and trigger["for"] == {"seconds": 600}
    assert len(trigger["entity_id"]) == len(devices)
    names = {e.entity_id: e.friendly_name for e in house_entities()}
    states = {e.entity_id: e.state for e in house_entities()}
    states["light.stehlampe"] = "unavailable"
    world = World(states, since={"light.stehlampe": 601}, names=names)
    assert for_trigger_fires(automation, world, "light.stehlampe")
    [sent] = run(automation["actions"], world, trigger_entity="light.stehlampe")
    assert sent.message == "Stehlampe ist seit 10 Minuten nicht erreichbar."


@pytest.mark.parametrize("event", [
    "der Bewegungsmelder im Flur ausfällt", "der Bewegungsmelder im Flur nicht mehr erreichbar ist",
    "der Bewegungsmelder im Flur länger als 30 Minuten nicht erreichbar ist",
])
def test_one_device_unreachable(monkeypatch, tmp_path, event):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"Sag mir Bescheid, wenn {event}.").speech
    assert "Bewegungsmelder Flur länger als" in preview and "nicht erreichbar" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["entity_id"] == "binary_sensor.bewegung_flur"
    expected = 1800 if "30 Minuten" in event else 600
    assert automation["triggers"][0]["for"] == {"seconds": expected}


def test_a_restart_does_not_flood(monkeypatch, tmp_path):
    """After a restart every device passes through ``unavailable``; the
    minimum time counts from the restart: devices back within it send
    nothing, one still gone after it sends exactly one push."""
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn ein Gerät nicht mehr erreichbar ist.")
    house.say("Ja.")
    [automation] = house.automations()
    ids = automation["triggers"][0]["entity_id"]
    names = {e.entity_id: e.friendly_name for e in house_entities()}
    normal = {e.entity_id: e.state for e in house_entities()}
    # 30 s after the restart: all back except the hallway detector.
    states = dict(normal)
    states["binary_sensor.bewegung_flur"] = "unavailable"
    since = {entity: 30 for entity in ids}
    world = World(states, since=since, names=names)
    assert [entity for entity in ids if for_trigger_fires(automation, world, entity)] == []
    # 11 minutes after the restart.
    world = World(states, since={entity: 660 for entity in ids}, names=names)
    fired = [entity for entity in ids if for_trigger_fires(automation, world, entity)]
    assert fired == ["binary_sensor.bewegung_flur"]


# --- report -----------------------------------------------------------------

@pytest.mark.parametrize("schedule", ["jeden Sonntag um 10 Uhr", "sonntags um 10 Uhr", "an jedem Sonntag um 10 Uhr"])
@pytest.mark.parametrize("question", ["welche Batterien unter 30 Prozent sind", "welche Batterien unter 30 % liegen"])
def test_battery_report(monkeypatch, tmp_path, schedule, question):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"Sag mir {schedule}, {question}.").speech
    assert "10:00 Uhr" in preview and "Sonntag" in preview and "Batterien unter 30 %" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"] == [{"trigger": "time", "at": "10:00:00"}]
    world = World({e.entity_id: e.state for e in house_entities()})
    [sent] = run(automation["actions"], world)
    assert sent.message == "Batterien unter 30 %: Batterie Fenstersensor Bad (14 %), Batterie Rauchmelder oben (9 %)"
    for entity in _batteries():
        world.states[entity] = "90"
    [sent] = run(automation["actions"], world)
    assert sent.message == "Keine Batterie liegt unter 30 %."


def test_report_without_time_asks(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    question = house.say("Sag mir jeden Sonntag, welche Batterien schwach sind.").speech
    assert question.startswith("Um wie viel Uhr"), question
    preview = house.say("um 9 Uhr").speech
    assert "09:00 Uhr" in preview and "Batterien unter 20 %" in preview, preview


def test_a_report_is_never_answered_now(monkeypatch, tmp_path):
    turn = _house(monkeypatch, tmp_path).say("Sag mir jeden Sonntag um 10 Uhr, welche Batterien unter 30 Prozent sind.")
    assert "Batterie Rauchmelder oben (9 %)" not in turn.speech
    assert "Soll ich das so einrichten" in turn.speech


def test_unreachable_report(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Sag mir werktags um 7 Uhr, welche Geräte nicht erreichbar sind.").speech
    assert "07:00 Uhr" in preview and "Nicht erreichbar" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    states = {e.entity_id: e.state for e in house_entities()}
    states["light.stehlampe"] = "unavailable"
    [sent] = run(automation["actions"], World(states))
    assert sent.message == "Nicht erreichbar: Stehlampe"
