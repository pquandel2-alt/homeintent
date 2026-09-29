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


# ------------------------------------------------------ A3 relative amounts
@pytest.mark.parametrize(("sentence", "temperature"), [
    ("Mach die Heizung im Bad zwei Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad 2 Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad um zwei Grad wärmer.", 24.0),
    ("Mach die Heizung im Bad drei Grad kälter.", 19.0),
    ("Dreh die Heizung im Bad um drei Grad hoch.", 25.0),
    ("Heizung im Bad zwei Grad höher.", 24.0),
    ("Mach die Heizung im Bad 1,5 Grad kälter.", 20.5),
    ("Mach die Heizung im Bad ein halbes Grad wärmer.", 22.5),
    ("Mach die Heizung im Bad wärmer.", 23.0),
])
def test_a3_amount_sets_the_climate_step(monkeypatch, sentence, temperature):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert _writes(turn) == [(
        "climate", "set_temperature",
        {"temperature": temperature, "entity_id": "climate.heizung_badezimmer"},
    )]


def test_a3_device_limits_still_apply(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Mach die Heizung im Bad zwanzig Grad wärmer.")
    ((_, _, data),) = _writes(turn)
    assert data["temperature"] == 30.0
    assert "Grenzwert" in turn.speech


@pytest.mark.parametrize(("sentence", "step"), [
    ("Mach die Stehlampe 20 Prozent heller.", 20),
    ("Mach die Stehlampe zwanzig Prozent heller.", 20),
    ("Mach die Stehlampe um dreißig Prozent dunkler.", -30),
    ("Mach die Stehlampe etwas dunkler.", -5),
])
def test_a3_amount_sets_the_brightness_step(monkeypatch, sentence, step):
    house = HouseConversation(monkeypatch)
    ((domain, _, data),) = _writes(house.say(sentence))
    assert domain in {"light", "homeassistant"} and data["brightness_step_pct"] == step


def test_a3_amount_changes_the_volume_relative_to_now(monkeypatch):
    house = HouseConversation(monkeypatch)
    radio = next(e for e in house.entities if e.entity_id == "media_player.kuechenradio")
    now = float(radio.attributes["volume_level"])
    ((_, service, data),) = _writes(house.say("Mach das Radio in der Küche zehn Prozent lauter."))
    assert service == "volume_set" and data["volume_level"] == round(now + 0.1, 2)
    ((_, service, data),) = _writes(house.say("Mach das Radio in der Küche fünf Prozent leiser."))
    assert service == "volume_set" and data["volume_level"] == round(now - 0.05, 2)
    ((_, service, _),) = _writes(house.say("Mach das Radio in der Küche lauter."))
    assert service == "volume_up"


def test_a3_amount_moves_a_cover_relative_to_now(monkeypatch):
    house = HouseConversation(monkeypatch)
    cover = next(e for e in house.entities if e.entity_id == "cover.wohnzimmer_rollladen_links")
    now = int(cover.attributes["current_position"])
    turn = house.say("Fahr den linken Rollladen im Wohnzimmer um 20 Prozent runter.")
    ((_, _, data),) = _writes(turn)
    assert data["position"] == max(0, now - 20)
    # Asked back for the cover, the step still applies to the chosen one.
    house = HouseConversation(monkeypatch)
    assert _writes(house.say("Fahr den Rollladen im Wohnzimmer um 20 Prozent runter.")) == []
    ((_, _, data),) = _writes(house.say("Den linken."))
    assert data["position"] == max(0, now - 20)


def test_a3_an_absolute_value_after_auf_is_no_step(monkeypatch):
    house = HouseConversation(monkeypatch)
    turn = house.say("Fahr den linken Rollladen im Wohnzimmer auf 20 Prozent runter.")
    ((_, _, data),) = _writes(turn)
    assert data["position"] == 20


@pytest.mark.parametrize("sentence", [
    "Dreh die Heizung im Bad um drei Grad hoch.",
    "Fahr den linken Rollladen im Wohnzimmer um 20 Prozent runter.",
])
def test_a3_um_with_a_unit_is_no_clock_time(monkeypatch, sentence):
    house = HouseConversation(monkeypatch)
    turn = house.say(sentence)
    assert "Uhr" not in turn.speech and "Automation" not in turn.speech
