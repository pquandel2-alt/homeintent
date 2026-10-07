"""7.9.3 A2/A3: "Urlaub" as a device value, and ending the vacation mode.

A2 (Regression aus 7.9.2): "Urlaub/Abwesenheit/Ferien" as the *value* of a
program, mode or preset ("auf Urlaub stellen", "Modus Urlaub") is a device
command; the vacation mode comes only from a statement about people or
travel ("ich bin/wir sind … weg/im Urlaub") or from "Urlaubsmodus bis …".

A3: the end is a construction - return (wieder da, zurück, heim,
angekommen, wieder zuhause) or end (vorbei, zu Ende, beenden, aus,
abschalten) about vacation, trip or absence.  With an active vacation mode
every return statement asks whether to end it; without one the answer is
honest and nothing happens.  Phrasings are generated combinatorially.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import date, datetime

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

TODAY = date(2026, 10, 6)


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 12, 0, tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _house(monkeypatch, tmp_path) -> HouseConversation:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(
        ["person.philipp", "person.anna"], confirmed=True
    ))
    return house


def _store(tmp_path):
    path = tmp_path / "homeintent_vacation.json"
    return json.loads(path.read_text()) if path.exists() else {}


# --- A2: the value of a device setting --------------------------------------

_VERBS = ("Stell {o} auf {v}.", "Setz {o} auf {v}.", "Schalte {o} auf {v}.", "Stelle {o} bitte auf {v}.",
          "Ich stelle {o} auf {v}.", "Kannst du {o} auf {v} stellen?", "{O} auf {v} stellen.")
_OBJECTS = ("das Heizprogramm", "die Heizung", "den Modus der Heizung", "das Programm")
_VALUES = ("Urlaub", "Ferien", "Abwesend", "Abwesenheit")


@pytest.mark.parametrize("verb,obj,value", list(itertools.product(_VERBS, _OBJECTS, _VALUES)))
def test_a_value_of_a_device_setting_is_never_the_vacation_mode(verb, obj, value):
    from homeintent.vacation import device_value_reading, parse_vacation_request

    text = verb.format(o=obj, O=obj[:1].upper() + obj[1:], v=value)
    assert parse_vacation_request(text, TODAY) is None, text
    assert device_value_reading(text), text


@pytest.mark.parametrize("text", [
    "Modus Urlaub für die Heizung.", "Preset Abwesend im Bad.", "Heizprogramm Urlaub.",
    "Betriebsart Urlaub einstellen.", "Programm auf Ferien.",
])
def test_mode_and_preset_nouns_name_a_value(text):
    from homeintent.vacation import parse_vacation_request

    assert parse_vacation_request(text, TODAY) is None, text


_PEOPLE = ("Ich bin", "Wir sind")
_TRIPS = ("bis Sonntag weg", "bis Sonntag im Urlaub", "bis Sonntag auf Urlaub", "bis Sonntag verreist",
          "bis zum 20. in den Ferien")


@pytest.mark.parametrize("who,trip", list(itertools.product(_PEOPLE, _TRIPS)))
def test_a_statement_about_people_stays_the_vacation_mode(who, trip):
    """"Wir sind bis Sonntag auf Urlaub" is about people, not a value."""
    from homeintent.vacation import parse_vacation_request

    request = parse_vacation_request(f"{who} {trip}.", TODAY)
    assert request is not None and request.action == "start" and request.end is not None


@pytest.mark.parametrize("text", ["Urlaubsmodus bis Freitag.", "Schalte den Urlaubsmodus bis Sonntag ein."])
def test_urlaubsmodus_bis_stays_the_vacation_mode(text):
    from homeintent.vacation import parse_vacation_request

    request = parse_vacation_request(text, TODAY)
    assert request is not None and request.action == "start"


@pytest.mark.parametrize("text", ["Stell das Heizprogramm auf Urlaub.", "Setz das Heizprogramm auf Urlaub.",
                                  "Stell das Heizprogramm auf Abwesend."])
def test_the_heating_program_is_switched(monkeypatch, tmp_path, now, text):
    """Live-Befund B5 (7.9.2): the sentence switched the select in 7.9.1."""
    house = _house(monkeypatch, tmp_path)
    turn = house.say(text)
    value = text.rsplit(" ", 1)[-1].rstrip(".")
    assert ("select", "select_option", {"option": value, "entity_id": "select.heizprogramm"}) in turn.calls, turn
    assert "Bis wann" not in turn.speech


# --- A3: ending --------------------------------------------------------------

_SUBJECTS = ("Urlaub", "der Urlaub", "die Reise", "die Ferien", "die Abwesenheit", "der Urlaubsmodus")
_END_FORMS = ("{s} ist vorbei.", "{S} vorbei.", "{S} ist zu Ende.", "Beende {a}.", "{S} beenden.",
              "Schalte {a} aus.", "{S} aus.", "Mach {a} aus.", "{S} ist um.", "Deaktiviere {a}.")


def _accusative(subject: str) -> str:
    return {"der": "den"}.get(subject.split()[0], subject.split()[0]) + " " + subject.split(" ", 1)[1] \
        if " " in subject else subject


@pytest.mark.parametrize("form,subject", list(itertools.product(_END_FORMS, _SUBJECTS)))
def test_end_constructions(form, subject):
    from homeintent.vacation import parse_vacation_request

    text = form.format(s=subject, S=subject[:1].upper() + subject[1:], a=_accusative(subject))
    request = parse_vacation_request(text, TODAY)
    assert request is not None and request.action == "end", text


_RETURNS = ("Wir sind wieder da.", "Ich bin wieder da.", "Wir sind zurück.", "Ich bin wieder zurück.",
            "Wir sind wieder zuhause.", "Ich bin wieder zu Hause.", "Ich bin wieder daheim.",
            "Wir sind heimgekommen.", "Wir sind angekommen.", "Ich bin heim.", "Wieder da!",
            "Wir sind gerade angekommen.", "Ich bin zurückgekommen.")


@pytest.mark.parametrize("text", _RETURNS)
def test_return_constructions(text):
    from homeintent.vacation import parse_vacation_request

    request = parse_vacation_request(text, TODAY)
    assert request is not None and request.action == "return", text


@pytest.mark.parametrize("text", ["Wir sind aus dem Urlaub zurück.", "Ich bin von der Reise zurück.",
                                  "Wir sind aus den Ferien wieder da.", "Wir sind vom Urlaub heimgekommen."])
def test_return_from_a_named_trip_is_an_end(text):
    from homeintent.vacation import parse_vacation_request

    request = parse_vacation_request(text, TODAY)
    assert request is not None and request.action == "end", text


@pytest.mark.parametrize("text", [
    "Wann ist der Urlaub vorbei?", "Wie lange läuft der Urlaubsmodus noch?", "Wenn wir zurück sind, mach Licht.",
    "Sobald ich wieder da bin, schalte die Heizung ein.", "Ist Anna schon zurück?", "Wer ist wieder da?",
    "Schalte den Urlaubsmodus nicht aus.",
])
def test_questions_conditions_and_negations_are_no_end(text):
    from homeintent.vacation import parse_vacation_request

    request = parse_vacation_request(text, TODAY)
    assert request is None or request.action == "status", text


def _start(house):
    house.say("Ich bin bis Sonntag weg.")
    assert house.say("Ja.").speech.startswith("Der Urlaubsmodus läuft")


@pytest.mark.parametrize("text", _RETURNS[:8] + ("Urlaub vorbei.", "Die Reise ist zu Ende.",
                                               "Wir sind aus dem Urlaub zurück."))
def test_with_active_vacation_every_return_asks_and_yes_ends(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path)
    _start(house)
    question = house.say(text).speech
    assert "Soll ich?" in question and "lösche seine" in question, question
    if text in _RETURNS:
        assert question.startswith("Willkommen zurück! Der Urlaubsmodus läuft noch bis 11.10.")
    assert house.automations()  # nothing before "Ja"
    done = house.say("Ja.")
    assert done.speech == "Der Urlaubsmodus ist beendet; alles ist zurückgenommen."
    assert house.automations() == [] and _store(tmp_path) == {}


def test_with_active_vacation_no_keeps_it(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    _start(house)
    house.say("Wir sind wieder da.")
    assert house.say("Nein.").speech == "In Ordnung, ich ändere nichts."
    assert len(house.automations()) == 2 and _store(tmp_path)


def test_after_the_end_no_vacation_message_remains(monkeypatch, tmp_path, now):
    """The monitoring automation is gone, so a window opening sends nothing
    (HA evaluation model: no trigger left for the window)."""
    from _ha_sim import World, trigger_fires

    house = _house(monkeypatch, tmp_path)
    _start(house)
    world = World({"binary_sensor.kuechenfenster": "on"})

    def triggered(automations):
        return [item for item in automations
                if any(trigger_fires(t, world, "binary_sensor.kuechenfenster", "off") for t in item["triggers"])]

    [monitor] = triggered(house.automations())
    assert monitor["actions"][0]["action"] == "notify.send_message"
    house.say("Wir sind wieder zuhause.")
    house.say("Ja.")
    assert triggered(house.automations()) == []


@pytest.mark.parametrize("text", ["Urlaub vorbei.", "Die Reise ist zu Ende.", "Wir sind aus dem Urlaub zurück."])
def test_without_active_vacation_honest_and_nothing_happens(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(text)
    assert turn.speech == "Der Urlaubsmodus ist nicht aktiv; ich habe nichts geändert."
    assert turn.calls == []


@pytest.mark.parametrize("text", ["Wir sind wieder da.", "Ich bin wieder zuhause.", "Ich bin wieder daheim."])
def test_without_active_vacation_a_return_reads_on_without_effect(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(text)
    assert "Urlaub" not in turn.speech and turn.calls == [], turn
    assert "nicht verstanden" not in turn.speech


def test_the_helper_switch_off_stays_a_device_command(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Schalte den Urlaubsmodus aus.")
    assert ("homeassistant", "turn_off", {"entity_id": "input_boolean.urlaubsmodus"}) in [
        (domain, service, data) for domain, service, data in turn.calls
    ] or any(service == "turn_off" for _, service, _ in turn.calls), turn
