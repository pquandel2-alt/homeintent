"""7.9.3 A4: summary - paraphrases, order, dates, bounds.

* The speaker's absence is a construction: subordinator (während, als,
  seit, solange) + "ich" + away predicate (weg, unterwegs, fort, gegangen,
  nicht da/zuhause, aus dem Haus), "in/während meiner Abwesenheit", and
  "Was hab ich verpasst?" (generated combinatorially).
* Within the same importance in time order; the speaker's own coming and
  going once and short.
* Several days: the heading names date and time, entries of another day
  name the day.
* Only relevant classes are read, bounded entity count, bounded period.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest

import _ha_stub
from test_event_summary_792 import _at, _history, _house

_ha_stub.install()


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 20, 0, tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


_ASK = ("Was ist passiert, {c}?", "Was war los, {c}?", "Was hat sich getan, {c}?", "{C} – was ist passiert?",
        "Was gab es Neues, {c}?")
_CLAUSES = ("seit ich weg war", "seit ich gegangen bin", "als ich weg war", "während ich unterwegs war",
            "solange ich fort war", "als ich nicht da war", "während ich nicht zuhause war",
            "seit ich aus dem Haus bin", "in meiner Abwesenheit", "während meiner Abwesenheit")


@pytest.mark.parametrize("ask,clause", list(itertools.product(_ASK, _CLAUSES)))
def test_absence_paraphrases(ask, clause):
    from homeintent.event_summary import parse_summary_query

    text = ask.format(c=clause, C=clause[:1].upper() + clause[1:])
    query = parse_summary_query(text, datetime(2026, 10, 6, 20, 0))
    assert query is not None and query.away, text


@pytest.mark.parametrize("text", ["Was hab ich verpasst?", "Was habe ich verpasst?", "Hab ich was verpasst?",
                                  "Was habe ich versäumt?"])
def test_what_did_i_miss_is_my_absence(text):
    from homeintent.event_summary import parse_summary_query

    query = parse_summary_query(text, datetime(2026, 10, 6, 20, 0))
    assert query is not None and query.away, text


@pytest.mark.parametrize("text", [
    "Was war die Temperatur gestern?", "Wie warm war es, als ich weg war?", "Seit wann bin ich weg?",
    "Ich bin weg.", "Wann war ich weg?", "Mach das Licht aus, wenn ich weg bin.",
])
def test_no_summary(text):
    from homeintent.event_summary import parse_summary_query

    assert parse_summary_query(text, datetime(2026, 10, 6, 20, 0)) is None, text


@pytest.mark.parametrize("question", ["Was ist passiert, seit ich weg war?", "Was hab ich verpasst?",
                                      "Was war los, in meiner Abwesenheit?"])
def test_live_paraphrase_answers_from_the_absence(monkeypatch, tmp_path, now, question):
    speech = _house(monkeypatch, tmp_path, now).say(question).speech
    assert speech.startswith("Während du weg warst (09:00 bis 17:30 Uhr): "), speech


def test_same_importance_is_chronological_and_own_moves_once(monkeypatch, tmp_path, now):
    """Live-Befund: "um 16:06 …; um 16:08 …; um 16:05 du bist gegangen"."""
    day = _at(now, 0)
    history = {
        "person.philipp": [(day, "home", {}), (_at(now, 16, 5), "not_home", {}), (_at(now, 16, 40), "home", {})],
        "binary_sensor.terrassentuer": [(day, "off", {}), (_at(now, 16, 8), "on", {})],
        "binary_sensor.haustuer": [(day, "off", {}), (_at(now, 16, 6), "on", {}), (_at(now, 16, 7), "off", {})],
        "cover.garagentor": [(day, "closed", {}), (_at(now, 16, 1), "open", {})],
    }
    speech = _house(monkeypatch, tmp_path, now, history=history).say("Was ist heute passiert?").speech
    order = [speech.index(text) for text in ("16:01 Garagentor", "16:06 Haustür", "16:08 Terrassentür")]
    assert order == sorted(order), speech
    assert speech.count("du ") == 1 and "du warst von 16:05 bis 16:40 Uhr weg" in speech, speech
    assert speech.index("16:08 Terrassentür") < speech.index("du warst"), speech


def test_multi_day_absence_names_dates(monkeypatch, tmp_path, now):
    two_days_ago = now - timedelta(days=2)
    history = _history(now)
    history["person.philipp"] = [(two_days_ago.replace(hour=0), "home", {}),
                                 (two_days_ago.replace(hour=14), "not_home", {}),
                                 (_at(now, 18), "home", {})]
    history["binary_sensor.haustuer"] = [(two_days_ago.replace(hour=0), "off", {}),
                                         (two_days_ago.replace(hour=15, minute=5), "on", {}),
                                         (two_days_ago.replace(hour=15, minute=6), "off", {}),
                                         (_at(now, 15, 1), "on", {})]
    speech = _house(monkeypatch, tmp_path, now, history=history).say("Was hab ich verpasst?").speech
    assert speech.startswith("Während du weg warst (So, 04.10. 14:00 Uhr bis Di, 06.10. 18:00 Uhr): "), speech
    assert "am So, 04.10. um 15:05 Haustür wurde geöffnet" in speech, speech
    assert "um 15:01 Haustür wurde geöffnet" in speech and "am Di" not in speech  # today: no day


def test_yesterday_entries_say_gestern(monkeypatch, tmp_path, now):
    yesterday = now - timedelta(days=1)
    history = {"binary_sensor.haustuer": [(yesterday.replace(hour=0), "off", {}),
                                          (yesterday.replace(hour=9, minute=15), "on", {})]}
    speech = _house(monkeypatch, tmp_path, now, history=history).say("Was war gestern los?").speech
    assert "gestern um 09:15 Haustür wurde geöffnet" in speech, speech


def test_only_relevant_classes_are_read(monkeypatch, tmp_path, now):
    from _testhaus import house_entities
    from homeintent.event_summary import history_entities

    ids = set(history_entities(house_entities(), None))
    assert "binary_sensor.haustuer" in ids and "binary_sensor.rauchmelder_flur" in ids
    assert "sensor.waschmaschine_status" in ids and "lock.haustuerschloss" in ids
    for unrelated in ("sensor.temperatur_kueche", "sensor.batterie_fenster_bad", "light.stehlampe",
                      "sensor.stromverbrauch_haus", "cover.wohnzimmer_rollladen_links", "switch.kaffeemaschine"):
        assert unrelated not in ids, unrelated


def test_bounds_are_enforced():
    from homeintent.entities import EntitySnapshot
    from homeintent.event_summary import MAX_EVENTS, MAX_HISTORY_ENTITIES, history_entities, summarize

    many = [EntitySnapshot(f"binary_sensor.tuer_{index}", f"Tür {index}", "binary_sensor", "off",
                           device_class="door") for index in range(3 * MAX_HISTORY_ENTITIES)]
    assert len(history_entities(many, None)) == MAX_HISTORY_ENTITIES
    start = datetime(2026, 10, 1, 0, 0)
    history = {
        entity.entity_id: [(start, "off")] + [
            (start + timedelta(minutes=10 * step), "on" if step % 2 else "off") for step in range(1, 40)
        ] for entity in many[:100]
    }
    events = summarize(history, many, start, start + timedelta(days=1), away=False,
                       speaker_person=None, speaker_is_admin=True)
    assert len(events) == MAX_EVENTS

