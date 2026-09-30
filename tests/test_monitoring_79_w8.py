"""7.9 W8: managing monitors in plain words.

"Welche Überwachungen laufen?" lists HomeIntent's notification automations
(their spoken preview, stored as the automation's description - never YAML
or entity ids) and HomeIntent's own monitors together.  Stopping disables,
deleting asks first, pausing needs an end time and resumes on its own.
Several matches ask which one is meant.
"""

from __future__ import annotations

import asyncio

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation
from homeintent.monitor_goal import MonitorGoalStore


def _house(monkeypatch, tmp_path) -> HouseConversation:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    return house


def _setup(house: HouseConversation) -> None:
    for text in (
        "Beobachte die Fenster und warne mich, wenn eins offen ist und niemand zuhause ist.",
        "Überwache das Garagentor und melde dich, wenn es länger als 10 Minuten offen ist.",
    ):
        assert "Soll ich das so einrichten" in house.say(text).speech
        house.say("Ja.")
    house.say("Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.")
    house.say("Ja.")


@pytest.mark.parametrize("question", [
    "Welche Überwachungen laufen?", "Was überwachst du gerade?", "Welche Meldungen hast du eingerichtet?",
    "Zeig mir meine Überwachungen.",
])
def test_listing_names_every_monitor_in_plain_words(monkeypatch, tmp_path, question):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    turn = house.say(question)
    assert turn.speech.startswith("Es laufen 3 Überwachungen"), turn.speech
    assert "ein Fenster offen ist" in turn.speech
    assert "Garagentor länger als 10 Minuten offen" in turn.speech
    assert "Temperatur Keller" in turn.speech and "das überwache ich selbst" in turn.speech
    assert "binary_sensor." not in turn.speech and "cover." not in turn.speech and "trigger" not in turn.speech
    assert turn.calls == []


def test_nothing_to_list(monkeypatch, tmp_path):
    assert "keine Überwachung" in _house(monkeypatch, tmp_path).say("Welche Überwachungen laufen?").speech


@pytest.mark.parametrize("command", [
    "Stopp die Fensterüberwachung.", "Beende die Überwachung der Fenster.", "Schalte die Fenster-Meldung aus.",
])
def test_stop_disables_the_matching_monitor(monkeypatch, tmp_path, command):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    turn = house.say(command)
    assert turn.speech.startswith("Ausgeschaltet"), turn.speech
    windows = [a for a in house.automations() if "Fenster" in a["alias"]]
    assert [a.get("initial_state") for a in windows] == [False]
    assert all(a.get("initial_state", True) for a in house.automations() if a not in windows)
    assert "(ausgeschaltet)" in house.say("Welche Überwachungen laufen?").speech


def test_stop_a_homeintent_monitor(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    assert house.say("Stopp die Überwachung der Temperatur im Keller.").speech.startswith("Ausgeschaltet")
    [record] = asyncio.run(house.entity._runtime_data.monitor_goals.async_load())
    assert record.enabled is False


def test_delete_asks_and_only_yes_deletes(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    ask = house.say("Lösche die Garagen-Meldung.")
    assert ask.speech.startswith("Soll ich die Überwachung") and len(house.automations()) == 2
    house.say("Nein.")
    assert len(house.automations()) == 2
    house.say("Lösche die Garagen-Meldung.")
    assert house.say("Ja.").speech.startswith("Gelöscht")
    assert [a["alias"] for a in house.automations() if "Garagentor" in a["alias"]] == []


def test_pause_until_tomorrow_resumes_on_its_own(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    turn = house.say("Pausiere die Garagen-Meldung bis morgen um 7 Uhr.")
    assert "Pausiert bis morgen um 07:00 Uhr" in turn.speech and "automatisch wieder ein" in turn.speech
    automations = house.automations()
    garage = next(a for a in automations if "Garagentor" in a["alias"])
    assert garage.get("initial_state") is False
    resume = next(a for a in automations if a["alias"].startswith("HomeIntent: Automation"))
    assert resume["triggers"][0]["at"] == "07:00:00"
    assert resume["actions"][0]["action"] == "homeintent.enable_automation"


def test_pause_without_an_end_asks_until_when(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    assert house.say("Pausiere die Garagen-Meldung bis morgen.").speech.startswith("Bis wann?")
    assert all(a.get("initial_state", True) for a in house.automations())


def test_several_matches_ask(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    house.say("Warne mich, wenn ein Fenster länger als 20 Minuten offen ist.")
    house.say("Ja.")
    turn = house.say("Stopp die Fensterüberwachung.")
    assert "Welche meinst du" in turn.speech
    assert all(a.get("initial_state", True) for a in house.automations())


def test_unknown_monitor_is_said(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    _setup(house)
    assert "keine Überwachung" in house.say("Stopp die Rauchmelder-Überwachung.").speech
