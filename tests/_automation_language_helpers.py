"""Shared helpers for the 7.2.0 automation language suites."""

from __future__ import annotations

from _automation_eval import model_meaning
from _automation_world import WORLD
from homeintent.engine import AutomationClarificationResult, AutomationMatchResult, NluEngine

ENGINE = NluEngine()
ENTITIES = list(WORLD)


def meaning(sentence: str) -> str:
    """"TRIGGERS ; if: CONDS => ACTIONS" of the parsed model (corpus DSL)."""
    result = ENGINE.match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationMatchResult), f"{sentence!r} -> {result!r}"
    assert result.validation_error is None, result.validation_error
    parsed = model_meaning(result.model, ENTITIES)
    text = " | ".join(sorted(parsed.triggers))
    if parsed.conditions:
        text += " ; if: " + " & ".join(sorted(parsed.conditions))
    return text + " => " + " + ".join(parsed.actions)


def question(sentence: str) -> str:
    result = ENGINE.match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationClarificationResult), f"{sentence!r} -> {result!r}"
    return result.response_text
