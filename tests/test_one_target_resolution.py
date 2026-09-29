"""Phase 5 (7.4.0): one target resolution for name phrases."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _testhaus import HouseConversation, house_entities
from conftest import ALL_ENTITIES
from homeintent.entities import EntitySnapshot, ResolutionStatus
from homeintent.nlu.target_resolution import numbered_question, resolve_phrase

HOUSE = house_entities()
PACKAGE = Path(__file__).parent.parent / "custom_components" / "homeintent"


def ids(result) -> set[str]:
    return {entity.entity_id for entity in ([result.entity] if result.entity else result.candidates)}


# ------------------------------------------------------------ architecture
def test_no_module_resolves_names_outside_the_one_resolution():
    """7.7 B5: the historic resolver is gone. Only entities.py and
    target_resolution.py rank name candidates; every caller uses ``resolve_phrase``."""
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "entities.py" or path.name == "target_resolution.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {
                "rank_name_candidates", "assemble_name_resolution", "resolve_entity_scored",
            }:
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert offenders == []


# ------------------------------------------------------------ class boundary
@pytest.mark.parametrize("phrase", ["Rollladen Büro", "Rolllade Büro", "Rollladen Küche"])
def test_correction_never_crosses_the_named_device_kind(phrase):
    new = resolve_phrase(phrase, HOUSE)
    assert all(entity.domain == "cover" for entity in (
        [new.entity] if new.entity else new.candidates
    )), (phrase, ids(new))


def test_named_kind_narrows_an_ambiguous_place_name():
    entities = [
        EntitySnapshot(f"{domain}.wohnzimmer", "Wohnzimmer", domain, "off", area_id="wz", area_name="Wohnzimmer")
        for domain in ("climate", "light", "media_player")
    ]
    result = resolve_phrase("Heizung Wohnzimmer", entities)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.entity.entity_id == "climate.wohnzimmer"


def test_a_device_named_after_the_kind_stays_a_candidate():
    """A switch called "Licht Sportraum" is a light for the class boundary."""
    result = resolve_phrase("licht", ALL_ENTITIES)
    assert {"switch.licht_sportraum", "switch.haustur_light"} <= ids(result)


def test_command_words_are_not_a_device_kind():
    """"eine Automation für die Haustür": Automation is what is created."""
    result = resolve_phrase("ich eine Automation für die Haustür anlege", HOUSE)
    assert ids(result) == {"binary_sensor.haustuer"}


def test_exact_registry_names_stay_authoritative():
    odd = [EntitySnapshot("switch.rollladen_licht", "Rollladen", "switch", "off")]
    assert resolve_phrase("Rollladen", odd).entity.entity_id == "switch.rollladen_licht"


# ------------------------------------------------------------ ambiguity
def test_ambiguity_stays_ambiguity_and_a_spoken_place_narrows_it():
    entities = [
        EntitySnapshot("light.decke_a", "Deckenlicht", "light", "off", area_id="bad", area_name="Bad"),
        EntitySnapshot("light.decke_b", "Deckenlicht", "light", "off", area_id="flur", area_name="Flur"),
    ]
    assert resolve_phrase("Deckenlicht", entities).status is ResolutionStatus.AMBIGUOUS
    narrowed = resolve_phrase("Deckenlicht im Bad", entities)
    assert ids(narrowed) == {"light.decke_a"}


def test_single_fuzzy_match_still_needs_confirmation():
    result = resolve_phrase("Kaffemaschine", HOUSE)
    assert result.status is ResolutionStatus.CONFIRMATION_REQUIRED


def test_only_the_entities_passed_in_are_candidates():
    exposed = [entity for entity in HOUSE if entity.entity_id != "light.kuechenlicht"]
    assert "light.kuechenlicht" not in ids(resolve_phrase("Küchenlicht", exposed))


def test_numbered_question():
    entities = [
        EntitySnapshot("light.a", "Deckenlicht Bad", "light", "off"),
        EntitySnapshot("light.b", "Deckenlicht Flur", "light", "off"),
        EntitySnapshot("light.c", "Deckenlicht Küche", "light", "off"),
    ]
    assert numbered_question(entities) == (
        "Welches Gerät meinst du: 1. Deckenlicht Bad, 2. Deckenlicht Flur oder 3. Deckenlicht Küche?"
    )
    assert numbered_question(entities[:1], noun="Routine").startswith("Welche Routine")


# ------------------------------------------------------------ end to end
def test_shutter_phrase_never_switches_a_light(monkeypatch):
    turn = HouseConversation(monkeypatch).say("Fahre den Rollladen Büro hoch.")
    assert not any(target.startswith("light.") for target in turn.targets), turn.speech
