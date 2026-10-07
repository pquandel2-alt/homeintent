"""7.9.3 B2: where is someone - read-only, from ``person.*``.

Generated paraphrases × person state (zuhause, unterwegs, Zone, unbekannt)
× rights (Administrator, Nicht-Administrator, the person themselves) ×
option "Aufenthaltsort im Haushalt teilen".  Never coordinates.  "Wann
kommt … heim?" is honest (no data) and offers at most the usual arrival
time from the history, marked as a habit value.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities, with_states

_ha_stub.install()

NOW = datetime(2026, 10, 7, 18, 0)  # a Wednesday


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = NOW.replace(tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _house(monkeypatch, tmp_path, now, *, user="admin", share=False, anna="Arbeit", since_hours=2,
           history=None, recorder=True):
    import homeintent.controllers.insights as insights
    from homeassistant.core import State

    entities = with_states(house_entities(), person__anna=anna)
    options = {**PUSH_OPTIONS, "share_household_location": share}
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=options, user=user)
    for person, state in (("person.anna", anna), ("person.philipp", "home")):
        item = State(person, state, {"latitude": 48.1, "longitude": 11.5, "gps_accuracy": 10})
        item.last_changed = now - timedelta(hours=since_hours)
        house.entity.hass.states._states[person] = item

    async def rows(hass, ids, start, end, *, attributes=False):
        if not recorder:
            return None
        return {key: [(moment, state, {}) for moment, state in value] for key, value in (history or {}).items()
                if key in ids}

    monkeypatch.setattr(insights, "async_read_state_rows", rows)
    return house


_WHERE = ("Wo ist Anna?", "Wo ist die Anna?", "Wo steckt Anna?", "Wo ist Anna gerade?", "Wo befindet sich Anna?")


@pytest.mark.parametrize("text", _WHERE)
@pytest.mark.parametrize("user,share,zone", [
    ("admin", False, True), ("anna", False, True), ("child", False, False), ("child", True, True),
])
def test_where_with_rights(monkeypatch, tmp_path, now, text, user, share, zone):
    house = _house(monkeypatch, tmp_path, now, user=user, share=share)
    if user == "child":
        import types

        async def get_user(user_id):
            return types.SimpleNamespace(id="child", name="Lena", is_admin=False)

        house.entity.hass.auth = types.SimpleNamespace(async_get_user=get_user)
    speech = house.say(text).speech
    if zone:
        assert speech == "Anna ist in der Zone „Arbeit“, seit 16:00 Uhr.", speech
    else:
        assert speech == "Anna ist unterwegs, seit 16:00 Uhr.", speech
    assert "48" not in speech and "11,5" not in speech and "11.5" not in speech  # never coordinates


@pytest.mark.parametrize("anna,expected", [
    ("home", "Anna ist zuhause, seit 16:00 Uhr."), ("not_home", "Anna ist unterwegs, seit 16:00 Uhr."),
    ("unknown", "Von Anna habe ich keinen Standort; ich kann dazu nichts sagen."),
])
def test_where_states(monkeypatch, tmp_path, now, anna, expected):
    assert _house(monkeypatch, tmp_path, now, anna=anna).say("Wo ist Anna?").speech == expected


_SOMEONE = ("Ist jemand zuhause?", "Ist irgendwer daheim?", "Ist jemand zu Hause?", "Ist irgendjemand zuhause?")


@pytest.mark.parametrize("text", _SOMEONE)
@pytest.mark.parametrize("anna,expected", [
    ("home", "Zuhause sind Anna und Philipp. Von Lena habe ich keinen Standort."),
    ("not_home", "Zuhause ist Philipp. Von Lena habe ich keinen Standort."),
])
def test_someone_home(monkeypatch, tmp_path, now, text, anna, expected):
    assert _house(monkeypatch, tmp_path, now, anna=anna).say(text).speech == expected


def test_nobody_home(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now, anna="not_home")
    house.entities = with_states(house.entities, person__philipp="not_home")
    speech = house.say("Ist jemand zuhause?").speech
    assert speech == "Von den Personen mit Standort ist niemand zuhause. Von Lena habe ich keinen Standort."


@pytest.mark.parametrize("text", ["Seit wann ist Anna weg?", "Seit wann ist Anna unterwegs?", "Wann ist Anna gegangen?"])
def test_since_when_away(monkeypatch, tmp_path, now, text):
    assert _house(monkeypatch, tmp_path, now, anna="not_home").say(text).speech == "Anna ist seit 16:00 Uhr unterwegs."


def test_since_yesterday(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path, now, anna="not_home", since_hours=26).say("Seit wann ist Anna weg?").speech
    assert speech == "Anna ist seit gestern, 16:00 Uhr unterwegs."


@pytest.mark.parametrize("text", ["Wann ist Anna heimgekommen?", "Wann ist Anna nach Hause gekommen?",
                                  "Wann ist Anna angekommen?"])
def test_arrived(monkeypatch, tmp_path, now, text):
    assert _house(monkeypatch, tmp_path, now, anna="home").say(text).speech == "Anna ist um 16:00 Uhr heimgekommen."


def test_arrived_from_history_while_away(monkeypatch, tmp_path, now):
    history = {"person.anna": [(now - timedelta(days=1, hours=3), "not_home"), (now - timedelta(days=1, hours=1), "home"),
                               (now - timedelta(hours=2), "not_home")]}
    speech = _house(monkeypatch, tmp_path, now, anna="not_home", history=history).say("Wann ist Anna heimgekommen?").speech
    assert speech == "Anna ist gerade unterwegs; zuletzt heimgekommen ist Anna gestern um 17:00 Uhr."


def _workday_arrivals(now, hour, minute, count):
    rows = []
    day = now - timedelta(days=1)
    while len([1 for _, state in rows if state == "home"]) < count:
        if day.weekday() < 5:
            rows.append((day.replace(hour=7), "not_home"))
            rows.append((day.replace(hour=hour, minute=minute), "home"))
        day -= timedelta(days=1)
    return sorted(rows)


@pytest.mark.parametrize("text", ["Wann kommt Philipp heim?", "Wann kommt Philipp nach Hause?", "Wann kommt Philipp zurück?"])
def test_will_arrive_is_honest_with_habit(monkeypatch, tmp_path, now, text):
    history = {"person.philipp": _workday_arrivals(now, 17, 30, 6)}
    house = _house(monkeypatch, tmp_path, now, history=history)
    house.entities = with_states(house.entities, person__philipp="not_home")
    speech = house.say(text).speech
    assert speech.startswith("Wann Philipp heimkommt, kann ich nicht wissen – dafür habe ich keine Daten."), speech
    assert "Als Gewohnheit aus dem Verlauf: werktags kam Philipp meist gegen 17:30 Uhr heim" in speech
    assert "keine Vorhersage" in speech


def test_will_arrive_without_enough_history(monkeypatch, tmp_path, now):
    history = {"person.philipp": _workday_arrivals(now, 17, 30, 2)}
    house = _house(monkeypatch, tmp_path, now, history=history)
    house.entities = with_states(house.entities, person__philipp="not_home")
    speech = house.say("Wann kommt Philipp heim?").speech
    assert speech == "Wann Philipp heimkommt, kann ich nicht wissen – dafür habe ich keine Daten."


def test_will_arrive_without_recorder(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now, recorder=False)
    house.entities = with_states(house.entities, person__philipp="not_home")
    assert house.say("Wann kommt Philipp heim?").speech.endswith("dafür habe ich keine Daten.")


def test_established_answers_stay(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now, anna="home")
    assert house.say("Wer ist zuhause?").speech == "Zuhause: Anna, Philipp."
    assert house.say("Ist Philipp schon zuhause?").speech == "Philipp ist laut Home Assistant zuhause."


@pytest.mark.parametrize("text", list(itertools.chain(
    ["Wo ist die Fernbedienung?", "Wo ist das Licht an?", "Wenn Anna heimkommt, mach Licht.",
     "Ist das Fenster zu?", "Wann ist die Waschmaschine fertig?"],
)))
def test_no_presence_question(text):
    from homeintent.presence_query import parse_presence_query

    assert parse_presence_query(text, ["Anna", "Philipp", "Lena"]) is None, text


def test_usual_arrival_is_a_median_of_the_same_kind_of_day():
    from homeintent.presence_query import usual_arrival

    times = [NOW.replace(day=day, hour=17, minute=minute) for day, minute in ((5, 10), (6, 30), (2, 50), (1, 20))]
    assert usual_arrival(times, NOW) == (17, 25, 4)
    weekend = [NOW.replace(day=4, hour=12)] * 3
    assert usual_arrival(weekend, NOW) is None  # Saturdays do not count for a Wednesday
