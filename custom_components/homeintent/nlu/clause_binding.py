"""Typed, source-backed bindings for coordinated write clauses.

This is the final fail-closed boundary between structural language analysis
and any router that may materialize a write.  It reuses the shared
``LanguageDocument`` and ``ClauseMeaning`` reader; it is not a second NLU
pipeline.  Every independent predicate receives its own operation, targets,
place, value/source, consumed spans and residue.  A shared predicate over
coordinated objects remains one binding, because grammar explicitly scopes
that predicate over those objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from ..media import parse_media_request
from ..service_call import ServiceCallPlan
from .clause_reading import ClauseMeaning, read_clauses
from .composition import independent_predicate_clauses
from .german_structure import ClauseKind, StructuralRelationKind
from .language_frontend import LanguageDocument
from .place_model import Place, PlaceKind, PlaceLexicon, build_place_lexicon
from .semantic_exclusion import has_exclusion_clause
from .semantic_utterance import SpeechAct
from .target_resolution import ResolutionOutcome, resolve_description

__all__ = (
    "ClauseBinding",
    "ClauseBindingAnalysis",
    "SourceResolution",
    "analyse_clause_bindings",
)

_DEDICATED_CLAUSE_KINDS = frozenset({
    ClauseKind.RELATIVE,
    ClauseKind.REPAIR,
    ClauseKind.EXCLUSION,
    ClauseKind.CONDITION,
    ClauseKind.TEMPORAL,
})


class SourceResolution(StrEnum):
    """How a play/source value is grounded inside its own clause."""

    NONE = "none"
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ClauseBinding:
    """One predicate and everything that is allowed to belong to it."""

    clause_id: str
    char_start: int
    char_end: int
    text: str
    operation: str | None
    target_expression: tuple[str, ...]
    resolved_target_ids: tuple[str, ...]
    place_expression: tuple[str, ...]
    resolved_place_ids: tuple[str, ...]
    source: str | None = None
    source_resolution: SourceResolution = SourceResolution.NONE
    value: float | int | str | None = None
    consumed_token_spans: tuple[tuple[int, int], ...] = ()
    residue: tuple[str, ...] = ()
    plans: tuple[ServiceCallPlan, ...] = ()
    rejection_reason: str | None = None

    @property
    def complete(self) -> bool:
        return self.rejection_reason is None and self.operation is not None and not self.residue

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Stable semantics used when a preview is checked again after Yes."""
        return (
            self.operation,
            self.resolved_target_ids,
            self.resolved_place_ids,
            normalize_for_compare(self.source or ""),
            self.source_resolution,
            self.value,
            tuple(
                (plan.domain, plan.service, _plan_ids(plan), tuple(sorted(plan.data.items())))
                for plan in self.plans
            ),
        )


@dataclass(frozen=True)
class ClauseBindingAnalysis:
    """All coordinated write bindings, validated before the first write."""

    coordinated: bool
    bindings: tuple[ClauseBinding, ...] = ()
    rejection_reason: str | None = None

    @property
    def complete(self) -> bool:
        return (
            self.coordinated
            and self.rejection_reason is None
            and bool(self.bindings)
            and all(binding.complete for binding in self.bindings)
        )

    @property
    def fingerprint(self) -> tuple[tuple[object, ...], ...]:
        return tuple(binding.fingerprint for binding in self.bindings)


def _plan_ids(plan: ServiceCallPlan) -> tuple[str, ...]:
    values = (plan.entity_id,) if isinstance(plan.entity_id, str) else tuple(plan.entity_id)
    return tuple(sorted(values))


