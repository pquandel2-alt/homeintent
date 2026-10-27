"""Unified semantic candidate interpreter for HomeIntent V8.

The interpreter consumes one loss-aware ``LanguageDocument`` and produces
ranked meaning candidates before any domain executor runs.  Existing safe
semantic compilers are reused as the authoritative bridge while commands,
queries and automations migrate to the common candidate model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from ..entities import EntitySnapshot, normalize_for_compare
from ..world_model import WorldModel
from .language_frontend import LanguageDocument, TextVariant, tokenize_language
from .german_structure import (
    ClauseKind,
    StructuralRelationKind,
    analyse_german_structure,
)
from .parser import ClarificationRequest, ParseResult
from .semantic_compiler import SemanticCommandCompiler, SemanticQueryCompiler
from .semantic_catalog import INTENT_BY_DOMAIN_ACTION
from .semantic_lexicon import SemanticKind
from .semantic_lexicon import analyse_semantics
from .semantic_location import resolve_coordinated_locations, resolve_semantic_location
from .semantic_utterance import SpeechAct, is_contextual_followup
from .understanding import (
    EvidenceKind,
    EvidencePolarity,
    MeaningCandidate,
    UnderstandingEvidence,
)
from .entity_resolution import all_mentioned_entities
from .frame import SemanticFrame, TargetReference
from .command import build_semantic_command
from .evidence import (
    CAPABILITY_CONTRADICTION_PENALTY,
    CONFLICT_PENALTY,
    UNEXPLAINED_TOKEN_PENALTY,
    evidence_score,
)
from .primitives import SemanticAction
from .query_command import QueryCommand
from .verb_state_query import match_verb_state_query
from .composition import CompositionalPlan, build_document_compositional_plan
from .registered_operation_compiler import compile_registered_operation
from .semantic_graph import build_semantic_graph
from .semantic_projection import (
    attach_graph,
    project_relational_comparison_query,
    project_relational_command,
    project_relationship_query,
    project_semantic_reasoning_query,
    project_property_repair_query,
    project_structured_repair,
    project_value_repair,
    project_structured_command,
)
from .temporal_semantics import analyse_temporal_semantics
from .validator import ValidationError, validate_command
from .understanding_context import UnderstandingContext
from .script_semantics import compile_script_request


@dataclass(frozen=True)
class InterpreterResult:
    candidates: tuple[MeaningCandidate, ...]
    parse_result: ParseResult | ClarificationRequest | None = None
    selected_variant: TextVariant | None = None
    compositional_plan: CompositionalPlan | None = None
    semantic_ambiguity: bool = False
    selected_candidate_index: int | None = None


SEMANTIC_AMBIGUITY_MARGIN = 5.0


def _world_model_boundary(value: object) -> WorldModel | None:
    """Narrow the optional cross-module model at the interpreter boundary."""
    if value is None:
        return None
    if isinstance(value, WorldModel):
        return value
    raise TypeError("world_model must be a WorldModel or None")


def _candidate_identity(
    candidate: MeaningCandidate,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (
        candidate.key,
        tuple(sorted((name, repr(value)) for name, value in candidate.slots.items())),
    )


def _slot_values(document: LanguageDocument) -> dict[str, object]:
    analysis = document.semantics
    slots: dict[str, object] = {
        "speech_act": document.utterance.speech_act.name.lower(),
        "modality": document.utterance.modality.name.lower(),
        "polarity": document.utterance.polarity.name.lower(),
        "clauses": tuple(
            {
                "role": clause.role.name.lower(),
                "text": clause.text,
                "connector": clause.connector,
            }
            for clause in document.utterance.clauses
        ),
    }
    for label, kind in (
        ("actions", SemanticKind.ACTION),
        ("domains", SemanticKind.DOMAIN),
        ("device_classes", SemanticKind.DEVICE_CLASS),
        ("states", SemanticKind.STATE),
        ("quantifiers", SemanticKind.QUANTIFIER),
        ("query_scopes", SemanticKind.QUERY_SCOPE),
        ("properties", SemanticKind.PROPERTY),
        ("comparators", SemanticKind.COMPARATOR),
        ("relations", SemanticKind.RELATION),
    ):
        values = analysis.values(kind)
        if values:
            slots[label] = tuple(sorted((str(value) for value in values)))
    return slots


def _conflicts(document: LanguageDocument) -> tuple[str, ...]:
    conflicts: list[str] = []
    for kind in (
        SemanticKind.ACTION,
        SemanticKind.DOMAIN,
        SemanticKind.STATE,
        SemanticKind.PROPERTY,
        SemanticKind.COMPARATOR,
    ):
        if not document.semantics.conflicts(kind):
            continue
        if (
            document.utterance.speech_act is SpeechAct.COMMAND
            and kind in {SemanticKind.ACTION, SemanticKind.STATE}
        ):
            # State/action homonyms in relative filters and exclusions do not
            # compete with the requested predicate. Coordinated main clauses
            # remain in scope and therefore still expose true conflicts.
            actionable_values = {
                span.value
                for span in document.semantics.matching(kind)
                if (
                    (clause := document.structure.clause_for_char(span.start)) is None
                    or clause.kind
                    not in {
                        ClauseKind.RELATIVE,
                        ClauseKind.EXCLUSION,
                        ClauseKind.CONDITION,
                        ClauseKind.TEMPORAL,
                    }
                )
            }
            if len(actionable_values) <= 1:
                continue
        if document.semantics.conflicts(kind):
            conflicts.append(kind.name.lower())
    return tuple(conflicts)


def _resolved_conflicts(
    document: LanguageDocument,
    parse_result: ParseResult | ClarificationRequest | None,
) -> tuple[str, ...]:
    """Return only conflicts that remain after domain-aware compilation.

    German particles and participles are deliberately polysemous: ``zu`` in
    ``zu starten`` is not a close action, and ``Wiedergabe gestartet`` may
    expose both ``play`` and ``start`` although both compile to the same media
    service.  Once the compiler has produced one validated intent for one
    domain, discard an action conflict only when every domain-valid action
    collapses to that exact intent.  Truly competing meanings such as
    ``Licht an und aus`` remain conflicts.
    """
    conflicts = list(_conflicts(document))
    if (
        "property" in conflicts
        and isinstance(parse_result, ParseResult)
        and parse_result.frame.parameters.get("repair_replacement") is not None
    ):
        # The structured REPLACES edge and property projector have selected
        # one final compatible slot; the superseded property remains in the
        # graph for audit but is no longer an active semantic conflict.
        conflicts.remove("property")
    if (
        "domain" in conflicts
        and isinstance(parse_result, ParseResult)
        and parse_result.frame.target is not None
        and parse_result.frame.target.entity_id is not None
        and len(parse_result.resolved_entities) == 1
    ):
        # An exact registry identity is typed data. Generic nouns occurring
        # inside that user-controlled name cannot introduce a second domain.
        conflicts.remove("domain")
    if (
        "domain" in conflicts
        and isinstance(parse_result, ParseResult)
        and parse_result.frame.intent == "HassRelationshipQuery"
    ):
        # A relation query has one target class before the relation marker
        # and a separately grounded anchor after it. Their domains describe
        # different graph roles and therefore are not competing meanings.
        conflicts.remove("domain")
    if (
        "state" in conflicts
        and isinstance(parse_result, ParseResult)
        and document.utterance.speech_act is SpeechAct.QUERY
        and (
            "exists" in document.semantics.values(SemanticKind.QUERY_SCOPE)
            or parse_result.frame.intent == "HassExistsQuery"
        )
    ):
        substantive_states = {
            span.value
            for span in document.semantics.matching(SemanticKind.STATE)
            if span.text.casefold() not in {"ein", "eine"}
        }
        if len(substantive_states) <= 1:
            conflicts.remove("state")
    if (
        "action" in conflicts
        and isinstance(parse_result, ParseResult)
        and document.utterance.speech_act is SpeechAct.QUERY
        and parse_result.frame.action is SemanticAction.QUERY
    ):
        # State adjectives share lexemes with executable actions in German
        # (``Fenster offen``, ``Licht eingeschaltet``). A successfully typed
        # query is read-only and therefore resolves this lexical action
        # conflict without weakening command validation.
        conflicts.remove("action")
    if (
        "state" in conflicts
        and isinstance(parse_result, ParseResult)
        and parse_result.frame.action is SemanticAction.QUERY
        and isinstance(parse_result.frame.parameters.get("query_command"), QueryCommand)
        and parse_result.frame.parameters["query_command"].algebra is not None
    ):
        # Nested relational predicates legitimately carry different states;
        # the typed algebra preserves their scopes.
        conflicts.remove("state")
    if (
        isinstance(parse_result, ParseResult)
        and parse_result.frame.action not in {None, SemanticAction.QUERY}
        and isinstance(parse_result.frame.parameters.get("selection_query"), QueryCommand)
        and parse_result.frame.parameters["selection_query"].algebra is not None
    ):
        # The relational command projector has separated its executable
        # predicate from nested read-only state predicates and re-grounded
        # every target. Surface homonyms in the nested clause no longer
        # compete with the selected action.
        for scoped_conflict in ("action", "state"):
            if scoped_conflict in conflicts:
                conflicts.remove(scoped_conflict)
    if (
        "state" in conflicts
        and "action" not in conflicts
        and isinstance(parse_result, ParseResult)
        and document.utterance.speech_act is SpeechAct.COMMAND
        and parse_result.frame.action not in {None, SemanticAction.QUERY}
    ):
        conflicts.remove("state")
    if "action" not in conflicts or not isinstance(parse_result, ParseResult):
        return tuple(conflicts)
    domains = {
        entity.domain for entity in parse_result.resolved_entities
    }
    if not domains and parse_result.frame.target is not None:
        if parse_result.frame.target.domain is not None:
            domains.add(parse_result.frame.target.domain)
    intents = {
        intent
        for domain in domains
        for action in document.semantics.values(SemanticKind.ACTION)
        if (intent := INTENT_BY_DOMAIN_ACTION.get((domain, str(action)))) is not None
    }
    if intents == {parse_result.frame.intent}:
        conflicts.remove("action")
    if (
        "state" in conflicts
        and "action" not in conflicts
        and document.utterance.speech_act is SpeechAct.COMMAND
        and parse_result.frame.action not in {None, SemanticAction.QUERY}
    ):
        # The command compiler has already collapsed separable particles,
        # floor words and registry-name tokens into one validated intent.
        conflicts.remove("state")
    return tuple(conflicts)


def _missing_slots(
    document: LanguageDocument,
    slots: dict[str, object],
    parse_result: ParseResult | ClarificationRequest | None = None,
) -> tuple[str, ...]:
    analysis = document.semantics
    missing: list[str] = []
    if document.utterance.speech_act is SpeechAct.COMMAND:
        compiled_action = (
            parse_result.frame.action
            if isinstance(parse_result, ParseResult)
            else None
        )
        if not analysis.values(SemanticKind.ACTION) and compiled_action is None:
            missing.append("action")
        if not (
            analysis.values(SemanticKind.DOMAIN)
            or analysis.values(SemanticKind.DEVICE_CLASS)
            or slots.get("entities")
        ):
            missing.append("target")
    elif document.utterance.speech_act is SpeechAct.QUERY:
        if not (
            analysis.values(SemanticKind.DOMAIN)
            or analysis.values(SemanticKind.DEVICE_CLASS)
            or analysis.values(SemanticKind.PROPERTY)
            or slots.get("entities")
        ):
            missing.append("target_or_property")
    return tuple(missing)


class SemanticInterpreter:
    """Compose one language document into candidates and a safe parse."""

    @staticmethod
    def interpret(
        document: LanguageDocument,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        *,
        compile_result: bool = True,
        resolve_registry: bool = True,
        context: UnderstandingContext | None = None,
    ) -> InterpreterResult:
        # Pin the cross-module TYPE_CHECKING cycle to one explicit runtime
        # boundary; downstream calls retain precise WorldModel contracts.
        typed_world_model = _world_model_boundary(world_model)
        candidates: list[MeaningCandidate] = []
        parse_records: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            tuple[
                float,
                ParseResult | ClarificationRequest,
                TextVariant,
                CompositionalPlan | None,
            ],
        ] = {}
        candidate_indexes: dict[
            tuple[str, tuple[tuple[str, str], ...]], int
        ] = {}
        for variant in document.variants:
            analysis = (
                document.semantics
                if variant.source == "original"
                else analyse_semantics(variant.text)
            )
            variant_tokens = (
                document.tokens
                if variant.source == "original"
                else tokenize_language(variant.text)
            )
            variant_structure = (
                document.structure
                if variant.source == "original"
                else analyse_german_structure(variant_tokens)
            )
            candidate_document = replace(
                document,
                source_text=variant.text,
                tokens=variant_tokens,
                semantics=analysis,
                structure=variant_structure,
                temporal=analyse_temporal_semantics(variant_tokens),
            )
            graph = build_semantic_graph(
                variant.text,
                variant_tokens,
                variant_structure,
                analysis,
                document.utterance.speech_act,
            )
            slots = _slot_values(candidate_document)
            mentions = (
                all_mentioned_entities(variant.text, entities)
                if resolve_registry
                else ()
            )
            if mentions:
                slots["entities"] = tuple(entity.entity_id for entity in mentions)
                if "domains" not in slots:
                    slots["domains"] = tuple(sorted({entity.domain for entity in mentions}))
            locations = (
                resolve_coordinated_locations(variant.text, entities, typed_world_model)
                if resolve_registry
                else None
            )
            if locations:
                slots["locations"] = tuple(item[0] for item in locations)
                grounded_locations = locations
            else:
                location = (
                    resolve_semantic_location(variant.text, entities, typed_world_model)
                    if resolve_registry
                    else None
                )
                if location is not None:
                    slots["locations"] = (location[0],)
                    grounded_locations = (location,)
                else:
                    grounded_locations = ()

            evidence = tuple(
                UnderstandingEvidence(
                    EvidenceKind.LEXICON,
                    f"{span.kind.name.lower()}={span.value}",
                    span.start,
                    span.end,
                    0.0,
                    span.text,
                    claim=f"{span.kind.name.lower()}={span.value}",
                    source_id="semantic_catalog",
                )
                for span in analysis.spans
            ) + tuple(
                UnderstandingEvidence(
                    EvidenceKind.STRUCTURE,
                    relation.kind.name.lower(),
                    relation.connector_start,
                    relation.connector_end,
                    0.0,
                    f"{relation.source_clause}->{relation.target_clause}",
                    claim=relation.kind.name.lower(),
                    source_id="german_structure",
                )
                for relation in variant_structure.relations
            ) + tuple(
                UnderstandingEvidence(
                    EvidenceKind.REGISTRY,
                    entity.entity_id,
                    score=0.0,
                    detail=entity.friendly_name,
                    claim=f"entity={entity.entity_id}",
                    source_id="entity_resolution",
                )
                for entity in mentions
            )
            if variant.source != "original":
                evidence += (
                    UnderstandingEvidence(
                        EvidenceKind.PHONETIC
                        if variant.source.startswith("phonetic:")
                        else EvidenceKind.CORRECTION,
                        variant.text,
                        score=0.0,
                        detail=variant.source,
                        polarity=EvidencePolarity.NEGATIVE,
                        claim=f"surface={variant.text}",
                        source_id=variant.source,
                    ),
                )

            parse_result: ParseResult | ClarificationRequest | None = None
            compositional_plan: CompositionalPlan | None = None
            # Bounded phonetic/edit candidates are explanatory only and must
            # pass the existing confirm-before-action correction flow.
            may_compile = (
                compile_result
                and not variant.source.startswith("phonetic:")
                and variant.source != "orthographic"
            )
            if may_compile and document.utterance.speech_act is SpeechAct.QUERY:
                parse_result = project_property_repair_query(
                    candidate_document, graph, entities, typed_world_model
                ) or project_relational_comparison_query(
                    candidate_document, graph, entities, typed_world_model
                ) or project_relationship_query(
                    candidate_document, graph, entities, typed_world_model
                ) or project_semantic_reasoning_query(
                    candidate_document, graph, entities, typed_world_model
                )
                has_standalone_query_meaning = any(
                    analysis.values(kind)
                    for kind in (
                        SemanticKind.DOMAIN,
                        SemanticKind.DEVICE_CLASS,
                        SemanticKind.PROPERTY,
                        SemanticKind.STATE,
                    )
                )
                semantic_query_clauses = {
                    clause.clause_id
                    for span in analysis.spans
                    if span.kind in {
                        SemanticKind.DOMAIN,
                        SemanticKind.DEVICE_CLASS,
                        SemanticKind.PROPERTY,
                        SemanticKind.STATE,
                    }
                    if (
                        clause := variant_structure.clause_for_char(span.start)
                    ) is not None
                }
                unsupported_unbound_query = (
                    len(semantic_query_clauses) > 1
                    and not variant_structure.relations
                )
                if parse_result is not None:
                    verb_answer = None
                elif (
                    is_contextual_followup(variant.text)
                    and not has_standalone_query_meaning
                ):
                    # Context-free interpretation cannot ground an elliptical
                    # follow-up. ConversationContext owns that resolution;
                    # scanning the complete registry here adds no evidence.
                    verb_answer = None
                    parse_result = None
                elif unsupported_unbound_query:
                    # Several meaning-bearing clauses without a structural
                    # relation cannot safely be collapsed into one flat
                    # QueryCommand. Return UNSUPPORTED before an exhaustive
                    # registry scan; a future relational projector may own
                    # this graph shape.
                    verb_answer = None
                    parse_result = None
                else:
                    verb_answer = match_verb_state_query(variant.text, entities)
                if parse_result is not None:
                    pass
                elif verb_answer is not None:
                    domains = {entity.domain for entity in verb_answer.entities}
                    parse_result = ParseResult(
                        frame=SemanticFrame(
                            intent="HassEntityStateQuery",
                            target=TargetReference(
                                text=variant.text,
                                domain=(next(iter(domains)) if len(domains) == 1 else None),
                            ),
                            area=None,
                            parameters={"predicate": verb_answer.predicate},
                            source_text=variant.text,
                            action=SemanticAction.QUERY,
                        ),
                        resolved_entities=list(verb_answer.entities),
                        response_text=verb_answer.response_text,
                        context_predicate=verb_answer.predicate,
                        explanation_text=verb_answer.explanation_text,
                    )
                elif not (
                    unsupported_unbound_query
                    or (
                        is_contextual_followup(variant.text)
                        and not has_standalone_query_meaning
                    )
                ):
                    parse_result = SemanticQueryCompiler.compile(
                        variant.text, entities, typed_world_model, analysis
                    )
            elif may_compile and document.utterance.safe_to_execute_directly:
                context_only_command = (
                    is_contextual_followup(variant.text)
                    and any(
                        token.canonical in {"dort", "davon"}
                        for token in candidate_document.tokens
                    )
                    and not any(
                        analysis.values(kind)
                        for kind in (
                            SemanticKind.DOMAIN,
                            SemanticKind.DEVICE_CLASS,
                        )
                    )
                    and not mentions
                )
                if context_only_command:
                    # This graph requires DiscourseState grounding. Running
                    # fuzzy entity resolution against the complete registry
                    # would be both expensive and semantically unauthorised.
                    continue
                has_repair = any(
                    relation.kind is StructuralRelationKind.REPLACES
                    for relation in candidate_document.structure.relations
                )
                parse_result = (
                    project_value_repair(
                        candidate_document, graph, entities, typed_world_model
                    ) or project_structured_repair(
                        candidate_document, graph, entities, typed_world_model
                    )
                    if has_repair
                    else None
                )
                if parse_result is None and not has_repair:
                    parse_result = project_relational_command(
                        candidate_document, graph, entities, typed_world_model
                    )
                compositional_plan = (
                    None
                    if has_repair
                    else build_document_compositional_plan(
                        candidate_document,
                        entities,
                        index=(
                            typed_world_model.entity_index
                            if typed_world_model is not None
                            else None
                        ),
                        resolved_mentions=(mentions if resolve_registry else None),
                    )
                )
                has_exclusion = any(
                    clause.kind is ClauseKind.EXCLUSION
                    for clause in candidate_document.structure.clauses
                )
                # A comma or hesitation can create a syntactic RELATIVE
                # candidate. It becomes authoritative only when it actually
                # modifies an independently recognised entity class. This
                # prevents punctuation artefacts from suppressing the normal
                # compiler while still refusing unknown genuine relatives.
                has_relative_filter = any(
                    clause.kind is ClauseKind.RELATIVE
                    and any(
                        clause.char_start <= span.start < clause.char_end
                        for span in candidate_document.semantics.matching(
                            SemanticKind.STATE
                        )
                    )
                    for clause in candidate_document.structure.clauses
                ) and any(
                    span.kind in {SemanticKind.DOMAIN, SemanticKind.DEVICE_CLASS}
                    and (
                        (clause := candidate_document.structure.clause_for_char(
                            span.start
                        )) is None
                        or clause.kind in {ClauseKind.MAIN, ClauseKind.COORDINATE}
                    )
                    for span in candidate_document.semantics.spans
                )
                has_structured_modifier = (
                    has_exclusion or has_relative_filter or has_repair
                )
                parse_result = parse_result or (
                    project_structured_command(
                        candidate_document,
                        graph,
                        entities,
                        typed_world_model,
                        composition=compositional_plan,
                    )
                    if not has_repair
                    and (compositional_plan is not None or has_structured_modifier)
                    else None
                )
                if parse_result is None and compositional_plan is None and not has_structured_modifier:
                    parse_result = compile_script_request(
                        variant.text,
                        entities,
                        custom_verbs=(
                            context.script_verbs if context is not None else ()
                        ),
                    )
                if parse_result is None and compositional_plan is None and not has_structured_modifier:
                    parse_result = compile_registered_operation(
                        variant.text,
                        entities,
                        index=(
                            typed_world_model.entity_index
                            if typed_world_model is not None
                            else None
                        ),
                    )
                if parse_result is None and compositional_plan is None and not has_structured_modifier:
                    parse_result = SemanticCommandCompiler.compile(
                        variant.text,
                        entities,
                        typed_world_model,
                        analysis,
                        context=context,
                    )

            resolved_mentions = mentions
            if compositional_plan is not None:
                resolved_mentions = compositional_plan.targets
            elif isinstance(parse_result, ParseResult):
                resolved_mentions = tuple(parse_result.resolved_entities)
            elif isinstance(parse_result, ClarificationRequest):
                resolved_mentions = parse_result.candidates
            typed_query_result = (
                isinstance(parse_result, ParseResult)
                and parse_result.frame.action is SemanticAction.QUERY
                and "query_result" in parse_result.frame.parameters
            )
            semantic_targets = () if typed_query_result else resolved_mentions
            if typed_query_result:
                slots["query_result_count"] = len(resolved_mentions)
            elif resolved_mentions and "entities" not in slots:
                slots["entities"] = tuple(
                    entity.entity_id for entity in resolved_mentions
                )

            if (
                typed_query_result
                and isinstance(parse_result, ParseResult)
                and parse_result.frame.semantic_graph is not None
            ):
                # Query result membership belongs to QueryResult/Discourse,
                # not to the request graph's target nodes. The projector has
                # already grounded its operands or relation anchor.
                graph = parse_result.frame.semantic_graph
            else:
                graph = graph.with_grounded_entities(
                    entity.entity_id for entity in semantic_targets
                )
            if grounded_locations:
                graph = graph.with_grounded_locations(grounded_locations)
            if isinstance(parse_result, ParseResult):
                raw_excluded_entity_ids = parse_result.frame.parameters.get(
                    "excluded_entity_ids", ()
                )
                raw_excluded_items: tuple[object, ...] = (
                    cast(tuple[object, ...], raw_excluded_entity_ids)
                    if isinstance(raw_excluded_entity_ids, tuple)
                    else ()
                )
                excluded_entity_ids = tuple(
                    entity_id
                    for entity_id in raw_excluded_items
                    if isinstance(entity_id, str)
                )
                if excluded_entity_ids and len(excluded_entity_ids) == len(
                    raw_excluded_items
                ):
                    graph = graph.with_excluded_entities(excluded_entity_ids)
                parse_result = attach_graph(parse_result, graph)
                slots.setdefault(
                    "domains",
                    tuple(sorted({entity.domain for entity in resolved_mentions})),
                )

            evidence += tuple(
                UnderstandingEvidence(
                    EvidenceKind.ENTITY,
                    entity.entity_id,
                    detail=f"exact grounded target: {entity.friendly_name}",
                    claim=f"entity={entity.entity_id}",
                    source_id="entity_resolution",
                )
                for entity in semantic_targets
            )
            evidence += tuple(
                UnderstandingEvidence(
                    EvidenceKind.AREA if area_id is not None else EvidenceKind.FLOOR,
                    spoken,
                    detail=area_id or floor_id,
                    claim=(
                        f"area={area_id}" if area_id is not None
                        else f"floor={floor_id}"
                    ),
                    source_id="semantic_location",
                )
                for spoken, area_id, floor_id in grounded_locations
                if area_id is not None or floor_id is not None
            )
            if typed_world_model is not None:
                evidence += tuple(
                    UnderstandingEvidence(
                        EvidenceKind.WORLD_MODEL,
                        entity.entity_id,
                        detail="grounded entity exists in current turn snapshot",
                        claim=f"live={entity.entity_id}",
                        source_id="world_model",
                    )
                    for entity in semantic_targets
                    if entity.entity_id in typed_world_model.entities_by_id
                )
            if (
                isinstance(parse_result, ParseResult)
                and semantic_targets
                and not typed_query_result
            ):
                validation = validate_command(build_semantic_command(parse_result))
                evidence += (
                    UnderstandingEvidence(
                        EvidenceKind.CAPABILITY,
                        parse_result.frame.intent,
                        score=(
                            CAPABILITY_CONTRADICTION_PENALTY
                            if validation is ValidationError.UNSUPPORTED_CAPABILITY
                            else 0.0
                        ),
                        detail=(
                            "target lacks required capability"
                            if validation is ValidationError.UNSUPPORTED_CAPABILITY
                            else "target capabilities agree with projected intent"
                        ),
                        polarity=(
                            EvidencePolarity.NEGATIVE
                            if validation is ValidationError.UNSUPPORTED_CAPABILITY
                            else EvidencePolarity.POSITIVE
                        ),
                        claim=f"capability_for={parse_result.frame.intent}",
                        source_id="command_validator",
                    ),
                )
                if parse_result.frame.property is not None:
                    evidence += (
                        UnderstandingEvidence(
                            EvidenceKind.PROPERTY,
                            parse_result.frame.property.name.lower(),
                            detail="property retained by compatibility projection",
                            claim=f"property={parse_result.frame.property.name.lower()}",
                            source_id="semantic_projection",
                        ),
                    )
                if parse_result.frame.numeric_value is not None:
                    evidence += (
                        UnderstandingEvidence(
                            EvidenceKind.UNIT,
                            parse_result.frame.numeric_value.unit.name.lower(),
                            detail="typed numeric value and unit",
                            claim=(
                                "unit="
                                + parse_result.frame.numeric_value.unit.name.lower()
                            ),
                            source_id="semantic_frame",
                        ),
                    )
            evidence += tuple(
                UnderstandingEvidence(
                    EvidenceKind.TEMPORAL,
                    temporal.value,
                    detail=temporal.kind.name.lower(),
                    claim=f"temporal={temporal.kind.name.lower()}",
                    source_id="temporal_semantics",
                )
                for temporal in candidate_document.temporal
            )

            conflicts = _resolved_conflicts(candidate_document, parse_result)
            missing = _missing_slots(candidate_document, slots, parse_result)
            registry_tokens = {
                normalize_for_compare(token)
                for entity in resolved_mentions
                for name in (
                    entity.friendly_name,
                    *entity.aliases,
                    entity.area_name or "",
                    *entity.area_aliases,
                    entity.floor_name or "",
                )
                for token in name.split()
            }
            if compositional_plan is not None:
                registry_tokens.update(
                    normalize_for_compare(token)
                    for entity in compositional_plan.targets
                    for name in (entity.friendly_name, *entity.aliases)
                    for token in name.split()
                )
            if isinstance(parse_result, ParseResult) and parse_result.frame.area:
                registry_tokens.update(
                    normalize_for_compare(token)
                    for token in parse_result.frame.area.text.split()
                )
            if isinstance(parse_result, ParseResult):
                excluded = parse_result.frame.parameters.get("excluded", ())
                if isinstance(excluded, (tuple, list)):
                    excluded_items = cast(tuple[object, ...] | list[object], excluded)
                    excluded_names: tuple[str, ...] = tuple(
                        name for name in excluded_items if isinstance(name, str)
                    )
                    registry_tokens.update(
                        normalize_for_compare(token)
                        for name in excluded_names
                        for token in name.split()
                    )
            unexplained = tuple(
                token
                for token in analysis.unexplained_tokens
                if normalize_for_compare(token) not in registry_tokens
            )
            evidence += tuple(
                UnderstandingEvidence(
                    EvidenceKind.NEGATIVE_EVIDENCE,
                    f"conflict={conflict}",
                    score=CONFLICT_PENALTY,
                    detail="competing semantic values in one active scope",
                    polarity=EvidencePolarity.NEGATIVE,
                    claim=f"unique_{conflict}",
                    source_id="candidate_validation",
                )
                for conflict in conflicts
            ) + tuple(
                UnderstandingEvidence(
                    EvidenceKind.NEGATIVE_EVIDENCE,
                    f"unexplained={token}",
                    score=UNEXPLAINED_TOKEN_PENALTY,
                    detail="meaning-bearing token has no grounded claim",
                    polarity=EvidencePolarity.NEGATIVE,
                    claim=f"explained_token={token}",
                    source_id="semantic_lexicon",
                )
                for token in unexplained
            )
            complete = (
                isinstance(parse_result, ParseResult)
                and not conflicts
                and not missing
            )
            score = evidence_score(evidence, variant_cost=variant.cost)
            key_parts = [document.utterance.speech_act.name.lower()]
            for name in ("actions", "domains", "device_classes", "properties"):
                if name in slots:
                    key_parts.append(f"{name}={slots[name]}")
            key = "|".join(key_parts)
            identity = (key, tuple(sorted((name, repr(value)) for name, value in slots.items())))
            candidate = MeaningCandidate(
                key=key,
                score=score,
                complete=complete,
                slots=slots,
                missing_slots=missing,
                conflicts=conflicts,
                evidence=evidence,
                graph=graph,
                rejection_reason=(
                    "conflict:" + ",".join(conflicts)
                    if conflicts
                    else "missing:" + ",".join(missing)
                    if missing
                    else "unexplained:" + ",".join(unexplained)
                    if unexplained and not complete
                    else None
                ),
            )
            previous_index = candidate_indexes.get(identity)
            if previous_index is None:
                candidate_indexes[identity] = len(candidates)
                candidates.append(candidate)
            else:
                previous = candidates[previous_index]
                merged_evidence = previous.evidence + tuple(
                    item for item in candidate.evidence
                    if item not in previous.evidence
                )
                if complete and not previous.complete:
                    # A normalized surface can preserve exactly the same
                    # slots while making them compilable. Keep that stronger
                    # proof, but retain evidence from every equivalent
                    # surface (including non-executable phonetic variants).
                    candidates[previous_index] = replace(
                        candidate, evidence=merged_evidence
                    )
                else:
                    candidates[previous_index] = replace(
                        previous, evidence=merged_evidence
                    )
            if parse_result is not None and not conflicts:
                record = parse_records.get(identity)
                if record is None or score > record[0]:
                    parse_records[identity] = (
                        score,
                        parse_result,
                        variant,
                        compositional_plan,
                    )

        candidates.sort(key=lambda item: (-item.score, item.key))
        complete = tuple(candidate for candidate in candidates if candidate.complete)
        semantic_ambiguity = (
            len(complete) > 1
            and complete[0].score - complete[1].score <= SEMANTIC_AMBIGUITY_MARGIN
            and _candidate_identity(complete[0]) != _candidate_identity(complete[1])
        )
        selected_record = None
        selected_candidate_index: int | None = None
        if not semantic_ambiguity:
            for index, candidate in enumerate(candidates):
                record = parse_records.get(_candidate_identity(candidate))
                if record is not None:
                    selected_record = record
                    selected_candidate_index = index
                    break
        return InterpreterResult(
            candidates=tuple(candidates),
            parse_result=selected_record[1] if selected_record is not None else None,
            selected_variant=selected_record[2] if selected_record is not None else None,
            compositional_plan=selected_record[3] if selected_record is not None else None,
            semantic_ambiguity=semantic_ambiguity,
            selected_candidate_index=selected_candidate_index,
        )
