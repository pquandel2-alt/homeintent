"""7.9.2 A4: a floor watched by fewer detectors than it has rooms says so.

Nachtest 7.9.1 T4: "Melde dich, wenn sich im Obergeschoss zwei Stunden
nichts bewegt" watched only the bedroom's presence detector; the preview
did not say that bathroom and children's room stay unwatched. Now the
preview names every room of the floor (derived from the registry as
HomeIntent sees it) that the chosen detectors do not cover.

Generated: floors x inactivity phrasings x durations; the expected rooms
are computed from the house, not listed.
"""

from __future__ import annotations

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_FLOORS = {"im Obergeschoss": "obergeschoss", "oben": "obergeschoss", "im Erdgeschoss": "erdgeschoss",
           "unten": "erdgeschoss"}
_PHRASES = (
    "Melde dich, wenn sich {place} {duration} nichts bewegt.",
    "Sag mir Bescheid, wenn {place} {duration} keine Bewegung erkannt wird.",
)
_DURATIONS = ("zwei Stunden", "30 Minuten", "3 Stunden")


def _rooms(floor_id: str) -> set[str]:
    return {entity.area_name for entity in house_entities() if entity.floor_id == floor_id and entity.area_name}


@pytest.mark.parametrize("duration", _DURATIONS)
@pytest.mark.parametrize("phrase", _PHRASES)
@pytest.mark.parametrize("place", list(_FLOORS))
def test_unwatched_rooms_are_named(monkeypatch, tmp_path, place, phrase, duration):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(phrase.format(place=place, duration=duration)).speech
    house.say("Ja.")
    [automation] = house.automations()
    watched_ids = automation["triggers"][0]["entity_id"]
    watched_ids = [watched_ids] if isinstance(watched_ids, str) else watched_ids
    by_id = {entity.entity_id: entity for entity in house_entities()}
    watched_rooms = {by_id[item].area_name for item in watched_ids}
    unwatched = _rooms(_FLOORS[place]) - watched_rooms
    assert unwatched, "test house: every floor has a room without the chosen detector"
    for room in unwatched:
        assert room in preview.split("Soll ich")[0].split("Startet Home Assistant")[-1], (room, preview)
    assert "nicht beobachten" in preview or "unbeobachtet" in preview, preview


def test_the_obergeschoss_sentence(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say("Melde dich, wenn sich im Obergeschoss zwei Stunden nichts bewegt.").speech
    assert (
        "Im Obergeschoss gibt es nur im Schlafzimmer einen Melder; Badezimmer, Flur Obergeschoss "
        "und Kinderzimmer kann ich nicht beobachten."
    ) in preview, preview


@pytest.mark.parametrize("place", ["im Flur", "im Schlafzimmer", "im Wohnzimmer"])
def test_a_room_says_nothing_extra(monkeypatch, tmp_path, place):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(f"Melde dich, wenn sich {place} zwei Stunden nichts bewegt.").speech
    assert "nicht beobachten" not in preview and "unbeobachtet" not in preview, preview


def test_a_floor_without_detector_keeps_the_791_answer(monkeypatch, tmp_path):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say("Melde dich, wenn sich im Keller zwei Stunden nichts bewegt.")
    assert "Im Keller gibt es keinen Bewegungs- oder Präsenzmelder" in turn.speech
