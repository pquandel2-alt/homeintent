"""7.9.1 A3: "über/unter … geht" is a threshold, not somebody leaving.

Nachtest 7.9.0 B4 (regression from 7.8.3): "Ping mich an, wenn die
Temperatur im Schlafzimmer über 24 Grad geht." was read as "jemand verlässt
das Haus", because ``_LEAVE_RE`` read every clause-final "geht" as leaving.
Structural rule: a comparator-value phrase before the verb, or a measured
value/device as subject, makes "gehen" a value predicate.

Generated: comparators x units x "geht/gehen" x clause order. Each reading
must equal the reading with "steigt/fällt/liegt". "Wenn ich gehe" and "Wenn
Anna geht" stay presence.
"""

from __future__ import annotations

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, offers_setup

# (subject phrase, plural subject phrase, value, unit word)
_SUBJECTS = (
    ("die Temperatur im Schlafzimmer", "die Temperaturen im Schlafzimmer", "24", "Grad"),
    ("die Luftfeuchtigkeit im Badezimmer", "die Luftfeuchtigkeiten im Badezimmer", "70", "Prozent"),
    ("das CO2 im Wohnzimmer", "die CO2-Werte im Wohnzimmer", "1200", "ppm"),
    ("das CO2 im Wohnzimmer", "die CO2-Werte im Wohnzimmer", "1200", ""),
    ("die Leistung der Waschmaschine", "die Leistungen der Waschmaschine", "2000", "Watt"),
)
_COMPARATORS = {
    "über": "steigt", "mehr als": "steigt", "auf über": "steigt", "höher als": "steigt",
    "unter": "fällt", "weniger als": "fällt", "auf unter": "fällt", "niedriger als": "fällt",
}
_NOTIFY = ("Sag mir Bescheid", "Melde dich")


def _house(monkeypatch, tmp_path) -> HouseConversation:
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)


def _trigger(house: HouseConversation, text: str) -> dict:
    turn = house.say(text)
    assert offers_setup(turn.speech), (text, turn.speech)
    assert "verlässt" not in turn.speech, turn.speech
    house.say("Ja.")
    [automation] = house.automations()[-1:]
    [trigger] = automation["triggers"]
    return {key: trigger.get(key) for key in ("trigger", "entity_id", "above", "below")}


def _sentence(notify: str, clause: str, front: bool) -> str:
    return f"Wenn {clause}, {notify.casefold().replace('sag', 'sag', 1)}." if front else f"{notify}, wenn {clause}."


@pytest.mark.parametrize("front", [False, True], ids=["wenn-hinten", "wenn-vorn"])
@pytest.mark.parametrize("comparator", list(_COMPARATORS))
@pytest.mark.parametrize(("subject", "plural", "value", "unit"), _SUBJECTS)
def test_geht_reads_like_steigt_and_liegt(monkeypatch, tmp_path, subject, plural, value, unit, comparator, front):
    phrase = f"{comparator} {value} {unit}".strip()
    expected_verb = _COMPARATORS[comparator]
    readings = []
    for index, clause in enumerate((
        f"{subject} {phrase} geht",
        f"{plural} {phrase} gehen",
        f"{subject} {phrase} {expected_verb}",
        f"{subject} {phrase} liegt",
    )):
        house = _house(monkeypatch, tmp_path / str(index))
        readings.append(_trigger(house, _sentence(_NOTIFY[index % 2], clause, front)))
    assert readings[0] == readings[2] == readings[3], readings
    assert readings[1]["trigger"] == readings[0]["trigger"]
    assert readings[1]["above"] == readings[0]["above"] and readings[1]["below"] == readings[0]["below"]


@pytest.mark.parametrize(("text", "person"), [
    ("Wenn ich gehe, sag mir Bescheid.", None),
    ("Sag mir Bescheid, wenn Anna geht.", "person.anna"),
    ("Wenn Anna geht, melde dich.", "person.anna"),
    ("Melde dich, wenn Philipp geht.", "person.philipp"),
])
def test_people_who_go_still_leave(monkeypatch, tmp_path, text, person):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(text)
    assert "das Haus verlässt" in turn.speech, turn.speech
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert trigger["entity_id"].startswith("person.")
    if person is not None:
        assert trigger["entity_id"] == person


@pytest.mark.parametrize("clause", [
    "die Temperatur im Schlafzimmer über 24 Grad geht",
    "das CO2 im Wohnzimmer auf über 1200 geht",
    "die Luftfeuchtigkeit im Badezimmer unter 40 Prozent geht",
])
def test_reader_gives_no_presence(clause):
    from homeintent.automation_language import read_event_roles

    roles = read_event_roles(clause)
    assert roles.presence is None
    assert roles.value is not None
