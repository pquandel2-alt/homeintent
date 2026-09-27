"""Controlled projection from turn meaning to compatibility domain models.

This module never creates German text and never invokes a language parser.
It accepts the already analysed :class:`LanguageDocument`, delegates identity
and location grounding to the existing authoritative resolvers, and emits the
same ``ParseResult``/``SemanticFrame`` consumed by the established validator,
reasoning and service-mapping pipeline.
"""

from __future__ import annotations

from dataclasses import replace
from enum import Enum, auto
from typing import Iterable

from ..areas import AreaResolutionStatus, AreaSnapshot, resolve_area_scored
from ..house_graph import RelationKind, TraversalDirection
from ..entities import EntitySnapshot, normalize_for_compare, spoken_state
from ..world_model import WorldModel
from .composition import CompositionalPlan
from .constraint_resolver import Constraints, resolve_candidates
from .domain_operations import INTENT_BY_DOMAIN_ACTION
from .semantic_catalog import MEASUREMENT_PROPERTY_SPECS, VALUE_INTENT_BY_DOMAIN_PROPERTY
from .entity_resolution import ResolutionStatus, resolve_entity_scored
from .entity_resolution import all_mentioned_entities
from .frame import AreaReference, Quantifier, SemanticFrame, TargetReference
from .german_structure import ClauseKind, StructuralRelationKind
from .language_frontend import LanguageDocument
from .parser import ParseResult
from .primitives import (
    NumericUnit,
    NumericValue,
    SemanticAction,
    SemanticProperty,
    SemanticQuantity,
)
from .query_command import (
    AggregateExpression,
    AggregateKind,
    CompareExpression,
    GroupExpression,
    LimitExpression,
    MeasurementExpression,
    OrderExpression,
    PropertyOperand,
    QueryCommand,
    QueryFilter,
    QueryRelationKind,
    QueryResult,
    QueryResultStatus,
    QueryScope,
    QueryTarget,
    QueryTargetKind,
    RelationalComparison,
    RelationalOperator,
    RelationConstraint,
    RelationFilterExpression,
    SetExpression,
    SetOperator,
    SortDirection,
    SourceExpression,
    StateFilterExpression,
    StateDurationFilterExpression,
    ThresholdExpression,
    QueryTraversal,
    ReasoningStep,
    ReasoningTrace,
)
from .query_executor import QueryExecutor
from .normalize import normalize
from .semantic_exclusion import has_exclusion_clause, split_exclusion
from .semantic_graph import SemanticEdgeKind, SemanticGraph, SemanticNodeKind
from .semantic_lexicon import SemanticKind, SemanticSpan
from .semantic_location import resolve_coordinated_locations, resolve_semantic_location
from .semantic_state import SemanticState, matches_semantic_state
from .temporal_semantics import TemporalKind


class ProjectionStatus(Enum):
    """Whether a graph was safely representable by today's domain model."""

    PROJECTED = auto()
    AMBIGUOUS = auto()
    UNSUPPORTED = auto()


_ACTION_ENUM = {
    "turn_on": SemanticAction.TURN_ON,
    "turn_off": SemanticAction.TURN_OFF,
    "toggle": SemanticAction.TOGGLE,
    "open": SemanticAction.OPEN,
    "close": SemanticAction.CLOSE,
    "lock": SemanticAction.LOCK,
    "unlock": SemanticAction.UNLOCK,
    "mute": SemanticAction.MUTE,
    "locate": SemanticAction.LOCATE,
    "press": SemanticAction.PRESS,
    "start": SemanticAction.START,
    "play": SemanticAction.START,
    "pause": SemanticAction.PAUSE,
    "stop": SemanticAction.STOP,
}

_PROPERTY_ENUM = {
    "temperature": SemanticProperty.TEMPERATURE,
    "humidity": SemanticProperty.HUMIDITY,
    "brightness": SemanticProperty.BRIGHTNESS,
    "position": SemanticProperty.POSITION,
    "power": SemanticProperty.POWER,
    "energy": SemanticProperty.ENERGY,
    "battery": SemanticProperty.BATTERY,
    "volume": SemanticProperty.VOLUME,
}

_RELATIONAL_OPERATOR = {
    "lt": RelationalOperator.LT,
    "lte": RelationalOperator.LTE,
    "eq": RelationalOperator.EQ,
    "gte": RelationalOperator.GTE,
    "gt": RelationalOperator.GT,
}

_QUERY_EXECUTOR = QueryExecutor()


_NUMBER_WORDS = {
    "null": 0, "ein": 1, "eine": 1, "einen": 1, "eins": 1,
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}


def _entity_source(domain: str, device_class: str | None = None) -> SourceExpression:
    return SourceExpression(QueryTarget(domain=domain, device_class=device_class))


def _state_for_device_class(
    document: LanguageDocument, device_class: str
) -> SemanticState | None:
    classes = tuple(document.semantics.matching(SemanticKind.DEVICE_CLASS))
    matching = tuple(
        span for span in classes
        if isinstance(span.value, tuple) and span.value[1] == device_class
    )
    if not matching:
        return None
    if device_class in {"window", "door", "garage_door"}:
        for index, token in enumerate(document.tokens):
            if token.canonical != "auf":
                continue
            following = (
                document.tokens[index + 1]
                if index + 1 < len(document.tokens) else None
            )
            if following is None or not following.is_number:
                return SemanticState.OPEN
    anchor = matching[0]
    states = tuple(
        span for span in document.semantics.matching(SemanticKind.STATE)
        if span.text.casefold() not in {"ein", "eine", "einen", "einem", "einer"}
    )
    if not states:
        return None
    selected = min(states, key=lambda span: abs(span.start - anchor.start)).value
    return selected if isinstance(selected, SemanticState) else None


def _explain_reasoning_result(result: QueryResult, world: WorldModel) -> str | None:
    """Render only explicit graph/state facts used by the symbolic result."""
    facts: list[str] = []
    graph = world.house_graph
    for area in result.areas:
        area_node = f"area:{area.area_id}"
        evidence_entities = tuple(
            world.entities_by_id.get(node.node_id.removeprefix("entity:"))
            for node in graph.sources(area_node, RelationKind.LOCATED_IN)
            if node.node_id.startswith("entity:")
        )
        windows = tuple(
            entity for entity in evidence_entities
            if entity is not None
            and entity.device_class == "window"
            and matches_semantic_state(entity, SemanticState.OPEN)
        )
        if windows:
            facts.append(
                f"in {area.name} ist "
                + ", ".join(entity.friendly_name for entity in windows)
                + " offen"
            )
            continue
        measurements = tuple(
            entity for entity in evidence_entities
            if entity is not None and entity.device_class in {
                "temperature", "humidity", "battery", "power", "energy"
            }
        )
        if measurements:
            facts.append(
                f"für {area.name} wurde "
                + ", ".join(
                    f"{entity.friendly_name} mit {entity.state}{(' ' + entity.unit) if entity.unit else ''}"
                    for entity in measurements
                )
                + " verwendet"
            )
    if not facts and result.entities:
        facts.extend(
            f"{entity.friendly_name} hat den Zustand {spoken_state(entity.state)}"
            for entity in result.entities
        )
    return "; ".join(facts).capitalize() + "." if facts else None


