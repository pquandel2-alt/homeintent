"""Typed migration boundary for a narrow automation capability matrix.

The first authoritative matrix covers incomplete future/solar automation
utterances.  It consumes the turn's existing :class:`LanguageDocument` and
can only request missing slots; it cannot create a model or service plan.
Successful automation compilation remains behind compatibility adapters and
is deliberately not claimed as migrated yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable

from ..entities import EntitySnapshot, normalize_for_compare
from .german_structure import ClauseKind
from .language_frontend import LanguageDocument
from .semantic_lexicon import SemanticKind
from .semantic_utterance import Modality, Polarity
from .temporal_semantics import TemporalKind


class AutomationMeaningSlot(Enum):
    DATE = auto()
    CLOCK = auto()
    SUN_EVENT = auto()
    SUN_OFFSET = auto()
    CONDITION = auto()
    DURATION = auto()
    STATE = auto()
    ACTION = auto()
    TARGET = auto()
    EXCEPTION = auto()
    REPETITION = auto()
    VALIDITY = auto()


class AutomationRouteKind(Enum):
    NOT_APPLICABLE = auto()
    COMPATIBILITY = auto()
    INCOMPLETE = auto()
    UNSAFE = auto()


@dataclass(frozen=True)
class AutomationTurnMeaning:
    """Source-backed slots projected once from the shared frontend."""

    slots: frozenset[AutomationMeaningSlot]
    missing_slots: tuple[AutomationMeaningSlot, ...] = ()
    unexplained_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutomationRouteDecision:
    """Router decision for the currently migrated capability matrix."""

    kind: AutomationRouteKind
    meaning: AutomationTurnMeaning
    question: str | None = None
    migration_status: str = "incomplete_future_solar_v1"


_NUMBER_WORDS = frozenset({
    "ein", "eine", "einer", "zwei", "drei", "vier", "fuenf", "sechs",
    "sieben", "acht", "neun", "zehn", "fuenfzehn", "zwanzig", "dreissig",
})
_DURATION_UNITS = frozenset({
    "sekunde", "sekunden", "minute", "minuten", "stunde", "stunden",
    "tag", "tage",
})
_REPETITION_WORDS = frozenset({
    "jede", "jeden", "jedes", "immer", "taeglich", "woechentlich",
})
_TARGET_NOUNS = frozenset({
    "licht", "lichter", "lampe", "lampen", "heizung", "heizungen",
    "thermostat", "thermostate", "waschmaschine",
})


def _has_exact_registry_target(
    document: LanguageDocument, entities: Iterable[EntitySnapshot]
) -> bool:
    padded = f" {normalize_for_compare(document.source_text)} "
    return any(
        key and f" {key} " in padded
        for entity in entities
        for name in (entity.friendly_name, *entity.aliases)
        if (key := normalize_for_compare(name))
    )


def _has_numeric_duration_words(document: LanguageDocument) -> bool:
    words = tuple(token.canonical for token in document.tokens if token.is_word)
    return any(
        index + 1 < len(words)
        and (word.isdigit() or word in _NUMBER_WORDS)
        and words[index + 1] in _DURATION_UNITS
        for index, word in enumerate(words)
    )


def project_automation_meaning(
    document: LanguageDocument, entities: Iterable[EntitySnapshot]
) -> AutomationTurnMeaning:
    """Project compositional slots without re-reading the raw sentence."""
    slots: set[AutomationMeaningSlot] = set()
    temporal = {item.kind for item in document.temporal}
    if TemporalKind.DATE in temporal:
        slots.add(AutomationMeaningSlot.DATE)
    if TemporalKind.ABSOLUTE_TIME in temporal:
        slots.add(AutomationMeaningSlot.CLOCK)
    if TemporalKind.SUN_EVENT in temporal:
        slots.add(AutomationMeaningSlot.SUN_EVENT)
    if TemporalKind.SUN_EVENT in temporal and temporal & {
        TemporalKind.BEFORE, TemporalKind.AFTER
    }:
        slots.add(AutomationMeaningSlot.SUN_OFFSET)
    if TemporalKind.DURATION in temporal or _has_numeric_duration_words(document):
        slots.add(AutomationMeaningSlot.DURATION)
    if TemporalKind.WEEKDAY in temporal or any(
        token.canonical in _REPETITION_WORDS for token in document.tokens
    ):
        slots.add(AutomationMeaningSlot.REPETITION)
    if AutomationMeaningSlot.DATE in slots and AutomationMeaningSlot.REPETITION in slots:
        slots.add(AutomationMeaningSlot.VALIDITY)
    if document.semantics.values(SemanticKind.STATE) or document.semantics.values(
        SemanticKind.PROPERTY
    ):
        slots.add(AutomationMeaningSlot.STATE)
    if document.semantics.values(SemanticKind.ACTION):
        slots.add(AutomationMeaningSlot.ACTION)
    if _has_exact_registry_target(document, entities) or any(
        token.canonical in _TARGET_NOUNS for token in document.tokens
    ):
        slots.add(AutomationMeaningSlot.TARGET)
    if any(
        clause.kind is ClauseKind.CONDITION for clause in document.structure.clauses
    ):
        slots.add(AutomationMeaningSlot.CONDITION)
    if any(
        clause.kind is ClauseKind.EXCLUSION for clause in document.structure.clauses
    ):
        slots.add(AutomationMeaningSlot.EXCEPTION)
    return AutomationTurnMeaning(
        frozenset(slots), unexplained_tokens=document.semantics.unexplained_tokens
    )


def route_automation_turn(
    document: LanguageDocument, entities: Iterable[EntitySnapshot]
) -> AutomationRouteDecision:
    """Route only the migrated incomplete-future matrix, fail-closed."""
    meaning = project_automation_meaning(document, entities)
    future = bool(
        meaning.slots
        & {
            AutomationMeaningSlot.DATE,
            AutomationMeaningSlot.CLOCK,
            AutomationMeaningSlot.SUN_EVENT,
        }
    )
    if not future:
        return AutomationRouteDecision(AutomationRouteKind.NOT_APPLICABLE, meaning)
    if (
        document.utterance.polarity is Polarity.NEGATIVE
        or document.utterance.modality
        in {Modality.HYPOTHETICAL, Modality.IRREALIS, Modality.DELIBERATION}
    ):
        return AutomationRouteDecision(AutomationRouteKind.UNSAFE, meaning)

    has_incomplete_payload = (
        AutomationMeaningSlot.ACTION not in meaning.slots
        and bool(
            meaning.slots
            & {AutomationMeaningSlot.STATE, AutomationMeaningSlot.TARGET}
        )
    )
    # A plain explicit question remains a query.  The migrated shape is a
    # fragment with future timing + desired/observed state but no predicate
    # saying what should happen (the requested "Morgen ... noch warm" case).
    explicit_question = document.source_text.rstrip().endswith("?")
    # An explicit condition is a valid additional clause, not evidence that
    # the fragment is complete. Other clause kinds remain on the established
    # compatibility path until their binding is migrated.
    main_clause_count = sum(
        clause.kind is ClauseKind.MAIN for clause in document.structure.clauses
    )
    fragment_clauses = main_clause_count == 1 and all(
        clause.kind is ClauseKind.MAIN
        or (
            clause.kind is ClauseKind.CONDITION
            and clause.connector in {"wenn", "nur wenn", "sofern", "falls"}
        )
        for clause in document.structure.clauses
    )
    if not has_incomplete_payload or explicit_question or not fragment_clauses:
        return AutomationRouteDecision(AutomationRouteKind.COMPATIBILITY, meaning)

    missing: list[AutomationMeaningSlot] = [AutomationMeaningSlot.ACTION]
    # A generic device noun proves the target class, not a concrete room or
    # entity.  Heating-state fragments therefore still need their location;
    # a light fragment may obtain it together with the action next turn.
    if (
        AutomationMeaningSlot.TARGET not in meaning.slots
        or (
            AutomationMeaningSlot.STATE in meaning.slots
            and not _has_exact_registry_target(document, entities)
        )
    ):
        missing.insert(0, AutomationMeaningSlot.TARGET)
    projected = AutomationTurnMeaning(
        meaning.slots,
        tuple(missing),
        meaning.unexplained_tokens,
    )
    question = (
        "Welcher Raum soll warm sein?"
        if AutomationMeaningSlot.STATE in meaning.slots
        and AutomationMeaningSlot.TARGET in missing
        else "Was soll zu diesem Zeitpunkt passieren?"
    )
    return AutomationRouteDecision(
        AutomationRouteKind.INCOMPLETE,
        projected,
        question,
    )