def _number_matches(value: object, expected: float) -> bool:
    return isinstance(value, (int, float)) and abs(float(value) - expected) < 1e-9


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _plan_matches_operation(
    plan: ServiceCallPlan,
    operation: str | None,
    value: float | int | str | None,
) -> bool:
    """Require a closed operation-to-service mapping.

    A new semantic operation must be added here deliberately.  Falling back
    to ``True`` would let an operation from another clause satisfy this
    binding, which is exactly what this boundary exists to prevent.
    """
    if operation is None:
        return False
    if operation == "pause":
        return plan.service == "media_pause"
    if operation == "stop":
        return plan.service in {"media_stop", "stop"}
    if operation == "play":
        return plan.service in {"media_play", "select_source", "play_media"}
    if operation == "turn_on":
        return plan.service == "turn_on"
    if operation == "turn_off":
        return plan.service in {"turn_off", "media_pause"}
    if operation == "open":
        return plan.service in {"open_cover", "open_valve"}
    if operation == "close":
        return plan.service in {"close_cover", "close_valve"}
    if operation == "toggle":
        return plan.service == "toggle"
    if operation == "start":
        return plan.service in {"turn_on", "start", "media_play", "start_mowing"}
    if operation == "mute":
        return plan.service == "volume_mute"
    if operation == "locate":
        return plan.service == "locate"
    if operation == "press":
        return plan.service == "press"
    if operation == "lock":
        return plan.service == "lock"
    if operation == "unlock":
        return plan.service == "unlock"
    if operation == "adjust:volume:1":
        return plan.service == "volume_up"
    if operation == "adjust:volume:-1":
        return plan.service == "volume_down"
    if operation == "adjust:speed:1":
        return plan.service == "increase_speed"
    if operation == "adjust:speed:-1":
        return plan.service == "decrease_speed"
    if operation == "adjust:brightness:1":
        step = _number(plan.data.get("brightness_step_pct"))
        return (
            plan.service == "turn_on"
            and step is not None
            and step > 0
        )
    if operation == "adjust:brightness:-1":
        step = _number(plan.data.get("brightness_step_pct"))
        return (
            plan.service == "turn_on"
            and step is not None
            and step < 0
        )
    if operation in {"adjust:temperature:1", "adjust:temperature:-1"}:
        return plan.service == "set_temperature"
    if operation in {"adjust:level:1", "adjust:level:-1"}:
        direction = 1 if operation.endswith(":1") else -1
        if plan.service in {"volume_up", "increase_speed"}:
            return direction > 0
        if plan.service in {"volume_down", "decrease_speed"}:
            return direction < 0
        if plan.service == "turn_on" and "brightness_step_pct" in plan.data:
            step = _number(plan.data["brightness_step_pct"])
            return step is not None and step * direction > 0
        return plan.service in {"set_cover_position", "set_temperature"}
    if operation.startswith("set:percent"):
        if not isinstance(value, (int, float)):
            return False
        expected = float(value)
        return (
            plan.service == "set_cover_position"
            and _number_matches(plan.data.get("position"), expected)
        ) or (
            plan.service == "turn_on"
            and _number_matches(plan.data.get("brightness_pct"), expected)
        ) or (
            plan.service == "volume_set"
            and _number_matches(plan.data.get("volume_level"), expected / 100)
        )
    if operation.startswith("set:temperature"):
        return (
            isinstance(value, (int, float))
            and plan.service == "set_temperature"
            and _number_matches(plan.data.get("temperature"), float(value))
        )
    return False


def _plans(payload: object | None) -> tuple[ServiceCallPlan, ...]:
    commands = getattr(payload, "commands", None)
    items = commands if commands is not None else ((payload,) if payload is not None else ())
    return tuple(
        plan for item in items
        if isinstance((plan := getattr(item, "plan", None)), ServiceCallPlan)
    )


def _operation(meaning: ClauseMeaning) -> tuple[str | None, float | int | str | None]:
    if len(meaning.actions) == 1:
        return next(iter(meaning.actions)), None
    if meaning.degree is not None:
        return f"adjust:{meaning.degree[0]}:{meaning.degree[1]}", meaning.degree[1]
    if meaning.temperature is not None:
        return "set:temperature", meaning.temperature
    if meaning.percent is not None:
        return "set:percent", meaning.percent
    return None, None


def _selected_clauses(document: LanguageDocument) -> tuple[object, ...]:
    if not any(
        relation.kind is StructuralRelationKind.AND
        for relation in document.structure.relations
    ):
        return ()
    # A comma can coordinate preceding predicates without receiving its own
    # StructuralRelation.  Once an explicit AND proves a predicate chain,
    # every MAIN/COORDINATE clause belongs to the all-or-nothing turn.  Using
    # only the two relation endpoints would silently drop an earlier comma
    # member ("A an, B an und C aus").
    return tuple(
        clause
        for clause in document.structure.clauses
        if clause.kind in {ClauseKind.MAIN, ClauseKind.COORDINATE}
    )