def project_semantic_reasoning_query(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Compile compositional relational/aggregate meanings into QueryCommand.

    The recognizer combines independently typed target, property, state,
    relation, comparator and query-scope evidence. It intentionally does not
    match complete sentences; surface variants sharing those components
    therefore produce the same algebra tree.
    """
    if world_model is None:
        return None
    normalized = normalize_for_compare(document.source_text)
    words = frozenset(token.canonical for token in document.tokens if token.is_word)
    lexical_area_target = bool(
        words & {"raum", "räume", "räumen", "raeume", "raeumen", "zimmer", "zimmern"}
    )
    floor_group = "pro" in words and bool(words & {"etage", "stockwerk", "geschoss"})
    domains = tuple(str(value) for value in document.semantics.values(SemanticKind.DOMAIN))
    classes = tuple(
        value for value in document.semantics.values(SemanticKind.DEVICE_CLASS)
        if isinstance(value, tuple) and len(value) == 2
    )
    properties = tuple(str(value) for value in document.semantics.values(SemanticKind.PROPERTY))
    scopes = {str(value) for value in document.semantics.values(SemanticKind.QUERY_SCOPE)}
    area_target = lexical_area_target or "locations" in scopes
    comparators = tuple(str(value) for value in document.semantics.values(SemanticKind.COMPARATOR))
    domain_spans = document.semantics.matching(SemanticKind.DOMAIN)
    negated_domain_constraint = any(
        (clause := document.structure.clause_for_char(span.start)) is not None
        and any(
            negation.clause_id == clause.clause_id
            for negation in document.structure.negations
        )
        for span in domain_spans
    )
    area_positions = tuple(
        span.start
        for span in document.semantics.matching(SemanticKind.QUERY_SCOPE)
        if str(span.value) == "locations"
    ) + tuple(
        token.start
        for token in document.tokens
        if token.canonical in {
            "raum", "räume", "räumen", "raeume", "raeumen",
            "zimmer", "zimmern",
        }
    )
    domain_positions = tuple(
        span.start for span in document.semantics.matching(SemanticKind.DOMAIN)
    )
    # In "Lampen in Räumen ..." the entity precedes and therefore owns the
    # question focus.  In "Räume ... aber kein Licht" the room set remains
    # the focus and the later entity phrase is a constraint on that set.
    primary_entity_target = bool(
        domains
        and area_positions
        and domain_positions
        and min(domain_positions) < min(area_positions)
    )

    expression = None
    output_kind = QueryTargetKind.ENTITY

    # Continuous current-state duration comes from the typed temporal span.
    # Event-history wording has no SINCE expression and therefore cannot be
    # collapsed into this last_changed-backed operation.
    since = tuple(
        item for item in document.temporal
        if item.kind is TemporalKind.SINCE and item.seconds is not None
    )
    state_values = tuple(
        span.value for span in document.semantics.matching(SemanticKind.STATE)
        if isinstance(span.value, SemanticState)
        and span.text.casefold() not in {"ein", "eine", "einen", "einem", "einer"}
    )
    if len(since) == 1 and len(set(state_values)) == 1:
        if len(classes) == 1:
            duration_domain = str(classes[0][0])
            duration_class = str(classes[0][1])
        elif len(set(domains)) == 1:
            duration_domain = domains[0]
            duration_class = None
        else:
            duration_domain = ""
            duration_class = None
        if duration_domain:
            expression = StateDurationFilterExpression(
                StateFilterExpression(
                    _entity_source(duration_domain, duration_class), state_values[0]
                ),
                since[0].seconds or 0,
            )

    # Generic target + relation + nested constraint.
    related_class = next(
        (
            item for item in classes
            if _state_for_device_class(document, str(item[1])) is not None
        ),
        classes[0] if len(classes) == 1 else None,
    )
    if related_class is not None:
        related_state = _state_for_device_class(document, str(related_class[1]))
        related_entities: SourceExpression | StateFilterExpression = _entity_source(
            str(related_class[0]), str(related_class[1])
        )
        if related_state is not None:
            related_entities = StateFilterExpression(related_entities, related_state)
        if floor_group:
            expression = GroupExpression(
                related_entities,
                QueryTraversal(((RelationKind.ON_FLOOR, TraversalDirection.OUTGOING),)),
                QueryTargetKind.FLOOR,
            )
            output_kind = QueryTargetKind.FLOOR
        elif area_target:
            location = resolve_semantic_location(
                document.source_text, entities, world_model
            )
            requested_floor = location[2] if location is not None else None
            areas = RelationFilterExpression(
                SourceExpression(QueryTarget(
                    kind=QueryTargetKind.AREA, floor_id=requested_floor
                )),
                QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.INCOMING),)),
                related_entities,
            )
            outer_states = tuple(
                span.value
                for span in document.semantics.matching(SemanticKind.STATE)
                if span.value is not related_state
                and span.text.casefold()
                not in {"ein", "eine", "einen", "einem", "einer"}
                and not (
                    related_state is SemanticState.OPEN
                    and span.text.casefold() == "steht"
                )
            )
            if domains and ("none" in scopes or negated_domain_constraint):
                target_domain = domains[0]
                excluded_entities: SourceExpression | StateFilterExpression = _entity_source(
                    target_domain
                )
                if len(set(outer_states)) == 1:
                    excluded_entities = StateFilterExpression(
                        excluded_entities, outer_states[0]  # type: ignore[arg-type]
                    )
                excluded_areas = RelationFilterExpression(
                    SourceExpression(QueryTarget(kind=QueryTargetKind.AREA)),
                    QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.INCOMING),)),
                    excluded_entities,
                )
                expression = SetExpression(areas, SetOperator.DIFFERENCE, excluded_areas)
                output_kind = QueryTargetKind.AREA
            elif domains and primary_entity_target:
                target_domain = domains[0]
                expression = RelationFilterExpression(
                    _entity_source(target_domain),
                    QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.OUTGOING),)),
                    areas,
                )
                if len(set(outer_states)) == 1:
                    expression = StateFilterExpression(expression, outer_states[0])  # type: ignore[arg-type]
            elif domains:
                target_domain = domains[0]
                constrained_entities: SourceExpression | StateFilterExpression = (
                    _entity_source(target_domain)
                )
                if len(set(outer_states)) == 1:
                    constrained_entities = StateFilterExpression(
                        constrained_entities, outer_states[0]  # type: ignore[arg-type]
                    )
                constrained_areas = RelationFilterExpression(
                    SourceExpression(QueryTarget(kind=QueryTargetKind.AREA)),
                    QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.INCOMING),)),
                    constrained_entities,
                )
                expression = SetExpression(
                    areas, SetOperator.INTERSECTION, constrained_areas
                )
                output_kind = QueryTargetKind.AREA
            else:
                expression = areas
                output_kind = QueryTargetKind.AREA

            if comparators:
                number = next(
                    (
                        int(word) if word.isdigit() else _NUMBER_WORDS[word]
                        for word in words
                        if word.isdigit() or word in _NUMBER_WORDS
                    ),
                    None,
                )
                operator = _RELATIONAL_OPERATOR.get(comparators[0])
                if number is not None and operator is not None:
                    grouped = GroupExpression(
                        related_entities,
                        QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.OUTGOING),)),
                        QueryTargetKind.AREA,
                        include_empty_groups=(
                            operator in {
                                RelationalOperator.LTE,
                                RelationalOperator.EQ,
                            }
                        ),
                    )
                    threshold = ThresholdExpression(grouped, operator, float(number))
                    if "exists" in scopes and "locations" not in scopes:
                        expression = AggregateExpression(threshold, AggregateKind.EXISTS)
                    else:
                        expression = (
                            SetExpression(
                                threshold,
                                SetOperator.INTERSECTION,
                                SourceExpression(QueryTarget(
                                    kind=QueryTargetKind.AREA,
                                    floor_id=requested_floor,
                                )),
                            )
                            if requested_floor is not None else threshold
                        )
                        output_kind = QueryTargetKind.AREA

    # Measurement superlative over a typed selection scope.
    superlative = any(
        marker in normalized
        for marker in (
            "wärmst", "waermst", "kältest", "kaeltest", "höchst", "hoechst",
            "niedrigst", "feuchtest",
        )
    )
    if expression is None and properties and superlative:
        property_ = _PROPERTY_ENUM.get(properties[0])
        if property_ is not None:
            if area_target or "locations" in scopes:
                source = SourceExpression(QueryTarget(kind=QueryTargetKind.AREA))
                output_kind = QueryTargetKind.AREA
            elif classes:
                source = _entity_source(str(classes[0][0]), str(classes[0][1]))
            else:
                return None
            direction = (
                SortDirection.ASCENDING
                if any(marker in normalized for marker in ("kältest", "kaeltest", "niedrigst"))
                else SortDirection.DESCENDING
            )
            expression = LimitExpression(
                OrderExpression(
                    source,
                    MeasurementExpression(source, property_),
                    direction,
                ),
                1,
            )

    # Comparative set against one explicitly resolved area.
    if expression is None and area_target and properties and len(comparators) == 1:
        property_ = _PROPERTY_ENUM.get(properties[0])
        separator = " als "
        reference_text = normalized.split(separator, 1)[1].strip(" ?.!") if separator in normalized else ""
        reference = resolve_area_scored(
            reference_text, entities, snapshots=world_model.areas
        )
        operator = _RELATIONAL_OPERATOR.get(comparators[0])
        if (
            property_ is not None
            and operator is not None
            and reference.status is AreaResolutionStatus.RESOLVED
            and reference.area is not None
        ):
            all_areas = SourceExpression(QueryTarget(kind=QueryTargetKind.AREA))
            one_area = SourceExpression(QueryTarget(
                kind=QueryTargetKind.AREA, area=reference.area
            ))
            expression = CompareExpression(
                MeasurementExpression(all_areas, property_),
                operator,
                MeasurementExpression(one_area, property_),
            )
            output_kind = QueryTargetKind.AREA

    if expression is None:
        return None
    command = QueryCommand(
        intent="HassStateQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(kind=output_kind),
        filter=QueryFilter(),
        algebra=expression,
    )
    query_result = _QUERY_EXECUTOR.execute(command, [], world_model)
    return ParseResult(
        frame=SemanticFrame(
            intent=command.intent,
            target=TargetReference(document.source_text),
            area=None,
            quantifier=Quantifier("all"),
            parameters={"query_command": command, "query_result": query_result},
            source_text=document.source_text,
            action=SemanticAction.QUERY,
            property=_PROPERTY_ENUM.get(properties[0]) if properties else None,
            semantic_graph=graph,
        ),
        resolved_entities=list(query_result.entities),
        explanation_text=_explain_reasoning_result(query_result, world_model),
    )


def project_property_repair_query(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Replace one query property while retaining one proven location slot."""
    replacements = tuple(
        relation for relation in document.structure.relations
        if relation.kind is StructuralRelationKind.REPLACES
    )
    if len(replacements) != 1:
        return None
    clauses = {clause.clause_id: clause for clause in document.structure.clauses}
    original = clauses.get(replacements[0].source_clause)
    replacement = clauses.get(replacements[0].target_clause)
    if original is None or replacement is None:
        return None
    spans = document.semantics.matching(SemanticKind.PROPERTY)
    old = {str(span.value) for span in spans if original.char_start <= span.start < original.char_end}
    new = {str(span.value) for span in spans if replacement.char_start <= span.start < replacement.char_end}
    if len(old) != 1 or len(new) != 1 or old == new:
        return None
    property_name = next(iter(new))
    spec = MEASUREMENT_PROPERTY_SPECS.get(property_name)
    if spec is None:
        return None
    original_text = document.source_text[original.char_start:original.char_end]
    replacement_text = document.source_text[replacement.char_start:replacement.char_end]
    location = resolve_semantic_location(original_text, entities, world_model)
    if location is None or resolve_semantic_location(replacement_text, entities, world_model) is not None:
        return None
    domain, device_class, label, canonical_property = spec
    pool = (
        world_model.select_entities(
            domain=domain, device_class=device_class,
            area_id=location[1], floor_id=location[2],
        )
        if world_model is not None else tuple(entities)
    )
    matched = tuple(
        entity for entity in pool
        if entity.domain == domain and entity.device_class == device_class
        and (location[1] is None or entity.area_id == location[1])
        and (location[2] is None or entity.floor_id == location[2])
    )
    if not matched:
        return None
    primary = matched[0]
    if location[1] is not None and len(matched) > 1:
        preferred_ids = {
            node.node_id.removeprefix("entity:")
            for node in world_model.house_graph.related(
                f"area:{location[1]}", RelationKind.PREFERRED_MEASUREMENT
            )
            if node.node_id.startswith("entity:")
        } if world_model is not None else set()
        preferred = tuple(
            entity for entity in matched if entity.entity_id in preferred_ids
        )
        if len(preferred) == 1:
            primary = preferred[0]
            matched = (primary,)
    single_scope = location[1] is not None
    query_command = QueryCommand(
        "HassGetState",
        QueryScope.SINGLE if single_scope else QueryScope.LIST,
        QueryTarget(
            domain=domain,
            device_class=device_class,
            entity_id=primary.entity_id if len(matched) == 1 else None,
        ),
        QueryFilter(),
    )
    query_result = _QUERY_EXECUTOR.execute(query_command, list(matched), world_model)
    return ParseResult(
        frame=SemanticFrame(
            intent="HassGetState" if single_scope else "HassLocationPropertyQuery",
            target=TargetReference(
                location[0], primary.entity_id if len(matched) == 1 else None,
                domain, device_class,
            ),
            area=(AreaReference(location[0], location[1], primary.area_name) if location[1] else None),
            quantifier=None if single_scope else Quantifier("all"),
            parameters={
                "property": canonical_property, "property_label": label,
                "average": False,
                "location_kind": "area" if location[1] else "floor",
                "location_id": location[1] or location[2],
                "repair_replacement": property_name,
                "reasoning_trace": ReasoningTrace((ReasoningStep(
                    "repair_replacement",
                    tuple(f"property:{item}" for item in sorted(old)),
                    (f"property:{property_name}",),
                    detail="property",
                ),)),
                "query_command": query_command,
                "query_result": query_result,
            },
            source_text=document.source_text,
            action=SemanticAction.QUERY,
            property=_PROPERTY_ENUM.get(property_name),
            semantic_graph=graph.with_grounded_entities(entity.entity_id for entity in matched),
        ),
        resolved_entities=list(matched),
    )


def project_relational_command(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Select command targets with the read-only algebra, then re-ground live.

    Selection is evidence, never execution authority: the returned ordinary
    ``ParseResult`` still goes through the canonical validator, policy and
    service mapper. Only a positive, direct, explicitly universal command is
    eligible here.
    """
    if world_model is None or not document.utterance.safe_to_execute_directly:
        return None
    quantifiers = {
        str(value) for value in document.semantics.values(SemanticKind.QUANTIFIER)
    }
    normalized = normalize_for_compare(document.source_text)
    if "all" not in quantifiers and "überall" not in normalized:
        return None
    domains = {
        str(value) for value in document.semantics.values(SemanticKind.DOMAIN)
    }
    classes = {
        value for value in document.semantics.values(SemanticKind.DEVICE_CLASS)
        if isinstance(value, tuple) and len(value) == 2
    }
    related_classes = tuple(
        item for item in classes
        if _state_for_device_class(document, str(item[1])) is not None
    )
    if len(domains) != 1 or len(related_classes) != 1:
        return None
    related_class = related_classes[0]
    domain = next(iter(domains))
    actions = {
        str(span.value)
        for span in document.semantics.matching(SemanticKind.ACTION)
        if span.text.casefold() not in {"ein", "eine", "einen", "einem", "einer"}
        if INTENT_BY_DOMAIN_ACTION.get((domain, str(span.value))) is not None
    }
    if len(actions) != 1:
        return None
    action = next(iter(actions))
    semantic_action = _ACTION_ENUM.get(action)
    intent = INTENT_BY_DOMAIN_ACTION.get((domain, action))
    if semantic_action is None or intent is None:
        return None
    related_state = _state_for_device_class(document, str(related_class[1]))
    if related_state is None:
        return None
    qualifying_areas = RelationFilterExpression(
        SourceExpression(QueryTarget(kind=QueryTargetKind.AREA)),
        QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.INCOMING),)),
        StateFilterExpression(
            _entity_source(str(related_class[0]), str(related_class[1])),
            related_state,
        ),
    )
    target_expression = RelationFilterExpression(
        _entity_source(domain),
        QueryTraversal(((RelationKind.LOCATED_IN, TraversalDirection.OUTGOING),)),
        qualifying_areas,
    )
    selection = QueryCommand(
        "HassStateQuery", QueryScope.LIST, QueryTarget(domain=domain),
        QueryFilter(), target_expression,
    )
    selected = _QUERY_EXECUTOR.execute(selection, [], world_model)
    if selected.status is not QueryResultStatus.MATCHED or not selected.entities:
        # The relation was understood, but its live target set is empty (or
        # could not be evaluated). Keep that meaning authoritative so the
        # generic command compiler cannot silently discard the constraint
        # and broaden the action to every entity in the domain.
        return ParseResult(
            frame=SemanticFrame(
                intent=intent,
                target=TargetReference(document.source_text, domain=domain),
                area=None,
                quantifier=Quantifier("all"),
                parameters={
                    "selection_query": selection,
                    "reasoning_trace": selected.trace,
                },
                source_text=document.source_text,
                action=semantic_action,
                semantic_graph=graph,
            ),
            resolved_entities=[],
        )
    # Re-ground every id against this exact live turn snapshot before the
    # normal action pipeline sees it.
    targets = tuple(
        world_model.entities_by_id[entity.entity_id]
        for entity in selected.entities
        if entity.entity_id in world_model.entities_by_id
    )
    if len(targets) != len(selected.entities):
        return None
    first = targets[0]
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                document.source_text,
                first.entity_id if len(targets) == 1 else None,
                domain,
                first.device_class,
            ),
            area=None,
            quantifier=Quantifier("all"),
            parameters={
                "selection_query": selection,
                "reasoning_trace": selected.trace,
            },
            source_text=document.source_text,
            action=semantic_action,
            semantic_graph=graph.with_grounded_entities(
                entity.entity_id for entity in targets
            ),
        ),
        resolved_entities=list(targets),
    )


def attach_graph(result: ParseResult, graph: SemanticGraph) -> ParseResult:
    """Attach canonical meaning provenance to a compatibility frame."""
    return replace(result, frame=replace(result.frame, semantic_graph=graph))


def project_relational_comparison_query(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Project a two-operand live comparison directly from the graph.

    Both operands must be explicit, uniquely grounded registry entities. The
    shared ``QueryExecutor`` reads their live values; missing readings and
    incompatible units remain non-projectable instead of being converted or
    guessed. Entity order is recovered only from the unchanged source spans.
    """
    if world_model is None:
        return None
    comparators = graph.nodes_of_kind(SemanticNodeKind.COMPARISON)
    if len(comparators) != 1:
        return None
    operator = _RELATIONAL_OPERATOR.get(comparators[0].value)
    if operator is None:
        return None
    mentioned = all_mentioned_entities(
        document.source_text,
        entities,
        index=world_model.entity_index,
    )
    normalized_source = normalize_for_compare(document.source_text)

    def mention_position(entity: EntitySnapshot) -> int:
        positions = tuple(
            position
            for name in (entity.friendly_name, *entity.aliases)
            if (normalized_name := normalize_for_compare(name))
            and (position := normalized_source.find(normalized_name)) >= 0
        )
        return min(positions, default=len(normalized_source))

    if len(mentioned) == 2:
        operands = tuple(
            sorted(
                mentioned,
                key=lambda entity: (mention_position(entity), entity.entity_id),
            )
        )
        if mention_position(operands[1]) == len(normalized_source):
            return None
        grounded_locations: tuple[tuple[str, str | None, str | None], ...] = ()
    else:
        area_mentions = sorted(
            (
                position,
                area,
                surface,
            )
            for area in world_model.areas
            for surface in (area.name, *area.aliases)
            if (normalized_area := normalize_for_compare(surface))
            and (position := normalized_source.find(normalized_area)) >= 0
        )
        unique_areas: list[tuple[int, AreaSnapshot, str]] = []
        seen_area_ids: set[str] = set()
        for position, area, surface in area_mentions:
            if area.area_id not in seen_area_ids:
                unique_areas.append((position, area, surface))
                seen_area_ids.add(area.area_id)
        if len(unique_areas) != 2:
            return None
        readings = tuple(
            world_model.select_entities(
                domain="sensor",
                device_class="temperature",
                area_id=area.area_id,
            )
            for _, area, _ in unique_areas
        )
        if any(len(items) != 1 for items in readings):
            return None
        operands = (readings[0][0], readings[1][0])
        grounded_locations = tuple(
            (surface, area.area_id, None)
            for _, area, surface in unique_areas
        )

    properties = {node.value for node in graph.nodes_of_kind(SemanticNodeKind.PROPERTY)}
    if len(properties) == 1:
        property_ = _PROPERTY_ENUM.get(next(iter(properties)))
    else:
        device_classes = {entity.device_class for entity in operands}
        only_device_class = next(iter(device_classes)) if len(device_classes) == 1 else None
        property_ = (
            _PROPERTY_ENUM.get(only_device_class)
            if only_device_class is not None
            else None
        )
    if property_ is None:
        return None

    comparison = RelationalComparison(
        PropertyOperand(operands[0].entity_id, property_),
        operator,
        PropertyOperand(operands[1].entity_id, property_),
    )
    command = QueryCommand(
        intent="HassRelationalComparison",
        scope=QueryScope.SINGLE,
        target=QueryTarget(
            domain=operands[0].domain,
            device_class=operands[0].device_class,
            entity_id=operands[0].entity_id,
        ),
        filter=QueryFilter(relational=comparison),
    )
    query_result = _QUERY_EXECUTOR.execute(command, list(operands), world_model)
    if query_result.status is QueryResultStatus.TARGET_NOT_FOUND:
        return None
    grounded_graph = graph.with_grounded_entities(
        entity.entity_id for entity in operands
    )
    if grounded_locations:
        grounded_graph = grounded_graph.with_grounded_locations(grounded_locations)
    return ParseResult(
        frame=SemanticFrame(
            intent=command.intent,
            target=TargetReference(
                operands[0].friendly_name,
                operands[0].entity_id,
                operands[0].domain,
                operands[0].device_class,
            ),
            area=None,
            parameters={"query_command": command, "query_result": query_result},
            source_text=document.source_text,
            action=SemanticAction.QUERY,
            property=property_,
            semantic_graph=grounded_graph,
        ),
        # SemanticCommand's target cardinality remains singular. The second
        # operand lives in the typed comparison/QueryResult, not in the
        # command target list (where it would look like an ambiguous action
        # target to the central validator).
        resolved_entities=[operands[0]],
    )


def project_relationship_query(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Project the safe, registry-proven ``same area`` query shape."""
    if world_model is None:
        return None
    relations = graph.nodes_of_kind(SemanticNodeKind.RELATION)
    if len(relations) != 1 or relations[0].value != "same_area":
        return None
    relation_start = relations[0].start
    if relation_start is None:
        return None
    anchors = all_mentioned_entities(
        document.source_text,
        entities,
        index=world_model.entity_index,
    )
    if len(anchors) != 1:
        return None
    anchor = anchors[0]
    domains = {
        str(span.value)
        for span in document.semantics.matching(SemanticKind.DOMAIN)
        if span.end <= relation_start
    }
    device_classes = {
        str(span.value)
        for span in document.semantics.matching(SemanticKind.DEVICE_CLASS)
        if span.end <= relation_start
    }
    if len(domains) != 1 or len(device_classes) > 1:
        return None
    domain = next(iter(domains))
    device_class = next(iter(device_classes), None)
    candidates = list(
        world_model.select_entities(domain=domain, device_class=device_class)
    )
    command = QueryCommand(
        intent="HassRelationshipQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(domain=domain, device_class=device_class),
        filter=QueryFilter(
            relationship=RelationConstraint(
                QueryRelationKind.SAME_AREA, anchor.entity_id
            )
        ),
    )
    query_result = _QUERY_EXECUTOR.execute(command, candidates, world_model)
    if query_result.status is QueryResultStatus.TARGET_NOT_FOUND:
        return None
    matched = list(query_result.entities)
    # The graph describes the requested relation and its grounded anchor.
    # Potentially thousands of result members stay in the typed QueryResult
    # (and Discourse query-result group), avoiding an O(result-size) copy of
    # the complete registry into every MeaningCandidate graph.
    grounded_graph = graph.with_grounded_entities((anchor.entity_id,))
    return ParseResult(
        frame=SemanticFrame(
            intent=command.intent,
            target=TargetReference(
                document.source_text,
                domain=domain,
                device_class=device_class,
            ),
            area=None,
            parameters={"query_command": command, "query_result": query_result},
            source_text=document.source_text,
            action=SemanticAction.QUERY,
            semantic_graph=grounded_graph,
        ),
        resolved_entities=matched,
    )
def project_independent_predicates(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> tuple[ParseResult, ...]:
    """Project a safe structural AND of complete direct predicates.

    Clause and action scope come exclusively from GermanStructuralAnalysis
    and its graph-backed semantic spans. Each clause must explicitly and
    uniquely name one live entity. Rich modifiers stay with their dedicated
    graph projectors so they cannot be flattened accidentally here.
    """
    if (
        not document.utterance.safe_to_execute_directly
        or document.temporal
        or any(
            relation.kind in {
                StructuralRelationKind.OR,
                StructuralRelationKind.EXCEPT,
                StructuralRelationKind.REPLACES,
            }
            for relation in document.structure.relations
        )
        or any(
            clause.kind in {
                ClauseKind.CONDITION,
                ClauseKind.EXCLUSION,
                ClauseKind.RELATIVE,
                ClauseKind.REPAIR,
            }
            for clause in document.structure.clauses
        )
    ):
        return ()
    relations = tuple(
        relation
        for relation in document.structure.relations
        if relation.kind is StructuralRelationKind.AND
    )
    if not relations:
        return ()
    clauses = {clause.clause_id: clause for clause in document.structure.clauses}
    ordered_ids: list[str] = []
    for relation in relations:
        for clause_id in (relation.source_clause, relation.target_clause):
            if clause_id not in ordered_ids:
                ordered_ids.append(clause_id)
    selected = tuple(clauses[item] for item in ordered_ids if item in clauses)
    if len(selected) != len(ordered_ids) or len(selected) < 2:
        return ()

    results: list[ParseResult] = []
    index = world_model.entity_index if world_model is not None else None
    for clause in selected:
        action_values = {
            span.value
            for span in document.semantics.matching(SemanticKind.ACTION)
            if clause.char_start <= span.start < clause.char_end
            and isinstance(span.value, str)
        }
        if len(action_values) != 1:
            return ()
        action = next(iter(action_values))
        semantic_action = _ACTION_ENUM.get(action)
        if semantic_action is None:
            return ()
        clause_text = document.source_text[clause.char_start:clause.char_end]
        mentioned = all_mentioned_entities(clause_text, entities, index=index)
        if len(mentioned) != 1:
            return ()
        entity = mentioned[0]
        intent = INTENT_BY_DOMAIN_ACTION.get((entity.domain, action))
        if intent is None:
            return ()
        results.append(
            ParseResult(
                frame=SemanticFrame(
                    intent=intent,
                    target=TargetReference(
                        entity.friendly_name,
                        entity.entity_id,
                        entity.domain,
                        entity.device_class,
                    ),
                    area=None,
                    source_text=clause_text,
                    action=semantic_action,
                    semantic_graph=graph.with_grounded_entities((entity.entity_id,)),
                ),
                resolved_entities=[entity],
            )
        )
    return tuple(results)


def project_structured_repair(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None = None,
) -> ParseResult | None:
    """Project an unambiguous target self-correction without text reparsing.

    The replacement clause must name exactly one live entity and the graph
    must contain exactly one executable action. The original referent is
    never included in ``resolved_entities``. Numeric and temporal repairs are
    owned by their typed projections; predicate changes that cannot preserve
    property and scope remain unsupported.
    """
    replacements = tuple(
        relation
        for relation in document.structure.relations
        if relation.kind is StructuralRelationKind.REPLACES
    )
    if len(replacements) != 1:
        return None
    clauses = {clause.clause_id: clause for clause in document.structure.clauses}
    replacement = clauses.get(replacements[0].target_clause)
    if replacement is None or replacement.kind is not ClauseKind.REPAIR:
        return None
    actions = {node.value for node in graph.nodes_of_kind(SemanticNodeKind.ACTION)}
    if len(actions) != 1:
        return None
    action = next(iter(actions))
    replacement_text = document.source_text[
        replacement.char_start:replacement.char_end
    ]
    mentioned = all_mentioned_entities(
        replacement_text,
        entities,
        index=(world_model.entity_index if world_model is not None else None),
    )
    if len(mentioned) != 1:
        return None
    entity = mentioned[0]
    intent = INTENT_BY_DOMAIN_ACTION.get((entity.domain, action))
    semantic_action = _ACTION_ENUM.get(action)
    if intent is None or semantic_action is None:
        return None
    # A value/property/temporal expression in the replacement changes more
    # than entity identity. Refuse it rather than silently retaining a value
    # from the original clause.
    if any(
        node.start is not None
        and replacement.char_start <= node.start < replacement.char_end
        for kind in {
            SemanticNodeKind.VALUE,
            SemanticNodeKind.PROPERTY,
            SemanticNodeKind.TEMPORAL,
            SemanticNodeKind.COMPARISON,
        }
        for node in graph.nodes_of_kind(kind)
    ):
        return None
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name,
                entity.entity_id,
                entity.domain,
                entity.device_class,
            ),
            area=None,
            parameters={
                "reasoning_trace": ReasoningTrace((ReasoningStep(
                    "repair_replacement",
                    (),
                    (f"entity:{entity.entity_id}",),
                    detail="target",
                ),)),
            },
            source_text=document.source_text,
            action=semantic_action,
            semantic_graph=graph.with_grounded_entities((entity.entity_id,)),
        ),
        resolved_entities=[entity],
    )


def project_value_repair(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None = None,
) -> ParseResult | None:
    """Replace one typed property value; never retain the superseded value."""
    replacements = tuple(
        relation for relation in document.structure.relations
        if relation.kind is StructuralRelationKind.REPLACES
    )
    if len(replacements) != 1:
        return None
    clauses = {clause.clause_id: clause for clause in document.structure.clauses}
    original = clauses.get(replacements[0].source_clause)
    replacement = clauses.get(replacements[0].target_clause)
    if original is None or replacement is None:
        return None
    values = graph.nodes_of_kind(SemanticNodeKind.VALUE)
    original_values = tuple(
        node for node in values
        if node.start is not None and original.char_start <= node.start < original.char_end
    )
    replacement_values = tuple(
        node for node in values
        if node.start is not None and replacement.char_start <= node.start < replacement.char_end
    )
    if len(original_values) != 1 or len(replacement_values) != 1:
        return None
    try:
        replacement_value = float(replacement_values[0].value.replace(",", "."))
    except ValueError:
        return None
    domains = {
        str(value) for value in document.semantics.values(SemanticKind.DOMAIN)
    }
    properties = {
        str(value) for value in document.semantics.values(SemanticKind.PROPERTY)
    }
    source = normalize_for_compare(document.source_text)
    original_text = document.source_text[original.char_start:original.char_end]
    original_mentions = all_mentioned_entities(
        original_text,
        entities,
        index=(world_model.entity_index if world_model is not None else None),
    )
    if not domains and len(original_mentions) == 1:
        domains = {original_mentions[0].domain}
    if len(domains) != 1:
        return None
    domain = next(iter(domains))
    if "prozent" in source or "%" in document.source_text:
        property_name = (
            "position" if domain == "cover" else "brightness" if domain == "light" else None
        )
        unit = NumericUnit.PERCENT
        parameter = "percent"
        if not 0 <= replacement_value <= 100:
            return None
        parameter_value: float | int = int(replacement_value)
    elif "grad" in source and domain == "climate":
        property_name = "temperature"
        unit = NumericUnit.CELSIUS
        parameter = "temperature"
        parameter_value = replacement_value
    else:
        return None
    if property_name is None or (properties and properties != {property_name}):
        # A changed/incomplete property cannot inherit the former unit.
        return None
    intent = VALUE_INTENT_BY_DOMAIN_PROPERTY.get((domain, property_name))
    property_ = _PROPERTY_ENUM.get(property_name)
    candidates = (
        original_mentions
        if len(original_mentions) == 1 and original_mentions[0].domain == domain
        else tuple(
            world_model.select_entities(domain=domain)
            if world_model is not None
            else (entity for entity in entities if entity.domain == domain)
        )
    )
    if intent is None or property_ is None or len(candidates) != 1:
        return None
    entity = candidates[0]
    numeric = NumericValue(replacement_value, unit)
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain, entity.device_class
            ),
            area=None,
            parameters={
                parameter: parameter_value,
                "repair_replacement": property_name,
                "reasoning_trace": ReasoningTrace((ReasoningStep(
                    "repair_replacement",
                    (f"value:{original_values[0].value}",),
                    (f"value:{replacement_values[0].value}",),
                    detail=property_name,
                ),)),
            },
            source_text=document.source_text,
            action=SemanticAction.SET,
            property=property_,
            numeric_value=numeric,
            semantic_graph=graph.with_grounded_entities((entity.entity_id,)),
        ),
        resolved_entities=[entity],
    )


def _active_spans(
    document: LanguageDocument, kind: SemanticKind
) -> tuple[SemanticSpan, ...]:
    return tuple(
        span
        for span in document.semantics.matching(kind)
        if (
            (clause := document.structure.clause_for_char(span.start)) is None
            or clause.kind in {ClauseKind.MAIN, ClauseKind.COORDINATE}
        )
    )


def _locations(
    document: LanguageDocument,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> tuple[tuple[str, str | None, str | None], ...]:
    # An area named inside an exclusion belongs to that excluded target, not
    # to the main target set. Ground the main source span only; this is an
    # unchanged substring selected by structure, never a generated sentence.
    exclusions = tuple(
        clause
        for clause in document.structure.clauses
        if clause.kind is ClauseKind.EXCLUSION
    )
    location_text = document.source_text
    if exclusions:
        first_exclusion = min(clause.char_start for clause in exclusions)
        preceding = tuple(
            clause
            for clause in document.structure.clauses
            if clause.kind is ClauseKind.MAIN and clause.char_end <= first_exclusion
        )
        if preceding:
            location_text = document.source_text[
                preceding[0].char_start:max(clause.char_end for clause in preceding)
            ]
    coordinated = resolve_coordinated_locations(
        location_text, entities, world_model
    )
    if coordinated:
        return coordinated
    single = resolve_semantic_location(location_text, entities, world_model)
    return (single,) if single is not None else ()


def _scope_candidates(
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
    *,
    domain: str,
    device_class: str | None,
    locations: tuple[tuple[str, str | None, str | None], ...],
) -> tuple[EntitySnapshot, ...]:
    resolved: dict[str, EntitySnapshot] = {}
    scopes = locations or (("", None, None),)
    for _, area_id, floor_id in scopes:
        candidates = (
            world_model.select_entities(
                domain=domain,
                device_class=device_class,
                area_id=area_id,
                floor_id=floor_id,
            )
            if world_model is not None
            else tuple(
                resolve_candidates(
                    entities,
                    Constraints(
                        domain=domain,
                        device_class=device_class,
                        area_id=area_id,
                        floor_id=floor_id,
                    ),
                )
            )
        )
        resolved.update((entity.entity_id, entity) for entity in candidates)
    return tuple(resolved[key] for key in sorted(resolved))


def _exclusion_phrases(document: LanguageDocument) -> tuple[str, ...]:
    # "außer der Stehlampe und dem Nachtlicht" / "außer X, Y aus": the
    # structure analysis splits such lists into loose coordinate or main
    # clauses, which dropped every exclusion after the first one. The shared
    # text-level split names each exclusion separately (also used by the
    # semantic command compiler), so none of them can silently vanish.
    if has_exclusion_clause(document.source_text):
        _positive, targets = split_exclusion(normalize(document.source_text))
        if targets:
            return targets
    phrases: list[str] = []
    action_spans = document.semantics.matching(SemanticKind.ACTION)
    for clause in document.structure.clauses:
        if clause.kind is not ClauseKind.EXCLUSION:
            continue
        words = tuple(
            token.text
            for token in document.tokens[clause.token_start:clause.token_end]
            if token.is_word
            and not any(
                span.start <= token.start and token.end <= span.end
                for span in action_spans
            )
            and token.canonical
            not in {
                "aber", "ausser", "der", "die", "das", "den", "dem",
                "mit", "ausnahme", "von", "nur", "nicht",
            }
        )
        if words:
            phrases.append(" ".join(words))
    return tuple(phrases)


def _resolve_exclusions(
    phrases: tuple[str, ...],
    candidates: tuple[EntitySnapshot, ...],
    world_model: WorldModel | None,
) -> tuple[tuple[EntitySnapshot, ...], tuple[EntitySnapshot, ...]] | None:
    excluded: dict[str, EntitySnapshot] = {}
    for phrase in phrases:
        resolution = resolve_entity_scored(
            phrase,
            list(candidates),
            index=(world_model.entity_index if world_model is not None else None),
        )
        if (
            resolution.status is not ResolutionStatus.RESOLVED
            or resolution.entity is None
        ):
            return None
        excluded[resolution.entity.entity_id] = resolution.entity
    remaining = tuple(
        entity for entity in candidates if entity.entity_id not in excluded
    )
    if not remaining:
        return None
    return remaining, tuple(excluded[key] for key in sorted(excluded))


def _relative_filter(document: LanguageDocument) -> SemanticState | None:
    relative_clauses = tuple(
        clause
        for clause in document.structure.clauses
        if clause.kind is ClauseKind.RELATIVE
        and any(
            relation.kind is StructuralRelationKind.MODIFIES
            and relation.target_clause == clause.clause_id
            for relation in document.structure.relations
        )
    )
    if len(relative_clauses) != 1:
        return None
    clause = relative_clauses[0]
    states = {
        span.value
        for span in document.semantics.matching(SemanticKind.STATE)
        if clause.char_start <= span.start < clause.char_end
        and isinstance(span.value, SemanticState)
    }
    return next(iter(states)) if len(states) == 1 else None


def _has_definite_plural_target(document: LanguageDocument) -> bool:
    """Recognise a conservative ``die <plural entity class>`` noun phrase."""
    target_spans = (
        *_active_spans(document, SemanticKind.DOMAIN),
        *_active_spans(document, SemanticKind.DEVICE_CLASS),
    )
    for span in target_spans:
        surface = normalize_for_compare(span.text)
        if not surface.endswith(("en", "er")):
            continue
        previous = next(
            (
                token
                for token in reversed(document.tokens)
                if token.end <= span.start and token.is_word
            ),
            None,
        )
        if previous is not None and previous.canonical == "die":
            return True
    return False


def _grounded_vocabulary(
    entities: Iterable[EntitySnapshot],
    locations: tuple[tuple[str, str | None, str | None], ...],
    exclusions: tuple[str, ...],
) -> frozenset[str]:
    texts = [
        name
        for entity in entities
        for name in (
            entity.friendly_name,
            *entity.aliases,
            entity.area_name or "",
            *entity.area_aliases,
            entity.floor_name or "",
        )
    ]
    texts.extend(location[0] for location in locations)
    texts.extend(exclusions)
    return frozenset(
        normalize_for_compare(word)
        for value in texts
        for word in value.split()
        if word
    )


def project_structured_command(
    document: LanguageDocument,
    graph: SemanticGraph,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None = None,
    *,
    composition: CompositionalPlan | None = None,
) -> ParseResult | None:
    """Project one supported command graph shape without reparsing text.

    Supported shapes contain one predicate, explicit named targets or one
    quantified entity class, optional proven locations, one relative state
    filter and explicit exclusions. Any unsupported or ambiguous operand
    rejects the complete projection.
    """
    actions = {node.value for node in graph.nodes_of_kind(SemanticNodeKind.ACTION)}
    if len(actions) != 1:
        return None
    action = next(iter(actions))

    targets = composition.targets if composition is not None else ()
    graph_nodes = {node.node_id: node for node in graph.nodes}
    domain_values = {
        target.value
        for edge in graph.edges
        if edge.kind is SemanticEdgeKind.TARGET
        if (target := graph_nodes.get(edge.target)) is not None
        if target.kind is SemanticNodeKind.ENTITY_CLASS
        if (target.value, action) in INTENT_BY_DOMAIN_ACTION
    }
    if not domain_values:
        domain_values = {
            span.value
            for span in _active_spans(document, SemanticKind.DOMAIN)
            if isinstance(span.value, str)
        }
    device_values = {
        (span.value[0], span.value[1])
        for span in _active_spans(document, SemanticKind.DEVICE_CLASS)
        if isinstance(span.value, tuple)
        and len(span.value) == 2
        and isinstance(span.value[0], str)
        and (span.value[1] is None or isinstance(span.value[1], str))
    }
    if targets:
        domains = {entity.domain for entity in targets}
        if domain_values and not domains <= domain_values:
            return None
        device_class = None
    elif len(device_values) == 1:
        domain, device_class = next(iter(device_values))
        domains = {domain}
    elif len(domain_values) == 1:
        domains = set(domain_values)
        device_class = None
    else:
        return None

    mappings = {
        domain: INTENT_BY_DOMAIN_ACTION.get((domain, action)) for domain in domains
    }
    if any(intent is None for intent in mappings.values()):
        return None
    intents = {intent for intent in mappings.values() if intent is not None}
    if len(intents) != 1:
        return None
    intent = next(iter(intents))

    quantifier_values = {
        span.value
        for span in _active_spans(document, SemanticKind.QUANTIFIER)
        if isinstance(span.value, str)
    }
    if len(quantifier_values) > 1:
        return None
    raw_quantifier = next(iter(quantifier_values), None)
    quantifier = (
        Quantifier("both")
        if raw_quantifier == "both"
        else Quantifier("all")
        if raw_quantifier == "all"
        else None
    )
    if quantifier is None and _has_definite_plural_target(document):
        quantifier = Quantifier("all")
    locations = _locations(document, entities, world_model)
    if not targets:
        if quantifier is None or len(domains) != 1:
            return None
        targets = _scope_candidates(
            entities,
            world_model,
            domain=next(iter(domains)),
            device_class=device_class,
            locations=locations,
        )
        if not targets or (quantifier.kind == "both" and len(targets) != 2):
            return None
    elif locations and any(
        not any(
            (area_id is None or entity.area_id == area_id)
            and (floor_id is None or entity.floor_id == floor_id)
            for _, area_id, floor_id in locations
        )
        for entity in targets
    ):
        return None

    relative_clauses = tuple(
        clause
        for clause in document.structure.clauses
        if clause.kind is ClauseKind.RELATIVE
    )
    state_filter = _relative_filter(document)
    if relative_clauses and state_filter is None:
        return None
    if state_filter is not None:
        targets = tuple(
            entity
            for entity in targets
            if matches_semantic_state(entity, state_filter)
        )
        if not targets:
            return None

    exclusion_phrases = _exclusion_phrases(document)
    excluded: tuple[EntitySnapshot, ...] = ()
    if exclusion_phrases:
        resolved = _resolve_exclusions(exclusion_phrases, targets, world_model)
        if resolved is None:
            return None
        targets, excluded = resolved

    vocabulary = _grounded_vocabulary(
        (*targets, *excluded), locations, exclusion_phrases
    )
    structural_vocabulary = {
        normalize_for_compare(token.text)
        for clause in document.structure.clauses
        if clause.kind in {ClauseKind.RELATIVE, ClauseKind.EXCLUSION}
        for token in document.tokens[clause.token_start:clause.token_end]
        if token.is_word
        and token.canonical in {"aber", "ausser", "nur"}
    }
    structural_vocabulary.update(
        normalize_for_compare(clause.connector)
        for clause in document.structure.clauses
        if clause.kind in {ClauseKind.RELATIVE, ClauseKind.EXCLUSION}
        and clause.connector is not None
    )
    if composition is not None:
        structural_vocabulary.update(
            token.canonical
            for token in document.tokens
            if token.canonical in {"und", "sowie", "ausserdem"}
        )
    if any(
        normalize_for_compare(token) not in vocabulary | structural_vocabulary
        for token in document.semantics.unexplained_tokens
    ):
        return None

    frame_targets = targets[:1] if composition is not None else targets
    first = frame_targets[0]
    area = (
        AreaReference(locations[0][0], locations[0][1])
        if len(locations) == 1 and locations[0][1] is not None
        else None
    )
    semantic_quantity = (
        SemanticQuantity.exclude(*(entity.friendly_name for entity in excluded))
        if excluded
        else SemanticQuantity.exactly(2)
        if quantifier is not None and quantifier.kind == "both"
        else SemanticQuantity.all()
        if quantifier is not None
        else None
    )
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=(
                TargetReference(
                    first.friendly_name, first.entity_id, first.domain
                )
                if composition is not None
                else TargetReference(
                    next(iter(domains)), domain=next(iter(domains))
                )
            ),
            area=area,
            quantifier=quantifier,
            parameters={
                **(
                    {"state_filter": state_filter.name.lower()}
                    if state_filter is not None
                    else {}
                ),
                **(
                    {
                        "excluded": tuple(
                            item.friendly_name for item in excluded
                        ),
                        "excluded_entity_ids": tuple(
                            item.entity_id for item in excluded
                        ),
                    }
                    if excluded
                    else {}
                ),
                **(
                    {
                        "locations": tuple(
                            {
                                "text": text,
                                "area_id": area_id,
                                "floor_id": floor_id,
                            }
                            for text, area_id, floor_id in locations
                        )
                    }
                    if len(locations) > 1
                    else {}
                ),
            },
            source_text=document.source_text,
            action=_ACTION_ENUM.get(action),
            quantity=semantic_quantity,
        ),
        resolved_entities=list(frame_targets),
    )
