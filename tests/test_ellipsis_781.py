"""7.8.1: an ellipsis that names an unknown object never falls back to the
previous target ("Mach das Flurlicht an." -> "Und Deckenfluter aus." with a
Deckenfluter HomeIntent does not know)."""

from __future__ import annotations

import pytest

from _testhaus import HouseConversation, house_entities

AUTO = {"implicit_action_level": "low_risk_auto"}
READS = frozenset({("recorder", "get_statistics"), ("persistent_notification", "create")})


def writes(turn) -> list[tuple[str, str]]:
    return [(domain, service) for domain, service, _data in turn.calls if (domain, service) not in READS]


def _house(monkeypatch, hidden: str | None = None) -> HouseConversation:
    entities = [entity for entity in house_entities() if entity.entity_id != hidden]
    return HouseConversation(monkeypatch, entities=entities, options=AUTO)


@pytest.mark.parametrize("follow", ["Und Deckenfluter aus.", "Deckenfluter aus.", "Jetzt Blumenkohl aus."])
def test_unknown_object_is_named_and_nothing_runs(monkeypatch, follow):
    house = _house(monkeypatch, hidden="light.deckenfluter_buero")
    house.say("Mach das Flurlicht an.")
    turn = house.say(follow)
    assert writes(turn) == [], turn.speech
    assert "finde ich nicht" in turn.speech and "nichts ausgeführt" in turn.speech


@pytest.mark.parametrize("follow,target", [
    ("Und wieder aus.", "light.flurlicht"),
    ("Oben auch.", "light.flurlicht_oben"),
])
def test_known_followups_still_work(monkeypatch, follow, target):
    house = _house(monkeypatch)
    house.say("Mach das Flurlicht an.")
    turn = house.say(follow)
    assert {entity for _domain, _service, data in turn.calls for entity in _ids(data)} == {target}, turn.speech


@pytest.mark.parametrize("text", [
    "Kannst du es pausieren?", "Dann mach sie auf 23 Grad.", "Und jetzt bitte stoppen.", "Lass es laufen.",
])
def test_verbs_in_the_rest_are_no_objects(text):
    from homeintent.nlu.ellipsis_contract import ellipsis_fields

    assert ellipsis_fields(text, house_entities()).unknown == ()


def _ids(data: dict) -> list[str]:
    raw = data.get("entity_id", [])
    return [raw] if isinstance(raw, str) else list(raw)
