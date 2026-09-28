"""HassTurnOn/HassTurnOff/HassToggle over light, switch, and fan entities."""

from __future__ import annotations

import pytest

from conftest import (
    FANS,
    LIGHTS,
    MAINTAIN_PHRASES,
    SWITCHES,
    TURN_OFF_PHRASES,
    TURN_ON_PHRASES,
)


def _cases(phrases: list[str], names: dict[str, str], service: str):
    for name, eid in names.items():
        for tpl in phrases:
            yield pytest.param(tpl.format(name=name), eid, service, id=f"{eid}:{tpl}")


ON_CASES = list(
    [*_cases(TURN_ON_PHRASES, LIGHTS, "turn_on"),
     *_cases(TURN_ON_PHRASES, SWITCHES, "turn_on"),
     *_cases(TURN_ON_PHRASES, FANS, "turn_on")]
)
OFF_CASES = list(
    [*_cases(TURN_OFF_PHRASES, LIGHTS, "turn_off"),
     *_cases(TURN_OFF_PHRASES, SWITCHES, "turn_off"),
     *_cases(TURN_OFF_PHRASES, FANS, "turn_off")]
)


@pytest.mark.parametrize("text,entity_id,service", ON_CASES)
def test_turn_on(engine, entities, text, entity_id, service):
    result = engine.match(text, entities)
    assert result is not None, f"no match for {text!r}"
    assert result.plan.domain == "homeassistant"
    assert result.plan.service == service
    assert result.plan.entity_id == entity_id


@pytest.mark.parametrize("text,entity_id,service", OFF_CASES)
def test_turn_off(engine, entities, text, entity_id, service):
    result = engine.match(text, entities)
    assert result is not None, f"no match for {text!r}"
    assert result.plan.domain == "homeassistant"
    assert result.plan.service == service
    assert result.plan.entity_id == entity_id


def test_toggle(engine, entities):
    result = engine.match("Schalte das Flurlicht um", entities)
    assert result is not None
    assert result.plan.service == "toggle"
    assert result.plan.entity_id == "light.flur_licht"


def test_domain_guard_cover_intent_rejects_light(engine, entities):
    # HassOpenCover only allows the cover domain - a light must not match.
    assert engine.match("Öffne das Treppenlicht", entities) is None


def test_unknown_entity_no_match(engine, entities):
    assert engine.match("Mach das Kellerlicht an", entities) is None


MAINTAIN_CASES = [
    phrase.format(name=name)
    for phrase in MAINTAIN_PHRASES
    for name in (*LIGHTS, *SWITCHES, *FANS)
]


@pytest.mark.parametrize("text", MAINTAIN_CASES)
def test_maintain_phrase_never_plans_a_service_call(engine, entities, text):
    """S1: "lass X an" keeps a state and must never switch anything."""
    result = engine.match(text, entities)
    assert result is None or getattr(result, "plan", None) is None