def _places(meaning: ClauseMeaning) -> tuple[Place, ...]:
    found: list[Place] = []
    for description in meaning.descriptions:
        if description.place is not None and description.place not in found:
            found.append(description.place)
    return tuple(found)


def _place_ids(places: Sequence[Place]) -> tuple[str, ...]:
    return tuple(sorted({
        *(f"area:{item}" for place in places for item in place.area_ids),
        *(f"floor:{item}" for place in places for item in place.floor_ids),
    }))


def _source_candidates(
    entities: Sequence[EntitySnapshot], places: Sequence[Place]
) -> tuple[EntitySnapshot, ...]:
    players = tuple(
        entity for entity in entities
        if entity.domain == "media_player"
        and (not places or any(place.contains(entity) for place in places))
    )
    return players


def _clause_places(
    document: LanguageDocument,
    clause: object,
    lexicon: PlaceLexicon,
) -> tuple[Place, ...]:
    start = int(getattr(clause, "char_start"))
    end = int(getattr(clause, "char_end"))
    words = [
        token.canonical for token in document.tokens
        if start <= token.start and token.end <= end and token.is_word
    ]
    return tuple(dict.fromkeys(
        mention.place for mention in lexicon.scan(words)
        if mention.place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}
    ))


def _source_resolution(
    source: str, candidates: Sequence[EntitySnapshot]
) -> tuple[SourceResolution, str | None]:
    key = normalize_for_compare(source)
    matches = [
        (entity, str(item))
        for entity in candidates
        for item in entity.attributes.get("source_list", ())
        if normalize_for_compare(str(item)) == key
    ]
    if len(matches) == 1:
        return SourceResolution.EXACT, matches[0][1]
    if len(matches) > 1:
        return SourceResolution.AMBIGUOUS, None
    return SourceResolution.UNKNOWN, None


def _spans(
    document: LanguageDocument, start: int, end: int, residue: Sequence[str]
) -> tuple[tuple[int, int], ...]:
    remaining = [normalize_for_compare(item) for item in residue]
    consumed: list[tuple[int, int]] = []
    for token in document.tokens:
        if token.start < start or token.end > end or not (token.is_word or token.is_number):
            continue
        if token.canonical in remaining:
            remaining.remove(token.canonical)
            continue
        consumed.append((token.start, token.end))
    return tuple(consumed)


