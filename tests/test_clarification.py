"""Phase 25 (v2 plan, "Clarification"): SingleTargetParser's AMBIGUOUS outcome
becomes a real "Welches Licht meinst du?" round-trip instead of the plain
"not understood" every earlier phase gave a tied name. Covers the full path:
parser -> ClarificationRequest, NluEngine.match()/respond()/debug() surfacing
it, and NluEngine.resolve_clarification() completing it from the user's reply.
"""

from __future__ import annotations

from homeintent.entities import EntitySnapshot
from homeintent.nlu.response import NluError

LIGHT_BUERO_1 = EntitySnapshot(
    "light.buerolicht_1", "Bürolicht", "light", "off",
    area_id="buero_1", area_name="Büro 1",
    aliases=("Schreibtischlampe",),
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
LIGHT_BUERO_2 = EntitySnapshot(
    "light.buerolicht_2", "Bürolicht", "light", "off",
    area_id="buero_2", area_name="Büro 2",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
TIED_LIGHTS = [LIGHT_BUERO_1, LIGHT_BUERO_2]


def test_match_surfaces_clarification_instead_of_none(engine):
    result = engine.match("Mach das Bürolicht an", TIED_LIGHTS)
    assert result is not None
    assert result.plan is None
    assert result.clarification is not None
    assert result.response_text == (
        "Ich habe mehrere passende Lichter gefunden: "
        "1. Bürolicht im Bereich Büro 1; 2. Bürolicht im Bereich Büro 2. "
        "Welches meinst du?"
    )


def test_resolve_clarification_by_name_within_candidates(engine):
    # "Bürolicht" alone stays ambiguous even within just the two candidates
    # (both share that exact friendly_name) - a configured alias unique to
    # one candidate is what actually disambiguates by name, distinct from
    # the area-based path below.
    match = engine.match("Mach das Bürolicht an", TIED_LIGHTS)
    resolved = engine.resolve_clarification("Schreibtischlampe", match.clarification, TIED_LIGHTS)
    assert resolved is not None
    assert resolved.plan is not None
    assert resolved.plan.entity_id == "light.buerolicht_1"


def test_resolve_clarification_by_area_within_candidates(engine):
    match = engine.match("Mach das Bürolicht an", TIED_LIGHTS)
    resolved = engine.resolve_clarification("Büro 2", match.clarification, TIED_LIGHTS)
    assert resolved is not None
    assert resolved.plan is not None
    assert resolved.plan.entity_id == "light.buerolicht_2"
    assert resolved.plan.domain == "homeassistant"
    assert resolved.plan.service == "turn_on"
    assert resolved.frame is not None
    assert resolved.frame.intent == "HassTurnOn"
    assert resolved.command is not None
    assert resolved.command.intent == "HassTurnOn"


def test_resolve_clarification_returns_none_for_unresolvable_reply(engine):
    match = engine.match("Mach das Bürolicht an", TIED_LIGHTS)
    assert engine.resolve_clarification("Keller", match.clarification, TIED_LIGHTS) is None


def test_resolve_clarification_returns_none_when_still_ambiguous(engine):
    # Neither light's own area nor name distinguishes them from "Licht" alone.
    match = engine.match("Mach das Bürolicht an", TIED_LIGHTS)
    assert engine.resolve_clarification("Licht", match.clarification, TIED_LIGHTS) is None


def test_respond_returns_ambiguous_entity_error(engine):
    response = engine.respond("Mach das Bürolicht an", TIED_LIGHTS)
    assert response.success is False
    assert response.speech is None
    assert response.command is None
    assert response.error is NluError.AMBIGUOUS_ENTITY


def test_debug_trace_for_ambiguous_match(engine):
    trace = engine.debug("Mach das Bürolicht an", TIED_LIGHTS)
    assert trace.parser == "SemanticInterpreter"
    assert trace.intent == "HassTurnOn"
    assert trace.target == "Bürolicht"
    assert set(trace.candidates) == {"light.buerolicht_1", "light.buerolicht_2"}
    assert trace.validation == "AMBIGUOUS_ENTITY"
    assert trace.service is None
