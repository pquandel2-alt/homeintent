"""Release 7.3.0 section 3: situation views answer from observed states.

Views are generated over places, states and question shapes; every answer
must be read-only and must reflect the current state of the test house.
Sentences are built here, not taken from ``sim/``.
"""

from __future__ import annotations

import itertools

import pytest

from _testhaus import HouseConversation, house_entities, with_states
from custom_components.homeintent.nlu.situation_views import (
    HUMIDITY_VENTILATE_PERCENT,
    SituationView,
    answer_situation_view,
)

_STILL_ON = ["Ist {p} noch was an?", "Ist {p} noch etwas an?", "Läuft {p} noch irgendwas?"]
_PLACES = [
    ("unten", "light.stehlampe", "light.schlafzimmerlicht"),
    ("oben", "light.schlafzimmerlicht", "light.stehlampe"),
    ("im Wohnzimmer", "light.stehlampe", "light.kuechenlicht"),
]


def _quiet_house(**states):
    entities = house_entities()
    quiet = {
        entity.entity_id.replace(".", "__"): "off"
        for entity in entities
        if entity.domain in {"light", "switch", "fan"} or entity.domain == "media_player"
    }
    quiet.update(states)
    return with_states(entities, **quiet)


@pytest.mark.parametrize("shape,place", list(itertools.product(_STILL_ON, _PLACES)))
def test_still_on_names_exactly_the_devices_on_at_the_place(monkeypatch, shape, place):
    spoken, inside, outside = place
    house = HouseConversation(monkeypatch, _quiet_house(**{
        inside.replace(".", "__"): "on", outside.replace(".", "__"): "on",
    }))
    turn = house.say(shape.format(p=spoken))
    assert turn.calls == []
    name = {e.entity_id: e.friendly_name for e in house_entities()}
    assert name[inside] in turn.speech, turn.speech
    assert name[outside] not in turn.speech, turn.speech


@pytest.mark.parametrize("question", [
    "Ist unten noch was an?", "Habe ich vergessen, etwas auszuschalten?", "Ist noch was an?",
])
def test_still_on_says_no_when_everything_is_off(monkeypatch, question):
    turn = HouseConversation(monkeypatch, _quiet_house()).say(question)
    assert turn.speech.startswith("Nein"), turn.speech
    assert turn.calls == []


@pytest.mark.parametrize("question", [
    "Ist das Haus abgeschlossen?", "Ist alles zu?", "Ist alles sicher?", "Ist überall abgeschlossen?",
])
@pytest.mark.parametrize("window_open", [True, False])
def test_secure_view_reports_open_windows(monkeypatch, question, window_open):
    entities = house_entities()
    windows = {
        e.entity_id.replace(".", "__"): ("on" if window_open and e.entity_id.endswith("kuechenfenster") else "off")
        for e in entities if e.domain == "binary_sensor" and e.device_class in {"window", "door", "garage_door", "opening"}
    }
    locks = {e.entity_id.replace(".", "__"): "locked" for e in entities if e.domain == "lock"}
    covers = {e.entity_id.replace(".", "__"): "closed" for e in entities if e.domain == "cover"}
    turn = HouseConversation(monkeypatch, with_states(entities, **windows, **locks, **covers)).say(question)
    assert turn.calls == []
    assert turn.speech.startswith("Nein" if window_open else "Ja"), turn.speech
    assert ("Küchenfenster" in turn.speech) is window_open


def test_ventilate_uses_documented_humidity_threshold():
    entities = [e for e in house_entities() if not (e.domain == "sensor" and e.device_class == "carbon_dioxide")]
    humidity = [e for e in entities if e.domain == "sensor" and e.device_class == "humidity"]
    assert humidity
    high = with_states(entities, **{humidity[0].entity_id.replace(".", "__"): str(HUMIDITY_VENTILATE_PERCENT + 5)})
    low = with_states(entities, **{
        e.entity_id.replace(".", "__"): str(HUMIDITY_VENTILATE_PERCENT - 10) for e in humidity
    })
    assert answer_situation_view("Muss ich lüften?", high).text.startswith("Ja")
    assert answer_situation_view("Sollte ich lüften?", low).text.startswith("Nein")


@pytest.mark.parametrize("question", ["Warum ist es im Büro so kalt?", "Wieso ist es im Büro so warm?"])
def test_why_temperature_names_setpoint_and_measurement(monkeypatch, question):
    turn = HouseConversation(monkeypatch).say(question)
    assert turn.calls == []
    assert "Heizung Büro" in turn.speech and "Grad" in turn.speech


@pytest.mark.parametrize("state,expected", [("on", "Ja"), ("off", "Nein")])
@pytest.mark.parametrize("question", ["Ist jemand im Wohnzimmer?", "Ist im Wohnzimmer jemand?"])
def test_presence_per_room(monkeypatch, state, expected, question):
    entities = house_entities()
    sensors = {
        e.entity_id.replace(".", "__"): state for e in entities
        if e.domain == "binary_sensor" and e.area_id == "wohnzimmer"
        and e.device_class in {"occupancy", "presence", "motion"}
    }
    turn = HouseConversation(monkeypatch, with_states(entities, **sensors)).say(question)
    assert turn.speech.startswith(expected), turn.speech


def test_controllable_lists_only_actuators_of_the_room(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Was kann ich im Wohnzimmer steuern?")
    assert "Stehlampe" in turn.speech and "Bürolicht" not in turn.speech
    assert "Temperatur" not in turn.speech


@pytest.mark.parametrize("question,present,absent", [
    ("Welche Räume gibt es oben?", "Schlafzimmer", "Küche"),
    ("Welche Räume gibt es unten?", "Küche", "Schlafzimmer"),
])
def test_rooms_per_floor(monkeypatch, question, present, absent):
    turn = HouseConversation(monkeypatch).say(question)
    assert present in turn.speech and absent not in turn.speech


@pytest.mark.parametrize("noun", ["Lampen", "Lichter", "Fenster", "Heizungen", "Rollläden"])
def test_count_matches_genus_members(noun):
    answer = answer_situation_view(f"Wie viele {noun} gibt es?", house_entities())
    assert answer is not None and answer.view is SituationView.COUNT
    assert f" {len(answer.entities)} " in answer.text


def test_routine_view_describes_configured_script_steps():
    steps = [
        {"action": "light.turn_off", "target": {"area_id": ["wohnzimmer", "kueche"]}},
        {"action": "lock.lock", "target": {"entity_id": "lock.haustuerschloss"}},
    ]
    entities = house_entities()
    script = next(e for e in entities if e.domain == "script")
    answer = answer_situation_view(
        f"Was macht das Skript {script.friendly_name}?", entities, routine_steps=lambda _: steps,
    )
    assert answer is not None and "ausschalten" in answer.text and "abschließen" in answer.text


@pytest.mark.parametrize("request_text", [
    "Kannst du alles zumachen?", "Mach unten alles aus.", "Schalte das Licht im Büro ein.",
])
def test_requests_are_never_views(request_text):
    assert answer_situation_view(request_text, house_entities()) is None