def _binding(
    document: LanguageDocument,
    clause: object,
    meaning: ClauseMeaning,
    entities: Sequence[EntitySnapshot],
    plans: Sequence[ServiceCallPlan],
    inherited: ClauseBinding | None = None,
) -> ClauseBinding:
    clause_id = str(getattr(clause, "clause_id"))
    start = int(getattr(clause, "char_start"))
    end = int(getattr(clause, "char_end"))
    operation, value = _operation(meaning)
    targets: dict[str, EntitySnapshot] = {}
    reason: str | None = None
    inherited_place_expression: tuple[str, ...] | None = None
    inherited_place_ids: tuple[str, ...] | None = None
    for description in meaning.descriptions:
        resolution = resolve_description(description, entities)
        if resolution.outcome is ResolutionOutcome.NONE:
            reason = "unresolved_target"
            break
        if resolution.outcome is ResolutionOutcome.AMBIGUOUS:
            reason = "ambiguous_target"
            break
        targets.update((entity.entity_id, entity) for entity in resolution.entities)
    places = _places(meaning)
    if operation is None:
        reason = reason or "missing_operation"
    if meaning.residue:
        reason = reason or "unexplained_residue"
    target_expression = tuple(
        item.surface or item.explicit_name or item.head for item in meaning.descriptions
    )
    if not meaning.descriptions and inherited is not None and operation is not None and not meaning.residue:
        # Predicate ellipsis ("... lauter und dann leiser") carries the
        # already grounded target/place, never the other predicate.
        targets = {
            entity.entity_id: entity
            for entity in entities
            if entity.entity_id in inherited.resolved_target_ids
        }
        places = ()
        inherited_place_expression = inherited.place_expression
        inherited_place_ids = inherited.resolved_place_ids
        target_expression = ("<ellipsis>",)
    elif not meaning.descriptions:
        reason = reason or "missing_target"
    if any(not any(place.contains(entity) for entity in targets.values()) for place in places):
        reason = "target_outside_clause_place"
    local_plans = tuple(
        plan for plan in plans
        if set(_plan_ids(plan)) and set(_plan_ids(plan)) <= set(targets)
        and _plan_matches_operation(plan, operation, value)
    )
    if plans and targets:
        planned_ids = {
            entity_id for plan in local_plans for entity_id in _plan_ids(plan)
        }
        if not local_plans:
            reason = reason or "plan_not_bound"
        elif planned_ids != set(targets):
            reason = reason or "plan_target_mismatch"
    return ClauseBinding(
        clause_id=clause_id,
        char_start=start,
        char_end=end,
        text=meaning.text,
        operation=operation,
        target_expression=target_expression,
        resolved_target_ids=tuple(sorted(targets)),
        place_expression=(
            inherited_place_expression
            if inherited_place_expression is not None
            else tuple(place.label for place in places)
        ),
        resolved_place_ids=(
            inherited_place_ids
            if inherited_place_ids is not None
            else _place_ids(places)
        ),
        value=value,
        consumed_token_spans=_spans(document, start, end, meaning.residue),
        residue=meaning.residue,
        plans=local_plans,
        rejection_reason=reason,
    )


def _source_binding(
    document: LanguageDocument,
    clause: object,
    meaning: ClauseMeaning,
    entities: Sequence[EntitySnapshot],
    lexicon: PlaceLexicon,
) -> ClauseBinding | None:
    request = parse_media_request(meaning.text)
    if request is None or request.op != "play" or not request.source_words:
        return None
    requested_source = " ".join(request.source_words)
    places = _places(meaning) or _clause_places(document, clause, lexicon)
    candidates = _source_candidates(entities, places)
    resolution, exact_source = _source_resolution(requested_source, candidates)
    source = exact_source or requested_source
    start = int(getattr(clause, "char_start"))
    end = int(getattr(clause, "char_end"))
    # The common multi-command compiler has no source-selection operator.
    # Known, unknown and ambiguous source clauses therefore all reject the
    # entire coordinated write before a preview is possible (safe option B).
    return ClauseBinding(
        clause_id=str(getattr(clause, "clause_id")),
        char_start=start,
        char_end=end,
        text=meaning.text,
        operation="play",
        target_expression=("media_player",),
        resolved_target_ids=tuple(sorted(entity.entity_id for entity in candidates)),
        place_expression=tuple(place.label for place in places),
        resolved_place_ids=_place_ids(places),
        source=source,
        source_resolution=resolution,
        consumed_token_spans=_spans(document, start, end, ()),
        residue=(),
        rejection_reason=(
            "unknown_source" if resolution is SourceResolution.UNKNOWN
            else "ambiguous_source" if resolution is SourceResolution.AMBIGUOUS
            else "compound_source_not_supported"
        ),
    )


def _validated_read_item(item: object) -> bool:
    if getattr(item, "plan", object()) is not None:
        return False
    command = getattr(item, "command", None)
    frame = getattr(command, "source_frame", None)
    action = getattr(frame, "action", None)
    return getattr(action, "name", "") == "QUERY"


def _read_binding(
    document: LanguageDocument,
    clause: object,
    meaning: ClauseMeaning,
    item: object,
) -> ClauseBinding:
    """Represent an already validated read-only member of a mixed plan."""
    command = getattr(item, "command")
    targets = tuple(getattr(command, "entities", ()))
    area = getattr(command, "area", None)
    start = int(getattr(clause, "char_start"))
    end = int(getattr(clause, "char_end"))
    intent = str(getattr(command, "intent", "query"))
    target_expression = tuple(
        item.surface or item.explicit_name or item.head for item in meaning.descriptions
    )
    return ClauseBinding(
        clause_id=str(getattr(clause, "clause_id")),
        char_start=start,
        char_end=end,
        text=meaning.text,
        operation=f"query:{intent}",
        target_expression=target_expression,
        resolved_target_ids=tuple(sorted(entity.entity_id for entity in targets)),
        place_expression=((str(getattr(area, "name")),) if area is not None else ()),
        resolved_place_ids=((f"area:{getattr(area, 'area_id')}",) if area is not None else ()),
        consumed_token_spans=_spans(document, start, end, ()),
    )


