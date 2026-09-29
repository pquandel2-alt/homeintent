"""Cause questions about one device: "Warum ist der Saugroboter angegangen?"

Only the meaning is determined here (a cause question and its grounded
device); the answer comes from ``execution_trace.explain_change`` over Home
Assistant's own context chain. This module never touches Home Assistant and
never executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot
from .language_frontend import LanguageDocument
from .target_resolution import ResolutionOutcome, resolve_text_target

# "warum/wieso/weshalb …", "wer hat … an/eingeschaltet/gestartet …"
_CAUSE_WORDS = frozenset({"warum", "wieso", "weshalb", "weswegen"})
_AGENT_WORDS = frozenset({"wer", "was"})
# Words that make "wer/was hat X …" a question about a change, not a state.
_CHANGE_WORDS = frozenset({
    "an", "aus", "angegangen", "ausgegangen", "angemacht", "ausgemacht",
    "eingeschaltet", "ausgeschaltet", "angeschaltet", "gestartet", "gestoppt",
    "geoeffnet", "geschlossen", "aufgemacht", "zugemacht", "ausgeloest",
    "losgegangen", "angesprungen", "hochgefahren", "runtergefahren",
    "heruntergefahren", "verriegelt", "entriegelt", "gedrueckt", "geschaltet",
    "veraendert", "verstellt", "los",
})


_NOT_A_DEVICE_CAUSE = frozenset({
    "du", "dich", "dir", "automation", "automationen", "regel", "gestern",
    "vorgestern", "letzte", "letzten", "letztes", "vorhin", "damals",
})


@dataclass(frozen=True)
class CauseQuestion:
    entity: EntitySnapshot | None
    clarification: str | None = None


def interpret_cause_question(
    document: LanguageDocument, entities: Sequence[EntitySnapshot]
) -> CauseQuestion | None:
    words = [token.canonical for token in document.tokens if token.is_word]
    if not words:
        return None
    first = words[0]
    # Questions to HomeIntent itself ("Warum hast du mich …"), about
    # automations or about the past belong to their own answers.
    if set(words) & _NOT_A_DEVICE_CAUSE:
        return None
    if first in _CAUSE_WORDS:
        pass
    elif first in _AGENT_WORDS and len(words) > 1 and words[1] in {"hat", "hatte"} and (
        set(words) & _CHANGE_WORDS
    ):
        pass
    else:
        return None
    resolutions = [
        item for item in resolve_text_target(document.tokens, entities)
        if item.outcome is not ResolutionOutcome.NONE
        # A device must be named (by name or kind); "Warum ist es im Büro so
        # kalt?" is a situation question, not a device cause question.
        and (item.description.explicit or item.description.genera)
    ]
    if not resolutions:
        return None
    first_resolution = resolutions[0]
    if first_resolution.outcome is ResolutionOutcome.RESOLVED and len(first_resolution.entities) == 1:
        return CauseQuestion(first_resolution.entities[0])
    candidates = first_resolution.entities
    if 1 < len(candidates) <= 8:
        names = [entity.friendly_name for entity in candidates]
        listed = ", ".join(names[:-1]) + " oder " + names[-1]
        return CauseQuestion(None, f"Welches Gerät meinst du: {listed}?")
    return None
