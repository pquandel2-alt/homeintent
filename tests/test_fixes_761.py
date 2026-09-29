"""7.6.1: findings of the independent re-test of 7.6.0 (A2-A8).

Each block belongs to one finding; every sentence runs through the real
conversation entity on the stub test house (``tests/_testhaus.py``).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _testhaus import HouseConversation, house_entities  # noqa: E402
from homeintent.nlu.entity_clarification import which_question  # noqa: E402
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402


def _writes(turn) -> list[tuple[str, str, dict]]:
    return [call for call in turn.calls if call[1] not in {"get_items", "get_events"}]


# --------------------------------------------------------------- A2 greetings
@pytest.mark.parametrize("sentence", [
    "Aktiviere Guten Morgen.",
    "Aktiviere die Szene Guten Morgen.",
    "Aktiviere bitte die Szene Guten Morgen.",
])
def test_a2_a_greeting_name_is_activatable(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert _writes(turn) == [("scene", "turn_on", {"entity_id": "scene.guten_morgen"})]
    assert "Guten Morgen aktiviert" in turn.speech


def test_a2_words_inside_a_spoken_name_are_no_time():
    entities = house_entities()
    assert analyse_language("Aktiviere Guten Morgen.", entities).temporal == ()
    # Outside a name "morgen" keeps its meaning.
    assert analyse_language("Schalte morgen das Küchenlicht an.", entities).temporal


def test_a2_time_outside_the_name_still_schedules(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Schalte morgen früh das Küchenlicht an.")
    assert _writes(turn) == []


@pytest.mark.parametrize("sentence", ["Guten Morgen.", "Guten Morgen!", "guten morgen"])
def test_a2_a_bare_greeting_starts_nothing(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    assert _writes(house.say(sentence)) == []


def test_a2_the_question_names_the_genus(monkeypatch):
    base = house_entities()
    scene = next(entity for entity in base if entity.entity_id == "scene.guten_morgen")
    entities = [entity for entity in base if entity.entity_id != "scene.guten_morgen"] + [
        replace(scene, entity_id="scene.guten_morgen_bad", friendly_name="Guten Morgen Bad"),
        replace(scene, entity_id="scene.guten_morgen_kueche", friendly_name="Guten Morgen Küche"),
    ]
    house = HouseConversation(monkeypatch, entities=entities)
    turn = house.say("Aktiviere die Szene Guten Morgen.")
    assert _writes(turn) == []
    assert "Szenen" in turn.speech and "Gerät" not in turn.speech
    scenes = tuple(entity for entity in base if entity.domain == "scene")
    assert which_question(scenes).startswith("Welche Szene meinst du: ")
    mixed = scenes[:1] + tuple(entity for entity in base if entity.domain == "light")[:1]
    assert which_question(mixed).startswith("Welches Gerät meinst du: ")
