"""Release 7.3.0 section 7: time language.

Spoken clock times, delays in any word order, wake requests and plural
targets with preview are generated over clock forms x targets x verbs.
No timed sentence may ever execute immediately.  Sentences are built here,
not taken from ``sim/``.
"""

from __future__ import annotations

import itertools

import pytest

from _testhaus import HouseConversation
from custom_components.homeintent.nlu.clock_language import (
    normalize_clock_expressions,
    split_relative_delay,
    wake_request,
)

# (spoken clock, HH:MM)
_CLOCKS = [
    ("halb sieben", "06:30"), ("viertel nach acht", "08:15"), ("viertel vor sieben", "06:45"),
    ("dreiviertel acht", "07:45"), ("zehn nach sechs", "06:10"), ("fünf vor halb neun", "08:25"),
    ("18 Uhr 30", "18:30"), ("sieben Uhr dreißig", "07:30"), ("18:30", "18:30"),
    ("18.30 Uhr", "18:30"), ("halb sieben abends", "18:30"),
]
_TARGETS = [
    ("das Bürolicht", "light.buerolicht", "an"),
    ("die Stehlampe", "light.stehlampe", "aus"),
    ("die Kaffeemaschine", "switch.kaffeemaschine", "an"),
]
_SHAPES = ["Mach um {c} {t} {p}.", "Um {c} mach {t} {p}.", "Schalte um {c} {t} {v}."]
_VERB_PARTICLE = {"an": "ein", "aus": "aus"}


@pytest.mark.parametrize("clock,expected", _CLOCKS)
def test_clock_forms_normalize(clock, expected):
    hour, minute = expected.split(":")
    assert f"um {int(hour)}:{minute} Uhr" in normalize_clock_expressions(f"Mach um {clock} das Licht an.")


@pytest.mark.parametrize("text", [
    "Fahr die Rollläden halb runter.", "Stell die Heizung auf 21.50 Grad.", "Dimme auf halb.",
    "Stell einen Timer auf zehn Minuten.",
])
def test_non_clock_words_are_untouched(text):
    assert normalize_clock_expressions(text) == text


@pytest.mark.parametrize(
    "clock,shape,target", list(itertools.product(_CLOCKS[:6] + _CLOCKS[8:9], _SHAPES, _TARGETS))
)
def test_timed_command_is_a_scheduled_automation_never_immediate(monkeypatch, clock, shape, target):
    spoken, expected = clock
    phrase, entity_id, particle = target
    text = shape.format(c=spoken, t=phrase, p=particle, v=_VERB_PARTICLE[particle])
    house = HouseConversation(monkeypatch)
    turn = house.say(text)
    assert turn.calls == [], text
    assert expected in turn.speech, (text, turn.speech)


@pytest.mark.parametrize("delay,seconds", [
    ("in 150 Minuten", 9000), ("in zwei Stunden und 30 Minuten", 9000),
    ("in einer halben Stunde", 1800), ("in 5 Minuten", 300),
])
@pytest.mark.parametrize("position", ["front", "middle", "end"])
def test_delay_in_any_word_order(delay, seconds, position):
    command = {"front": f"{delay} schließe die Rollläden",
               "middle": f"Schließe {delay} die Rollläden",
               "end": f"Schließe die Rollläden {delay}"}[position]
    split = split_relative_delay(command)
    assert split is not None and split[1] == seconds
    assert "Rollläden" in split[0] and "Minuten" not in split[0]


@pytest.mark.parametrize("text", [
    "Schließe in 150 Minuten die Rollläden.", "In zwei Stunden und 30 Minuten die Rollläden runter.",
    "Mach in zwei Stunden die Rollläden zu.",
])
def test_plural_delayed_target_is_the_genus_with_preview(monkeypatch, tmp_path, text):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path)
    turn = house.say(text)
    assert turn.calls == []
    assert "Rollläden" in turn.speech and "schließen" in turn.speech
    assert "Garagentor" not in turn.speech and "Markise" not in turn.speech
    house.say("Ja")
    actions = house.automations()[-1]["actions"][0]
    assert actions["action"] == "cover.close_cover"
    assert "cover.garagentor" not in actions["target"]["entity_id"]
    assert "cover.markise" not in actions["target"]["entity_id"]


@pytest.mark.parametrize("clock", ["um sieben", "um halb sieben", "um 6:45 Uhr"])
@pytest.mark.parametrize("instrument,expected", [
    ("mit Licht", "das Licht"), ("mit dem Radio", "das Radio"), ("mit der Stehlampe", "die Stehlampe"),
])
def test_wake_request_reads_clock_and_instrument(clock, instrument, expected):
    reading = wake_request(f"Weck mich {clock} {instrument}.")
    assert reading is not None and reading[1] == expected


def test_wake_request_with_light_uses_the_speakers_room(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, area="schlafzimmer", tmp_path=tmp_path)
    turn = house.say("Weck mich um halb sieben mit Licht.")
    assert turn.calls == [] and "06:30" in turn.speech and "Schlafzimmer" in turn.speech
    house.say("Ja")
    targets = house.automations()[-1]["actions"][0]["target"]["entity_id"]
    assert set(targets) == {"light.nachttischlampe_links", "light.nachttischlampe_rechts", "light.schlafzimmerlicht"}


def test_wake_request_without_room_asks(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Weck mich um sieben mit Licht.")
    assert turn.calls == [] and "welchem Raum" in turn.speech
