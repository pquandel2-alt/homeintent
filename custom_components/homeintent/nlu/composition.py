"""HA-free composition derived from the shared structural analysis.

The production understanding path consumes :class:`LanguageDocument` and
never edits entity names out of a sentence.  The older ``SemanticTurn`` /
``project_target`` helpers remain temporarily for the automation compatibility
parser and the read-only legacy shadow only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..entities import EntityIndex, EntitySnapshot
from .entity_resolution import all_mentioned_entities
from .meaning import CoordinationKind, SemanticTurn
from .german_structure import ClauseKind, StructuralRelationKind
from .language_frontend import LanguageDocument
from .semantic_lexicon import SemanticKind
from .semantic_utterance import SpeechAct
from .word_cues import has_word


@dataclass(frozen=True)
class CompositionalPlan:
    """Structural meaning proven before any service call is generated."""

    source_text: str
    targets: tuple[EntitySnapshot, ...]
    action: str | None
    shared_predicate: bool
    atomic: bool = True


def build_document_compositional_plan(
    document: LanguageDocument,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
    resolved_mentions: tuple[EntitySnapshot, ...] | None = None,
) -> CompositionalPlan | None:
    """Return a shared-predicate plan proven by clauses and lexical spans.

    Exactly one active action may govern two or more explicitly mentioned
    targets.  ``OR``, exclusions and multiple predicates are deliberately not
    collapsed.  Entity identity still comes exclusively from the canonical
    mention resolver.
    """
    if (
        document.utterance.speech_act is not SpeechAct.COMMAND
        or not document.utterance.safe_to_execute_directly
        or any(
            relation.kind in {
                StructuralRelationKind.OR,
                StructuralRelationKind.EXCEPT,
            }
            for relation in document.structure.relations
        )
        or any(token.canonical == "ausnahme" for token in document.tokens)
    ):
        return None
    # Reject the overwhelmingly common non-compositional case before any
    # registry work. The interpreter can also provide its already grounded
    # mentions so entity resolution is never repeated per target.
    if not any(
        token.canonical in {"und", "sowie", "ausserdem"}
        for token in document.tokens
    ):
        return None
    active_actions = tuple(
        span
        for span in document.semantics.matching(SemanticKind.ACTION)
        if (
            (clause := document.structure.clause_for_char(span.start)) is None
            or clause.kind in {ClauseKind.MAIN, ClauseKind.COORDINATE}
        )
    )
    if len(active_actions) != 1 or not isinstance(active_actions[0].value, str):
        return None
    targets = (
        resolved_mentions
        if resolved_mentions is not None
        else all_mentioned_entities(document.source_text, entities, index=index)
    )
    if len(targets) < 2:
        return None
    return CompositionalPlan(
        source_text=document.source_text,
        targets=targets,
        action=active_actions[0].value,
        shared_predicate=True,
    )


def independent_predicate_clauses(
    document: LanguageDocument,
) -> tuple[str, ...]:
    """Return source clauses only when ``AND`` joins complete predicates.

    Clause spans are sliced verbatim from the original input.  No connector,
    target or action is invented.  An incomplete operand returns an empty
    tuple so the caller can reject the whole compound atomically.
    """
    relations = tuple(
        relation
        for relation in document.structure.relations
        if relation.kind is StructuralRelationKind.AND
    )
    if not relations:
        return ()
    clause_by_id = {
        clause.clause_id: clause for clause in document.structure.clauses
    }
    ordered_ids: list[str] = []
    for relation in relations:
        if relation.source_clause not in ordered_ids:
            ordered_ids.append(relation.source_clause)
        if relation.target_clause not in ordered_ids:
            ordered_ids.append(relation.target_clause)
    clauses = tuple(clause_by_id[item] for item in ordered_ids if item in clause_by_id)
    if len(clauses) != len(ordered_ids):
        return ()
    actions = document.semantics.matching(SemanticKind.ACTION)
    markers = document.semantics.matching(SemanticKind.COMMAND_MARKER)
    all_have_actions = all(
        any(clause.char_start <= span.start < clause.char_end for span in actions)
        for clause in clauses
    )
    all_have_markers = all(
        any(clause.char_start <= span.start < clause.char_end for span in markers)
        for clause in clauses
    )
    if not (all_have_actions or all_have_markers):
        return ()
    return tuple(
        document.source_text[clause.char_start:clause.char_end].strip(" ,")
        for clause in clauses
    )


def build_compositional_plan(
    turn: SemanticTurn,
    entities: list[EntitySnapshot],
    *,
    index: EntityIndex | None = None,
) -> CompositionalPlan | None:
    """Recognise one safe action distributed over named coordinated targets."""
    markers = turn.semantic_analysis.matching(SemanticKind.COMMAND_MARKER)
    actions = turn.semantic_analysis.values(SemanticKind.ACTION)
    action_values = sorted(value for value in actions if isinstance(value, str))
    if (
        turn.speech_act is not SpeechAct.COMMAND
        or not turn.safe_to_execute_directly
        or turn.coordination is not CoordinationKind.ADDITIVE
        or len(markers) != 1
        or has_word(turn.source_text, "ausser", "oder")
    ):
        return None
    targets = all_mentioned_entities(turn.source_text, entities, index=index)
    if len(targets) < 2:
        return None
    return CompositionalPlan(
        turn.source_text,
        targets,
        action_values[-1] if action_values else None,
        shared_predicate=True,
    )


def project_target(plan: CompositionalPlan, selected: EntitySnapshot) -> str:
    """Project one target while preserving the shared predicate verbatim."""
    candidate = plan.source_text
    for entity in plan.targets:
        if entity.entity_id == selected.entity_id:
            continue
        for name in sorted((entity.friendly_name, *entity.aliases), key=len, reverse=True):
            candidate = re.sub(
                rf"(?<!\w){re.escape(name)}(?!\w)", " ", candidate, flags=re.I
            )
    candidate = re.sub(r"\b(?:und|sowie|außerdem)\b", " ", candidate, flags=re.I)
    return re.sub(r"\s+", " ", candidate).strip(" ,")
