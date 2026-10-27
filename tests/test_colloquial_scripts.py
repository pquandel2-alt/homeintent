"""Compositional colloquial script language, bound to exact script names."""

from __future__ import annotations

import pytest

from homeintent.engine import NluEngine
from homeintent.entities import EntitySnapshot
from homeintent.nlu.script_semantics import validate_script_verbs


SCRIPTS = [
    EntitySnapshot("script.sprudel", "1 Glas Sprudel", "script", "off"),
    EntitySnapshot("script.portion", "Eine Portion", "script", "off"),
    EntitySnapshot("script.kaffee", "2 Kaffee", "script", "off"),
    EntitySnapshot("script.radio_kueche", "Radio Küche", "script", "off"),
]


@pytest.mark.parametrize(
    ("sentence", "entity_id"),
    (
        ("Zapf mir ein Glas Sprudel", "script.sprudel"),
        ("1 Glas Sprudel zapfen", "script.sprudel"),
        ("Mach mir eine Portion", "script.portion"),
        ("Gib mir Eine Portion", "script.portion"),
        ("Ich möchte zwei Kaffee", "script.kaffee"),
        ("Ich hätte gern 2 Kaffee", "script.kaffee"),
        ("Bitte starte doch Radio Küche", "script.radio_kueche"),
        ("Radio Küche bitte starten", "script.radio_kueche"),
    ),
)
def test_colloquial_wrappers_run_only_an_exact_exposed_script(
    engine, sentence, entity_id
):
    result = engine.match(sentence, SCRIPTS)

    assert result is not None, sentence
    assert result.plan is not None
    assert result.frame is not None
    assert result.frame.intent == "HassRunScript"
    assert result.frame.action.name == "START"
    assert result.plan.domain == "script"
    assert result.plan.service == "turn_on"
    assert result.plan.entity_id == entity_id


def test_exact_script_name_beats_loose_media_interpretation(engine):
    entities = [
        *SCRIPTS,
        EntitySnapshot(
            "media_player.kueche",
            "Radio Küche Lautsprecher",
            "media_player",
            "idle",
            area_id="kueche",
            area_name="Küche",
            capabilities=frozenset({"PLAY"}),
        ),
    ]

    result = engine.match("Starte Radio Küche", entities)

    assert result is not None
    assert result.plan is not None
    assert result.plan.domain == "script"
    assert result.plan.entity_id == "script.radio_kueche"


@pytest.mark.parametrize(
    "sentence",
    (
        "Zapf mir ein Glas Sprudel gefährlich",
        "Zapf mir nicht ein Glas Sprudel",
        "Gib mir die Stehlampe",
        "Ich möchte irgendein unbekanntes Skript",
    ),
)
def test_colloquial_script_language_never_drops_residue_or_targets_devices(
    engine, sentence
):
    entities = [
        *SCRIPTS,
        EntitySnapshot("light.stehlampe", "Stehlampe", "light", "off"),
    ]

    result = engine.match(sentence, entities)

    assert result is None or result.plan is None


def test_custom_script_verb_is_literal_bounded_and_script_only():
    phrases = validate_script_verbs("braue, kredenze")
    engine = NluEngine(script_verbs=phrases)
    scripts = [
        EntitySnapshot("script.kaffee", "Kaffee", "script", "off"),
        EntitySnapshot("switch.kaffee", "Kaffee", "switch", "off"),
    ]

    result = engine.match("Braue Kaffee", scripts)

    assert result is not None
    assert result.plan is not None
    assert result.plan.domain == "script"
    assert engine.match(
        "Braue Stehlampe",
        [EntitySnapshot("light.stehlampe", "Stehlampe", "light", "off")],
    ) is None


@pytest.mark.parametrize(
    "value",
    (
        "nicht",
        "entriegeln",
        "starten",
        "brau.*",
        "eins zwei drei vier",
        ",".join(f"verb{index}" for index in range(13)),
    ),
)
def test_custom_script_verbs_reject_conflicts_regex_and_unbounded_input(value):
    with pytest.raises(ValueError):
        validate_script_verbs(value)
