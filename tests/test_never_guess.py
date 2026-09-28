"""Phase 3 (7.3.3): never guess - place, once vs. recurring, honest reasons."""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation
from custom_components.homeintent.nlu.recurrence import (
    Recurrence,
    answer_recurrence,
    recurrence_of,
)

AUTO = {"implicit_action_level": "low_risk_auto"}


# ------------------------------------------------------------ "hier"/"da"
@pytest.mark.parametrize("sentence", ["Mir ist hier kalt.", "Hier ist es zu dunkel.", "Hier ist es muffig."])
def test_here_without_satellite_or_discourse_asks_for_the_room(monkeypatch, sentence):
    turn = HouseConversation(monkeypatch, options=AUTO).say(sentence)
    assert turn.calls == [], turn.speech
    assert "welchem Raum" in turn.speech, turn.speech


def test_here_with_satellite_acts_in_that_room_and_names_it(monkeypatch):
    turn = HouseConversation(monkeypatch, area="kinderzimmer", options=AUTO).say("Mir ist hier kalt.")
    assert turn.targets == {"climate.heizung_kinderzimmer"}, turn.speech
    assert "Kinderzimmer" in turn.speech


def test_here_after_a_place_in_the_conversation_uses_that_place(monkeypatch):
    house = HouseConversation(monkeypatch, options=AUTO)
    house.say("Wie warm ist es im Büro?")
    turn = house.say("Mir ist hier kalt.")
    assert turn.targets == {"climate.heizung_buero"}, turn.speech
    assert "Büro" in turn.speech


# ------------------------------------------------------------ once/recurring
@pytest.mark.parametrize("text,expected", [
    ("Schalte um 22 Uhr das Flurlicht aus.", Recurrence.UNSPECIFIED),
    ("Schalte jeden Tag um 22 Uhr das Flurlicht aus.", Recurrence.RECURRING),
    ("Schalte werktags um 7 Uhr die Kaffeemaschine ein.", Recurrence.RECURRING),
    ("Schalte an Werktagen um 7 Uhr die Kaffeemaschine ein.", Recurrence.RECURRING),
    ("Mach immer bei Sonnenuntergang das Licht an.", Recurrence.RECURRING),
    ("Schalte heute um 22 Uhr alle Lichter aus.", Recurrence.ONCE),
    ("Schalte um 22 Uhr alle Lichter aus.", Recurrence.UNSPECIFIED),
    ("Gieße alle zwei Tage die Pflanzen.", Recurrence.RECURRING),
])
def test_recurrence_markers(text, expected):
    assert recurrence_of(text) is expected


@pytest.mark.parametrize("answer,expected", [
    ("Nur heute.", Recurrence.ONCE), ("Einmal.", Recurrence.ONCE),
    ("Jeden Tag.", Recurrence.RECURRING), ("Immer.", Recurrence.RECURRING),
    ("Hm.", Recurrence.UNSPECIFIED),
])
def test_recurrence_answers(answer, expected):
    assert answer_recurrence(answer) is expected


def test_clock_time_without_marker_is_a_one_time_command(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Schalte um 22 Uhr das Flurlicht aus.")
    assert turn.calls == []
    assert "22:00" in turn.speech and "nach der ersten Ausführung automatisch gelöscht" in turn.speech


def test_clock_time_with_marker_is_a_recurring_automation(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Schalte jeden Tag um 22 Uhr das Flurlicht aus.")
    assert turn.calls == []
    assert "22:00" in turn.speech and "gelöscht" not in turn.speech


@pytest.mark.parametrize("sentence", [
    "Mach bei Sonnenuntergang das Flurlicht an.", "Wenn es dunkel wird, mach das Flurlicht an.",
])
def test_typically_recurring_event_without_marker_asks(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    ask = house.say(sentence)
    assert ask.calls == [] and ask.speech.endswith("Nur heute oder jeden Tag?"), ask.speech
    once = house.say("Nur heute.")
    assert "nur einmal" in once.speech and "Soll diese Automation erstellt werden?" in once.speech


def test_recurring_answer_gives_the_recurring_preview(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Mach bei Sonnenuntergang das Flurlicht an.")
    daily = house.say("Jeden Tag.")
    assert "Sonnenuntergang" in daily.speech and "gelöscht" not in daily.speech


# ------------------------------------------------------------ honest reasons
@pytest.mark.parametrize("sentence", [
    "Dimme das Flurlicht auf 30 Prozent.", "Stell das Flurlicht auf 30 Prozent.",
    "Mach das Flurlicht blau.",
])
def test_capability_refusal_names_what_the_device_can_do(monkeypatch, sentence):
    turn = HouseConversation(monkeypatch).say(sentence)
    assert turn.calls == []
    assert "Flurlicht lässt sich nur ein- und ausschalten" in turn.speech, turn.speech
    assert "nicht eindeutig unterstützt" not in turn.speech
    assert "unterstützen diese Aktion nicht" not in turn.speech


def test_turning_up_heating_in_discourse_raises_the_setpoint(monkeypatch):
    house = HouseConversation(monkeypatch)
    house.say("Wie warm ist es im Kinderzimmer?")
    turn = house.say("Dann dreh da die Heizung hoch.")
    assert turn.targets == {"climate.heizung_kinderzimmer"}, turn.speech
    assert "unterstützen" not in turn.speech


# ------------------------------------------------------------ keep as is
@pytest.mark.parametrize("sentence", [
    "Lass das Flurlicht so, wie es ist.", "Lass die Rollläden so wie sie sind.", "Lass alles so.",
])
def test_keep_as_is_does_nothing_and_says_so(monkeypatch, sentence):
    turn = HouseConversation(monkeypatch).say(sentence)
    assert turn.calls == [] and "Ich ändere nichts" in turn.speech, turn.speech


def test_bare_yes_without_open_question_is_answered_honestly(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Ja.")
    assert turn.calls == [] and "keine Frage offen" in turn.speech
