"""Spoken management of routine bindings (7.3.3).

"Vergiss die Schlafroutine", "Schlafen ist ab jetzt das Skript Gute Nacht",
"Welche Routine nutzt du für den Filmabend?". Meaning only: the store is
changed by ``conversation.py`` after an explicit "Ja", never here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from .entities import EntitySnapshot, normalize_for_compare
from .nlu.need_compiler import routine_named_explicitly
from .nlu.need_semantics import (
    ROUTINE_CONCEPTS,
    RoutineConcept,
    routine_concept_of_compound,
)


class RoutineBindingOperation(StrEnum):
    BIND = "bind"
    FORGET = "forget"
    SHOW = "show"


@dataclass(frozen=True)
class RoutineBindingRequest:
    operation: RoutineBindingOperation
    concept: RoutineConcept
    target: EntitySnapshot | None = None
    candidates: tuple[EntitySnapshot, ...] = ()
    personal: bool = False


def _words(text: str) -> str:
    folded = normalize_for_compare(text)
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def concept_of_phrase(phrase: str) -> RoutineConcept | None:
    """"Schlafroutine", "schlafen", "den Filmabend", "Routine für die Nacht"."""
    words = _words(phrase).split()
    for word in words:
        concept = routine_concept_of_compound(word)
        if concept is not None:
            return concept
    for word in words:
        for concept in ROUTINE_CONCEPTS:
            if word in concept.cues or any(word.startswith(stem) for stem in concept.names):
                return concept
    return None


_FORGET = re.compile(
    r"^(?:bitte\s+)?(?:vergiss|loesche|entferne)\s+(?:die|meine|unsere|deine)?\s*(?P<concept>.+?)\s*$"
)
_BIND = re.compile(
    r"^(?:(?:die|meine|unsere)\s+)?(?P<concept>.+?)\s+(?:ist|sind)\s+"
    r"(?:ab\s+jetzt|ab\s+sofort|kuenftig|von\s+jetzt\s+an)\s+"
    r"(?:(?:das\s+skript|die\s+szene|die\s+routine|das)\s+)?(?P<target>.+?)\s*$"
)
_SHOW = re.compile(
    r"^(?:welche|was\s+fuer\s+eine)\s+routine\s+(?:nimmst|nutzt|verwendest|startest)\s+du\s+"
    r"(?:fuer|bei|zum|zur|beim)\s+(?P<concept>.+?)\s*$"
)


def _routine_targets(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if entity.domain in {"script", "scene"}]


def interpret_routine_binding(
    text: str, entities: Sequence[EntitySnapshot]
) -> RoutineBindingRequest | None:
    normalized = _words(text)
    personal = bool(re.search(r"\b(?:fuer\s+mich|meine|mein)\b", normalized))
    if match := _SHOW.match(normalized):
        concept = concept_of_phrase(match.group("concept"))
        return RoutineBindingRequest(RoutineBindingOperation.SHOW, concept) if concept else None
    if match := _FORGET.match(normalized):
        phrase = match.group("concept")
        if "routine" not in phrase:
            return None  # "Vergiss die Heizung" is not about routines
        concept = concept_of_phrase(phrase)
        if concept is None:
            return None
        return RoutineBindingRequest(RoutineBindingOperation.FORGET, concept, personal=personal)
    if match := _BIND.match(normalized):
        concept = concept_of_phrase(match.group("concept"))
        if concept is None:
            return None
        target_text = match.group("target")
        targets = [
            entity for entity in _routine_targets(entities)
            if _words(entity.friendly_name) == target_text
            or any(_words(alias) == target_text for alias in entity.aliases)
        ]
        if len(targets) == 1:
            return RoutineBindingRequest(
                RoutineBindingOperation.BIND, concept, targets[0], personal=personal
            )
        partial = [
            entity for entity in _routine_targets(entities)
            if routine_named_explicitly(target_text, entity)
        ]
        return RoutineBindingRequest(
            RoutineBindingOperation.BIND, concept, None, tuple(targets or partial), personal
        )
    return None


_ORDINALS = {
    "erste": 0, "ersten": 0, "eins": 0, "zweite": 1, "zweiten": 1, "zwei": 1,
    "dritte": 2, "dritten": 2, "drei": 2, "vierte": 3, "vierten": 3, "vier": 3,
    "fuenfte": 4, "fuenften": 4,
}


def choose_candidate(
    text: str, candidates: Sequence[EntitySnapshot]
) -> EntitySnapshot | None:
    """The answer to "Welche Routine meinst du: A, B oder C?"."""
    named = [entity for entity in candidates if routine_named_explicitly(text, entity)]
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        # "Gute Nacht" vs. "Nacht": the longest spoken name wins only if unique.
        longest = max(len(entity.friendly_name) for entity in named)
        best = [entity for entity in named if len(entity.friendly_name) == longest]
        return best[0] if len(best) == 1 else None
    words = _words(text).split()
    if "letzte" in words or "letzten" in words:
        return candidates[-1] if candidates else None
    for word in words:
        index = _ORDINALS.get(word)
        if index is not None and index < len(candidates):
            return candidates[index]
    return None
