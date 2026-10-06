"""7.9.2 A5: an exact registry name wins before a question.

Nachtest 7.9.1 T5: "Wenn der Stromverbrauch über 3000 Watt geht, warn mich"
asked between four power sensors although exactly one sensor is named
"Stromverbrauch Haus". Rule: if exactly one registry name without a room
contains the spoken noun as a whole word and the quantity fits, that
sensor is meant. Several such names keep the question; the same rule
holds for automations and for questions (commands already resolve
names).

Generated: verbs x comparators x units for the automation; question
frames for the query; a house with a second room-less name keeps asking.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_EVENTS = (
    "Wenn der Stromverbrauch über {value} geht, warn mich.",
    "Wenn der Stromverbrauch {value} übersteigt, sag mir Bescheid.",
    "Sag mir Bescheid, wenn der Stromverbrauch über {value} liegt.",
    "Melde dich, wenn der Stromverbrauch unter {value} fällt.",
)
_VALUES = ("3000 Watt", "3 Kilowatt", "2500 W")
_QUESTIONS = (
    "Wie hoch ist der Stromverbrauch?",
    "Wie hoch ist der Stromverbrauch gerade?",
    "Was ist der Stromverbrauch?",
)


def _second_name():
    entities = house_entities()
    base = next(e for e in entities if e.entity_id == "sensor.stromverbrauch_haus")
    return [*entities, replace(base, entity_id="sensor.stromverbrauch_garage",
                               friendly_name="Stromverbrauch Werkstatt", state="12")]


@pytest.mark.parametrize("value", _VALUES)
@pytest.mark.parametrize("event", _EVENTS)
def test_the_one_exact_name_wins(monkeypatch, tmp_path, event, value):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(event.format(value=value))
    assert "Stromverbrauch Haus" in turn.speech, turn.speech
    assert "meinst du" not in turn.speech


@pytest.mark.parametrize("event", _EVENTS[:2])
def test_two_exact_names_still_ask(monkeypatch, tmp_path, event):
    house = HouseConversation(monkeypatch, _second_name(), tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(event.format(value="3000 Watt"))
    assert "meinst du" in turn.speech, turn.speech
    assert "Stromverbrauch Haus" in turn.speech and "Stromverbrauch Werkstatt" in turn.speech


def test_a_name_with_a_room_does_not_win(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Wenn die Leistung über 3000 Watt geht, warn mich.")
    assert "meinst du" in turn.speech, turn.speech


@pytest.mark.parametrize("question", _QUESTIONS)
def test_questions_use_the_same_rule(monkeypatch, tmp_path, question):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(question)
    assert "432" in turn.speech, turn.speech


def test_questions_with_two_names_do_not_pick_one(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, _second_name(), tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Wie hoch ist der Stromverbrauch?")
    assert "432" not in turn.speech and "12 Watt" not in turn.speech, turn.speech


def test_rule_function():
    from homeintent.nlu.entity_resolution import exact_registry_name

    entities = house_entities()
    power = [e for e in entities if e.device_class == "power"]
    assert [e.entity_id for e in exact_registry_name("Stromverbrauch", power)] == ["sensor.stromverbrauch_haus"]
    assert exact_registry_name("Leistung", power) == ()  # every match has a room
    assert exact_registry_name("Strom", power) == ()  # whole words only
    assert exact_registry_name(None, power) == ()
