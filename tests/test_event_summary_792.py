"""7.9.2 B1: "Was war los, während ich weg war?" and summaries of a period.

7.9.1 answered with the *current* state. Now: events of the period from a
synthetic recorder history, most important first, at most five spoken,
the rest on "Was noch?". The absence comes from the speaker's person
history. Privacy: a non-administrator hears only about themselves by
name. Without the recorder: an honest answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 20, 0, tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _at(now: datetime, hour: int, minute: int = 0) -> datetime:
    return now.replace(hour=hour, minute=minute)


def _history(now: datetime) -> dict[str, list[tuple[datetime, str, dict]]]:
    day = _at(now, 0)
    return {
        "person.philipp": [(day, "home", {}), (_at(now, 9), "not_home", {}), (_at(now, 17, 30), "home", {})],
        "person.anna": [(day, "home", {}), (_at(now, 15, 2), "home", {}), (_at(now, 12), "not_home", {}),
                        (_at(now, 15, 2), "home", {})],
        "binary_sensor.haustuer": [(day, "off", {}), (_at(now, 15, 1), "on", {}), (_at(now, 15, 3), "off", {})],
        "binary_sensor.wassermelder_keller": [(day, "off", {}), (_at(now, 11, 20), "on", {})],
        "binary_sensor.bewegung_flur": [(day, "off", {}), (_at(now, 15, 2), "on", {})],
        "binary_sensor.kuechenfenster": [(day, "off", {}), (_at(now, 13), "on", {})],
        "sensor.waschmaschine_status": [(day, "running", {}), (_at(now, 12, 40), "finished", {})],
        "cover.garagentor": [(day, "closed", {}), (_at(now, 16), "open", {}), (_at(now, 16, 5), "closed", {})],
        "binary_sensor.rauchmelder_flur": [(day, "off", {}), (_at(now, 22 - 4), "on", {})],
    }


def _house(monkeypatch, tmp_path, now, user="admin", history=None, recorder=True):
    import homeintent.controllers.insights as insights
    from homeassistant.core import State

    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)
    hass = house.entity.hass
    for person, name in (("person.philipp", "Philipp"), ("person.anna", "Anna")):
        hass.states._states[person] = State(person, "home", {"friendly_name": name})
    data = history if history is not None else _history(now)

    async def rows(hass_, entity_ids, start, end, *, attributes=False):
        if not recorder:
            return None
        # Like the recorder with include_start_time_state: the state valid
        # at the start, then every change inside the window.
        found = {}
        for entity, values in data.items():
            if entity not in entity_ids:
                continue
            ordered = sorted(values, key=lambda item: item[0])
            before = [item for item in ordered if item[0] <= start]
            inside = [item for item in ordered if start < item[0] <= end]
            first = [(start, before[-1][1], before[-1][2])] if before else []
            found[entity] = first + inside
        return found

    monkeypatch.setattr(insights, "async_read_state_rows", rows)
    return house


@pytest.mark.parametrize("question", [
    "Was war los, während ich weg war?", "Was ist passiert, während ich weg war?",
    "Was habe ich verpasst, während ich weg war?", "Was war los, als ich weg war?",
])
def test_while_i_was_away(monkeypatch, tmp_path, now, question):
    speech = _house(monkeypatch, tmp_path, now).say(question).speech
    assert speech.startswith("Während du weg warst (09:00 bis 17:30 Uhr): "), speech
    order = [speech.index(text) for text in (
        "um 11:20 Wassermelder Keller hat ausgelöst",
        "um 13:00 Küchenfenster wurde geöffnet",
        "um 15:01 Haustür wurde geöffnet",
        "um 16:00 Garagentor wurde geöffnet",
    )]
    assert order == sorted(order)  # alarms first, then accesses by time
    assert "Dazu kommen" in speech and "Was noch?" in speech
    assert "Es läuft" not in speech  # never the current state


def test_what_else(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now)
    house.say("Was war los, während ich weg war?")
    rest = house.say("Was noch?").speech
    assert rest.startswith("Außerdem: ") and "Waschmaschine ist fertig" in rest, rest
    assert "Anna ist heimgekommen" in rest


def test_privacy_for_a_non_admin(monkeypatch, tmp_path, now):
    history = _history(now)
    history["person.anna"] = [(_at(now, 0), "home", {}), (_at(now, 9), "not_home", {}), (_at(now, 17, 30), "home", {})]
    history["person.philipp"] = [(_at(now, 0), "home", {}), (_at(now, 12), "not_home", {}), (_at(now, 15, 2), "home", {})]
    house = _house(monkeypatch, tmp_path, now, user="anna", history=history)
    house.say("Was war los, während ich weg war?")
    rest = house.say("Was noch?").speech
    assert "Philipp" not in rest and "jemand ist heimgekommen" in rest, rest


@pytest.mark.parametrize(("question", "start_hour"), [
    ("Was ist heute passiert?", 0), ("Was ist seit 14 Uhr passiert?", 14), ("Was war heute los?", 0),
])
def test_periods(monkeypatch, tmp_path, now, question, start_hour):
    speech = _house(monkeypatch, tmp_path, now).say(question).speech
    if start_hour == 14:
        assert "Wassermelder" not in speech and "um 15:01 Haustür wurde geöffnet" in speech, speech
    else:
        assert "um 11:20 Wassermelder Keller hat ausgelöst" in speech, speech
    assert "Küchenfenster" not in speech  # windows only while away


def test_last_night(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path, now).say("Was war letzte Nacht los?").speech
    assert speech.startswith("Letzte Nacht ist nichts Auffälliges passiert."), speech


def test_without_recorder(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path, now, recorder=False).say("Was ist heute passiert?").speech
    assert "Recorder" in speech and "nicht verfügbar" in speech


def test_never_away(monkeypatch, tmp_path, now):
    history = {"person.philipp": [(_at(now, 0) - timedelta(days=2), "home", {})]}
    speech = _house(monkeypatch, tmp_path, now, history=history).say("Was war los, während ich weg war?").speech
    assert speech == "Laut Verlauf warst du in den letzten 7 Tagen nicht weg."


def test_absence_rule():
    from homeintent.event_summary import last_absence

    t = datetime(2026, 10, 6, 8, 0)
    rows = [(t, "home"), (t + timedelta(hours=1), "not_home"), (t + timedelta(hours=3), "home"),
            (t + timedelta(hours=5), "Arbeit"), (t + timedelta(hours=6), "home")]
    assert last_absence(rows, t + timedelta(hours=10)) == (t + timedelta(hours=5), t + timedelta(hours=6))
    assert last_absence(rows[:4], t + timedelta(hours=10)) == (t + timedelta(hours=5), t + timedelta(hours=10))


@pytest.mark.parametrize("text", ["Was war die Temperatur gestern?", "Wie warm war es gestern?"])
def test_history_questions_are_no_summary(text):
    from homeintent.event_summary import parse_summary_query

    assert parse_summary_query(text, datetime(2026, 10, 6, 12, 0)) is None
