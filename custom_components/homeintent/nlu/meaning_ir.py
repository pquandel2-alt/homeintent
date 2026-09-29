"""Grounded meaning of an utterance on the shared IR (7.5.0).

``ground_meaning`` fills the meaning fields of ``MeaningClause`` from the
analyses HomeIntent already has - no new parser and no new meaning type:

* ``operation`` / ``value``: the semantic lexicon and the value readers the
  genus compiler uses (``ontology_compiler._clause_meanings``);
* ``targets``: the one target description of ``target_resolution``
  (genus, place, quantity, feature, explicit registry names, references);
* ``time``: temporal expressions plus the recurrence markers of 7.3.3;
* ``conditions`` / ``exceptions``: the structural clause analysis;
* ``origin``: explicit command, implicit need or none (questions);
* ``residue``: every word no analysis explained;
* ``evidence``: which analysis contributed what.

The result is read-only information for arbitration and measurement. It
never calls a service and never decides about execution.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ..entities import EntitySnapshot
from ..plan_origin import PlanOrigin
from .german_structure import ClauseKind
from .language_frontend import LanguageDocument, analyse_language, tokenize_language
from .recurrence import Recurrence, recurrence_of
from .semantic_catalog import TIME_BOUND_WORDS
from .semantic_exclusion import has_exclusion_clause, split_exclusion
from .semantic_utterance import (
    MeaningClause,
    PragmaticDisposition,
    SemanticUtterance,
    SpeechAct,
    TargetMeaning,
    TimeKind,
)

_REFERENCE_WORDS = frozenset({"es", "sie", "ihn", "dort", "da", "dorthin", "andere", "anderen", "dasselbe", "das"})


def _target(description: object) -> TargetMeaning:
    place = getattr(description, "place", None)
    quantity = getattr(description, "quantity", None)
    return TargetMeaning(
        genera=tuple(getattr(description, "genera", ())),
        place=getattr(place, "name", None) if place is not None else None,
        quantity=getattr(quantity, "name", "ONE"),
        features=frozenset(getattr(description, "features", frozenset())),
        explicit=tuple(entity.entity_id for entity in getattr(description, "explicit", ())),
    )


def _time(document: LanguageDocument) -> TimeKind:
    from .temporal_semantics import TemporalKind

    tokens = document.tokens
    now_like = {TemporalKind.NOW, TemporalKind.DURATION}
    # "jetzt" is now; "für eine Stunde" starts now and ends later: the words
    # of such spans are no time bound.
    covered = {
        index
        for item in document.temporal if item.kind in now_like
        for index in range(item.token_start, item.token_end)
    }
    def clock_fraction(index: int) -> bool:
        # "halb"/"viertel" are clock times only next to "um"/"nach"/"vor"
        # ("um halb acht", "viertel nach"), not in "auf drei Viertel".
        word = tokens[index].canonical
        if word not in {"halb", "viertel"}:
            return True
        before = tokens[index - 1].canonical if index > 0 else ""
        after = tokens[index + 1].canonical if index + 1 < len(tokens) else ""
        return before == "um" or after in {"nach", "vor"}

    later = any(item.kind not in now_like for item in document.temporal) or any(
        token.canonical in TIME_BOUND_WORDS and index not in covered and clock_fraction(index)
        for index, token in enumerate(tokens)
    ) or any(
        tokens[index].canonical == ":" and tokens[index - 1].is_number and tokens[index + 1].is_number
        for index in range(1, len(tokens) - 1)
    )
    recurrence = recurrence_of(document.source_text)
    if recurrence is Recurrence.RECURRING:
        return TimeKind.RECURRING
    if not later and document.utterance.speech_act is not SpeechAct.AUTOMATION:
        return TimeKind.NOW
    if recurrence is Recurrence.ONCE:
        return TimeKind.ONCE
    return TimeKind.UNSPECIFIED


def is_deferred(document: LanguageDocument) -> bool:
    """Time-bound or conditional meaning: nothing of it runs now (7.3.3 Q5).

    The one rule the arbiter applies to every executable reading (7.7).
    """
    if document.utterance.speech_act is SpeechAct.AUTOMATION:
        return True
    if any(
        clause.kind in {ClauseKind.CONDITION, ClauseKind.TEMPORAL}
        for clause in document.structure.clauses
    ):
        return True
    return _time(document) is not TimeKind.NOW


def _origin(utterance: SemanticUtterance, words: Sequence[str]) -> str | None:
    from .need_semantics import interpret_need

    if interpret_need(words, question=utterance.speech_act is SpeechAct.QUERY and utterance.normalized_text.rstrip().endswith("?")) is not None:
        return PlanOrigin.IMPLICIT_NEED.value
    if utterance.speech_act is SpeechAct.QUERY:
        return None
    if utterance.pragmatic_disposition is PragmaticDisposition.ASK_BEFORE_ACTION:
        return PlanOrigin.IMPLICIT_NEED.value
    if utterance.speech_act in {SpeechAct.COMMAND, SpeechAct.AUTOMATION}:
        return PlanOrigin.EXPLICIT_COMMAND.value
    return None


def ground_meaning(
    document: LanguageDocument | str, entities: Sequence[EntitySnapshot]
) -> SemanticUtterance:
    """The utterance with every clause's meaning fields filled in."""
    from .clause_reading import read_clauses
    from .semantic_lexicon import SemanticKind, analyse_semantics
    from .target_resolution import describe_with_residue

    if isinstance(document, str):
        document = analyse_language(document, entities)
    utterance = document.utterance
    structure = document.structure
    surface = getattr(structure, "source_text", "") or document.source_text
    conditions = tuple(
        surface[clause.char_start:clause.char_end].strip() for clause in structure.clauses
        if clause.kind in {ClauseKind.CONDITION, ClauseKind.TEMPORAL}
    )
    exceptions: tuple[str, ...] = ()
    if has_exclusion_clause(utterance.normalized_text):
        exceptions = split_exclusion(utterance.normalized_text)[1]
    time = _time(document)
    all_tokens = tokenize_language(utterance.normalized_text)
    words = [token.canonical for token in all_tokens if token.is_word]
    origin = _origin(utterance, words)
    # The expletive "es" of a need ("Hier ist es zu dunkel") refers to nothing.
    references = () if origin == PlanOrigin.IMPLICIT_NEED.value else tuple(
        word for word in words if word in _REFERENCE_WORDS
    )
    shared = dict(time=time, conditions=conditions, exceptions=exceptions, origin=origin)

    grounded: list[MeaningClause] = []
    for meaning in read_clauses(document, entities):
        value: dict[str, object] = {}
        if meaning.percent is not None:
            value["percent"] = meaning.percent
        if meaning.temperature is not None:
            value["temperature"] = meaning.temperature
        if meaning.degree is not None:
            value["degree"] = meaning.degree
        evidence = ["lexicon:operation"] if meaning.actions else []
        evidence += ["registry:name"] if any(item.explicit for item in meaning.descriptions) else []
        evidence += ["ontology:genus"] if any(item.genera for item in meaning.descriptions) else []
        evidence += ["place"] if any(item.place is not None for item in meaning.descriptions) else []
        targets = tuple(_target(item) for item in meaning.descriptions)
        if references and not targets:
            targets = (TargetMeaning(reference=references[0]),)
        grounded.append(MeaningClause(
            meaning.text, utterance.clauses[0].role if utterance.clauses else _main_role(),
            operation=meaning.actions,
            targets=targets,
            value=value,
            residue=meaning.residue,
            evidence=tuple(evidence),
            **shared,  # type: ignore[arg-type]
        ))
    if not grounded:
        # Structures the coordination reader leaves to dedicated compilers
        # (conditions, exceptions, repairs): one clause over the whole text.
        analysis = analyse_semantics(utterance.normalized_text)
        actions = frozenset(
            str(span.value) for span in analysis.spans if span.kind is SemanticKind.ACTION
        )
        descriptions, residue = describe_with_residue(
            tokenize_language(utterance.normalized_text), entities
        )
        targets = tuple(_target(item) for item in descriptions)
        if references and not targets:
            targets = (TargetMeaning(reference=references[0]),)
        grounded = [
            replace(
                clause,
                operation=actions,
                targets=targets,
                residue=tuple(residue),
                evidence=("lexicon:operation",) if actions else (),
                **shared,  # type: ignore[arg-type]
            )
            for clause in (utterance.clauses or (MeaningClause(utterance.normalized_text, _main_role()),))
        ]
    return replace(utterance, clauses=tuple(grounded))


def _main_role():
    from .semantic_utterance import ClauseRole

    return ClauseRole.MAIN


__all__ = ("ground_meaning", "is_deferred")