def analyse_clause_bindings(
    document: LanguageDocument,
    entities: Sequence[EntitySnapshot],
    payload: object | None = None,
) -> ClauseBindingAnalysis:
    """Bind coordinated predicates, or return a cheap non-coordinated marker."""
    if document.utterance.speech_act is not SpeechAct.COMMAND:
        return ClauseBindingAnalysis(False)
    if has_exclusion_clause(document.utterance.normalized_text) or any(
        clause.kind in _DEDICATED_CLAUSE_KINDS
        for clause in document.structure.clauses
    ):
        # These structures have loss-aware dedicated compilers.  Exclusion
        # lists are also detected lexically because the structural frontend
        # can represent ``mit Ausnahme von A und B`` as MAIN + COORDINATE:
        # that ``und`` joins excluded nominals, never write predicates.  An
        # empty ``read_clauses`` result therefore means "owned elsewhere",
        # not an incomplete immediate write.
        return ClauseBindingAnalysis(False)
    clauses = _selected_clauses(document)
    if len(clauses) < 2:
        return ClauseBindingAnalysis(False)
    place_lexicon = build_place_lexicon(entities)
    meanings = read_clauses(document, entities, lexicon=place_lexicon)
    if not meanings:
        return ClauseBindingAnalysis(True, rejection_reason="clauses_not_readable")
    source_clause = any(
        request.op == "play" and bool(request.source_words)
        for meaning in meanings
        if (request := parse_media_request(meaning.text)) is not None
    )
    shared_predicate = len(meanings) == 1 and len(meanings[0].descriptions) > 1
    independent_predicates = bool(independent_predicate_clauses(document))
    if not (source_clause or shared_predicate or independent_predicates):
        # Structural AND also joins nominal constituents (notably
        # "zwei Stunden und 30 Minuten") and meta-language.  Those are not
        # coordinated writes and keep their established domain validator.
        return ClauseBindingAnalysis(False)
    plans = _plans(payload)
    payload_items = tuple(getattr(payload, "commands", ()))
    if len(meanings) == 1 and len(meanings[0].descriptions) > 1:
        # One explicit predicate shared by coordinated target constituents.
        first, last = clauses[0], clauses[-1]
        synthetic = type("_SharedClause", (), {
            "clause_id": "+".join(str(getattr(item, "clause_id")) for item in clauses),
            "char_start": getattr(first, "char_start"),
            "char_end": getattr(last, "char_end"),
        })()
        bindings = (_binding(document, synthetic, meanings[0], entities, plans),)
    elif len(meanings) == len(clauses):
        rendered: list[ClauseBinding] = []
        previous: ClauseBinding | None = None
        for index, (clause, meaning) in enumerate(zip(clauses, meanings, strict=True)):
            payload_item = payload_items[index] if len(payload_items) == len(clauses) else None
            source = _source_binding(
                document, clause, meaning, entities, place_lexicon
            )
            binding = (
                _read_binding(document, clause, meaning, payload_item)
                if payload_item is not None and _validated_read_item(payload_item)
                else source or _binding(
                    document, clause, meaning, entities, plans, inherited=previous
                )
            )
            rendered.append(binding)
            previous = (
                binding
                if binding.complete
                and binding.operation is not None
                and not binding.operation.startswith("query:")
                else None
            )
        bindings = tuple(rendered)
    else:
        return ClauseBindingAnalysis(True, rejection_reason="clause_count_mismatch")
    rejected = next(
        (binding.rejection_reason for binding in bindings if not binding.complete),
        None,
    )
    return ClauseBindingAnalysis(True, bindings, rejected)
