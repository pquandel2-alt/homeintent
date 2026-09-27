"""F5: every name in an exclusion list is excluded, or nothing is executed."""

from __future__ import annotations

import pytest

from homeintent.engine import MatchResult
from homeintent.entities import EntitySnapshot
from homeintent.nlu.capabilities import derive_capabilities
from homeintent.nlu.understanding import UnderstandingKind


def _light(entity_id: str, name: str, area: str) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id, name, "light", "on", area_id=area.casefold(), area_name=area,
        capabilities=frozenset(c.name for c in derive_capabilities("light", None, {})),
    )


ENTITIES = [
    _light("light.stehlampe", "Stehlampe", "Wohnzimmer"),
    _light("light.nachtlicht", "Nachtlicht", "Kinderzimmer"),
    _light("light.kuechenlicht", "Küchenlicht", "Küche"),
    _light("light.flurlicht", "Flurlicht", "Flur"),
]


@pytest.mark.parametrize(
    "text",
    [
        "Mach alle Lichter aus außer der Stehlampe und dem Nachtlicht.",
        "Mach alle Lichter aus außer der Stehlampe, dem Nachtlicht.",
        "Schalte alle Lichter außer der Stehlampe und dem Nachtlicht aus.",
        "Schalte alle Lichter aus mit Ausnahme von Stehlampe sowie Nachtlicht.",
    ],
)
def test_every_listed_exclusion_is_excluded(engine, text):
    result = engine.match(text, ENTITIES)

    assert isinstance(result, MatchResult) and result.plan is not None
    assert sorted(result.plan.entity_id) == ["light.flurlicht", "light.kuechenlicht"]


@pytest.mark.parametrize(
    "text",
    [
        "Mach alle Lichter aus außer der Stehlampe und dem Gartenzwerg.",
        "Mach alle Lichter aus außer dem Gartenzwerg.",
    ],
)
def test_unresolvable_exclusion_executes_nothing_and_says_why(engine, text):
    outcome = engine.understand(text, ENTITIES)

    assert outcome.payload is None
    assert outcome.kind is UnderstandingKind.UNSUPPORTED
    assert "Gartenzwerg" in (outcome.speech or "")
    assert "nichts geschaltet" in (outcome.speech or "")
