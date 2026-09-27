"""Deterministic, order-independent compiler for German device commands.

Unlike a sentence-template parser, this module scans an utterance for
independent semantic facts (operation, target/domain, location, quantity and
value) and accepts the command only if those facts form exactly one valid
meaning against the current Home Assistant entity snapshot.  It is small,
local and dependency-free; no probabilistic model or network service is
involved.

The established Hassil grammars remain the compatibility fast path.  This
compiler is the safe fallback for natural paraphrases and free word order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..areas import AreaSnapshot
from ..entities import (
    EntityIndex,
    EntitySnapshot,
    generate_aliases,
    is_outdoor_entity,
    normalize_for_compare,
)
from ..name_similarity import bounded_name_similarity
from ..world_model import WorldModel
from .degree_semantics import extract_degree
from .constraint_resolver import Constraints, resolve_candidates
from .entity_resolution import (
    ResolutionStatus,
    ResolveStatus,
    mentioned_entities,
    rank_semantic_targets,
    resolve_entity,
    resolve_entity_scored,
    resolve_mentioned_target,
)
from .frame import (
    AreaReference,
    Comparison,
    Quantifier,
    SemanticFrame,
    TargetReference,
    TemporalExpression,
)
from .parser import ClarificationRequest, ParseResult
from .primitives import (
    SemanticAction,
    SemanticDirection,
    SemanticProperty,
    SemanticQuantity,
)
from .query_command import (
    QueryCommand,
    QueryFilter,
    QueryScope,
    QueryTarget,
    QueryTargetKind,
)
from .query_executor import QueryExecutor
from .registered_operation_compiler import climate_in_named_area
from .semantic_exclusion import split_exclusion as _split_exclusion
from .semantic_lexicon import SemanticAnalysis, SemanticKind, analyse_semantics
from .semantic_catalog import (
    DOMAIN_WORDS,
    INTENT_BY_DOMAIN_ACTION,
    MEASUREMENT_PROPERTY_SPECS,
    SEMANTIC_RESOLUTION_WORDS,
    V7_ENTITY_AMBIGUITY_MARGIN,
    VALUE_INTENT_BY_DOMAIN_PROPERTY,
)
from .understanding_context import UnderstandingContext
from .semantic_location import (
    has_explicit_location_cue,
    resolve_coordinated_locations,
    resolve_semantic_location,
    whole_home_phrase,
)
from .semantic_state import (
    QUERYABLE_STATE_DOMAINS,
    SemanticState,
    supports_state_predicate,
)
from .semantic_utterance import SpeechAct, analyse_utterance


def _device_class_targets(
    analysis: SemanticAnalysis,
) -> list[tuple[str, str | None]]:
    """Narrow lexicon object values to typed domain/device-class pairs."""
    targets: list[tuple[str, str | None]] = []
    for value in analysis.values(SemanticKind.DEVICE_CLASS):
        if not isinstance(value, tuple) or len(value) != 2:
            continue
        domain, device_class = value
        if isinstance(domain, str) and (
            device_class is None or isinstance(device_class, str)
        ):
            targets.append((domain, device_class))
    return targets


@dataclass(frozen=True)
class SemanticFacts:
    """Observable facts extracted without committing to an execution."""

    intents: frozenset[str]
    domains: frozenset[str]
    percent: int | None
    temperature: float | None
    quantity: Quantifier | None
    location_text: str | None
    area_id: str | None
    floor_id: str | None


_QUESTION_RE = re.compile(
    r"^\s*(?:wer|was|wie|welch\w*|wo|wann|warum|wieso|ist|sind|hat|haben|gibt)\b",
    re.IGNORECASE,
)
_LOCATION_CUE_RE = re.compile(r"\b(?:im|in\s+der|in\s+dem|am|beim)\s+", re.I)
_LEVEL_CUE_RE = re.compile(r"(?<!nach\s)\b(?:oben|unten)\b", re.I)
_PLURAL_RE = re.compile(
    r"\b(?:lichter|lampen|leuchten|steckdosen|rollläden|rolläden|"
    r"die\s+rol{1,3}aden|rollos|"
    r"jalousien|ventilatoren|heizungen|thermostate|skripte|lautsprecher|"
    r"staubsauger|ventile)\b",
    re.I,
)
_ALL_RE = re.compile(r"\b(?:alle|sämtliche|sämtlichen|jede|jeden|jedes|die\s+ganzen)\b", re.I)
_BOTH_RE = re.compile(r"\bbeide(?:n|r)?\b", re.I)
_COUNT_WORDS = {
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
_COUNT_RE = re.compile(r"\b(" + "|".join(_COUNT_WORDS) + r"|[2-9]|10)\b", re.I)
_ORDERED_SUBSET_RE = re.compile(r"\b(?:erste\w*|letzte\w*)\b", re.I)
_HALF_RE = re.compile(r"\b(?:halb|halbe(?:r|n)?|hälfte|zur\s+hälfte)\b", re.I)
_ZERO_RE = re.compile(r"\b(?:komplett|ganz|vollständig)\s+(?:runter|herunter|zu)\b", re.I)
_HUNDRED_RE = re.compile(r"\b(?:komplett|ganz|vollständig)\s+(?:hoch|auf)\b", re.I)
_PERCENT_RE = re.compile(
    r"(?:\bauf\s+(?P<after>100|[1-9]?\d)\b|"
    r"\b(?P<unit>100|[1-9]?\d)\s*(?:prozent|%)\b)", re.I
)
_ANY_PERCENT_RE = re.compile(r"\b(?P<value>\d+)\s*(?:prozent|%)\b", re.I)
_FIFTY_PERCENT_RE = re.compile(r"\bfünfzig\s+prozent\b", re.I)
_TEMPERATURE_RE = re.compile(
    r"\bauf\s+(?:(?:mindestens|höchstens|nicht\s+höher\s+als|über|unter)\s+)?"
    r"(?P<value>-?\d{1,2}(?:[,.]\d)?)\s*(?:grad|°\s*c|°c)\b",
    re.I,
)
_DIRECTIVE_RE = re.compile(
    r"\b(?:bitte|soll(?:st|en|t)?|möchte|will|kannste|könntest|koenntest|"
    r"würdest|wuerdest|würde\s+gern|würd\s+gern|wuerd\s+gern)\b", re.I
)
_COPULA_STATEMENT_RE = re.compile(
    r"\b(?:ist|sind|war|waren|bleibt|bleiben)\b", re.I
)

_TOKEN_RE = re.compile(r"[\wäöüß]+", re.I)
_STOP_WORDS = {
    "der", "die", "das", "den", "dem", "ein", "eine", "einen", "einem",
    "vom", "von", "hat", "haben", "was", "wie", "welchen", "welchem",
    "bitte", "kannst", "du", "mir", "mal", "doch", "jetzt", "vorhandenen",
    "im", "in", "am", "auf", "aus", "an", "zu", "und", "sind", "ist",
    "alle", "sämtliche", "sämtlichen", "jede", "jeden", "jedes", "beide",
    "zeigt", "zeigen", "welche", "welcher", "welches",
    "ganz", "um", "außer", "ausser", "mit", "ausnahme", "bei",
    "hoch", "noch", "vorhanden", "vorhandene", "vorhandenes", "stehen",
    "steht", "sag", "sage", "ob", "zustand", "status", "sensor", "gerät", "geräte",
    "verbraucht", "verbrauchen", "anzeigt", "also", "außerdem", "ausserdem", "okay", "ok", "gut", "nun", "na",
    "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
    "zehn", "nur",
}
_QUERY_MARKER_RE = re.compile(
    r"\b(?:ist|sind|welch\w*|wie\s+viele|wo|gibt\s+es|haben\s+wir|irgendein\w*|keine?\w*)\b",
    re.I,
)
_SINGULAR_NAMED_QUERY_RE = re.compile(r"^\s*ist\s+(?:das|der|die)\b", re.I)
_BARE_NAMED_STATE_QUERY_RE = re.compile(
    r"^\s*(?:wie\s+ist|welchen\s+zustand\s+hat|(?:wie\s+lautet\s+)?der\s+status\s+von)\b",
    re.I,
)
_DURATION_QUESTION_RE = re.compile(r"^\s*wie\s+lange\b", re.I)
_INDEFINITE_EXISTS_RE = re.compile(
    r"^\s*ist(?:\s+(?:im|in\s+der|in\s+dem)\s+.+?)?\s+ein(?:e|en)?\s+",
    re.I,
)
_DEVICE_QUERY_RE = re.compile(r"^\s*welch\w*\s+geräte\b", re.I)
_ENTITY_STATE_WORDING_RE = re.compile(
    r"^\s*(?:welchen\s+zustand\s+hat|wie\s+ist\s+der\s+zustand(?:\s+von|\s+vom)?|"
    r"was\s+ist\s+der\s+status(?:\s+von|\s+vom)?|"
    r"(?:zeig|zeige)\s+mir\s+(?:bitte\s+)?den\s+zustand(?:\s+von|\s+vom)?|"
    r"(?:wie\s+lautet\s+)?der\s+status\s+von)\b",
    re.I,
)

_QUERY_EXECUTOR = QueryExecutor()


def _compile_device_query(
    text: str,
    entities: list[EntitySnapshot],
    analysis: SemanticAnalysis,
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Compile a cross-domain device inventory query without a parser template."""
    if _DEVICE_QUERY_RE.search(text) is None:
        return None
    location = resolve_semantic_location(text, entities, world_model)
    if location is None or location[1] is None or location[2] is not None:
        return None
    states = [
        span.value
        for span in analysis.matching(SemanticKind.STATE)
        if isinstance(span.value, SemanticState)
    ]
    # ``eingeschaltet`` produces an action marker as well, but this function
    # is entered only for a proven QUERY and remains read-only by type.
    states = list(dict.fromkeys(states))
    if len(states) > 1:
        return None
    state = states[0] if states else None
    area = AreaSnapshot(area_id=location[1], name=location[0])
    command = QueryCommand(
        intent="HassDeviceQuery",
        scope=QueryScope.LIST,
        target=QueryTarget(kind=QueryTargetKind.DEVICE, area=area),
        filter=QueryFilter(state=state),
    )
    result = _QUERY_EXECUTOR.execute(command, [], world_model)
    return ParseResult(
        frame=SemanticFrame(
            intent="HassDeviceQuery",
            target=None,
            area=AreaReference(
                text=location[0], area_id=location[1], area_name=location[0]
            ),
            quantifier=Quantifier(kind="all"),
            parameters={"query_command": command, "query_result": result},
            source_text=text,
            action=SemanticAction.QUERY,
        ),
        resolved_entities=[],
    )


def _compile_entity_state_query(
    text: str,
    entities: list[EntitySnapshot],
    analysis: SemanticAnalysis,
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Compile explicit status/state questions to one registry-backed entity."""
    if _ENTITY_STATE_WORDING_RE.search(text) is None:
        return None
    mentioned = mentioned_entities(
        text,
        entities,
        index=world_model.entity_index if world_model is not None else None,
    )
    candidates = list(mentioned)
    if not candidates:
        targets = _device_class_targets(analysis)
        location = resolve_semantic_location(text, entities, world_model)
        if len(targets) != 1:
            return None
        domain, device_class = targets[0]
        area_id = location[1] if location else None
        floor_id = location[2] if location else None
        candidates = list(
            world_model.select_entities(
                domain=domain,
                device_class=device_class,
                area_id=area_id,
                floor_id=floor_id,
            )
            if world_model is not None
            else resolve_candidates(
                entities,
                Constraints(
                    domain=domain,
                    device_class=device_class,
                    area_id=area_id,
                    floor_id=floor_id,
                ),
            )
        )
    if len(candidates) != 1:
        return None
    entity = candidates[0]
    location = resolve_semantic_location(text, entities, world_model)
    area = (
        AreaSnapshot(area_id=location[1], name=location[0])
        if location is not None and location[1] is not None
        else None
    )
    command = QueryCommand(
        intent="HassEntityStateQuery",
        scope=QueryScope.SINGLE,
        target=QueryTarget(
            domain=entity.domain,
            device_class=entity.device_class,
            area=area,
            entity_id=entity.entity_id,
        ),
        filter=QueryFilter(),
    )
    result = _QUERY_EXECUTOR.execute(command, [entity])
    return ParseResult(
        frame=SemanticFrame(
            intent="HassEntityStateQuery",
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=(
                AreaReference(location[0], location[1], location[0])
                if location is not None and location[1] is not None
                else None
            ),
            parameters={"query_command": command, "query_result": result},
            source_text=text,
            action=SemanticAction.QUERY,
        ),
        resolved_entities=list(result.entities),
    )


def _compile_measurement_query(
    text: str,
    entities: list[EntitySnapshot],
    analysis: SemanticAnalysis,
    world_model: WorldModel | None = None,
    *,
    inferred_property: str | None = None,
) -> ParseResult | None:
    """Compile one property/location question from independent facts."""
    properties = (
        frozenset({inferred_property})
        if inferred_property is not None
        else analysis.values(SemanticKind.PROPERTY)
    )
    comparators = analysis.values(SemanticKind.COMPARATOR)
    if len(properties) != 1 or len(comparators) > 1:
        return None
    canonical = next(iter(properties))
    if not isinstance(canonical, str):
        return None
    spec = MEASUREMENT_PROPERTY_SPECS.get(canonical)
    if spec is None:
        return None
    domain, device_class, label, property_name = spec
    location = resolve_semantic_location(text, entities, world_model)
    named_entity: EntitySnapshot | None = None
    if location is None:
        # A property word alone is not an entity mention.  Require at least
        # one residual registry token before name ranking so a conjunction
        # such as "Temperatur und Luftfeuchtigkeit?" cannot turn its second
        # property into an accidental single-sensor lookup.
        if not {
            normalize_for_compare(token)
            for token in analysis.unexplained_tokens
            if normalize_for_compare(token) not in _STOP_WORDS
        }:
            return None
        ranked = rank_semantic_targets(
            text,
            entities,
            domains=frozenset({domain}),
            ignored_tokens=frozenset(
                _STOP_WORDS
                | {
                    normalize_for_compare(canonical),
                    normalize_for_compare(label),
                    normalize_for_compare(property_name),
                }
            ),
            index=(world_model.entity_index if world_model is not None else None),
        )
        compatible = tuple(
            candidate
            for candidate in ranked
            if device_class is None or candidate.entity.device_class == device_class
        )
        if not compatible:
            return None
        best_score = compatible[0].score
        best = tuple(item for item in compatible if item.score == best_score)
        if len(best) != 1:
            return None
        named_entity = best[0].entity
        if _has_unexplained_meaning(
            analysis, (named_entity.friendly_name, *named_entity.aliases)
        ):
            return None
        area_id = floor_id = None
        matched = [named_entity]
    else:
        if _has_unexplained_meaning(analysis, (location[0],)):
            return None
        area_id, floor_id = location[1], location[2]
        matched = [
            entity
            for entity in entities
            if entity.domain == domain
            and (device_class is None or entity.device_class == device_class)
            and (area_id is None or entity.area_id == area_id)
            and (floor_id is None or entity.floor_id == floor_id)
        ]
        if area_id is None and floor_id is None and domain == "sensor":
            # A whole-home measurement ("im ganzen Haus") means indoors: an
            # outdoor sensor would skew the house average (F21).
            indoor = [entity for entity in matched if not is_outdoor_entity(entity)]
            matched = indoor or matched
    comparator = next(iter(comparators), None)
    threshold_match = re.search(r"\b\d+(?:[,.]\d+)?\b", text)
    if comparator is not None and threshold_match is None:
        return None
    if comparator is not None and threshold_match is not None:
        threshold = float(threshold_match.group(0).replace(",", "."))
        predicates = {
            "lt": lambda value: value < threshold,
            "gt": lambda value: value > threshold,
            "lte": lambda value: value <= threshold,
            "gte": lambda value: value >= threshold,
        }
        predicate = predicates.get(str(comparator))
        if predicate is None:
            return None
        filtered: list[EntitySnapshot] = []
        for entity in matched:
            try:
                if predicate(float(entity.state)):
                    filtered.append(entity)
            except (TypeError, ValueError):
                continue
        matched = filtered
    if not matched:
        return None
    matched.sort(key=lambda entity: (entity.area_name or "", entity.friendly_name))
    average = "average" in analysis.values(SemanticKind.QUERY_SCOPE)
    intent = (
        "HassLocationPropertyQuery"
        if len(matched) > 1 or average
        else "HassGetState"
    )
    target_text = (
        named_entity.friendly_name
        if named_entity is not None
        else location[0] if location is not None else ""
    )
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                text=target_text,
                entity_id=(named_entity.entity_id if named_entity else None),
                domain=domain,
                device_class=device_class,
            ),
            area=(
                AreaReference(
                    text=target_text, area_id=area_id, area_name=matched[0].area_name
                )
                if area_id is not None
                else None
            ),
            quantifier=Quantifier(kind="all") if len(matched) > 1 else None,
            parameters={
                "property": property_name,
                "property_label": label,
                "average": average,
                "location_kind": (
                    "entity" if named_entity is not None
                    else "area" if area_id is not None else "floor"
                ),
                "location_id": (
                    named_entity.entity_id if named_entity is not None
                    else area_id if area_id is not None else floor_id
                ),
            },
            source_text=text,
            action=SemanticAction.QUERY,
        ),
        resolved_entities=matched,
    )


def _compile_comparison_query(
    text: str,
    entities: list[EntitySnapshot],
    analysis: SemanticAnalysis,
    world_model: WorldModel | None = None,
) -> ParseResult | None:
    """Compile a typed current-value comparison without a grammar parser."""
    comparators = analysis.values(SemanticKind.COMPARATOR)
    domains = {
        value for value in analysis.values(SemanticKind.DOMAIN)
        if isinstance(value, str)
    }
    properties = analysis.values(SemanticKind.PROPERTY)
    if not domains and "battery" in properties:
        domains = {"sensor"}
    if not domains and re.search(r"\b(?:räume?|heizungen?|thermostate?)\b", text, re.I):
        domains = {"climate"}
    threshold_match = re.search(r"\b\d+(?:[,.]\d+)?\b", text)
    if len(comparators) != 1 or len(domains) != 1 or threshold_match is None:
        return None
    comparator = str(next(iter(comparators)))
    domain = next(iter(domains))
    threshold = float(threshold_match.group(0).replace(",", "."))
    has_percent = re.search(r"(?:prozent|%)", text, re.I) is not None
    has_temperature = "temperature" in properties
    has_battery = "battery" in properties
    if not (
        (has_percent and not has_temperature and domain in {"light", "cover"})
        or (has_temperature and not has_percent and domain == "climate")
        or (has_percent and has_battery and domain == "sensor")
    ):
        return None
    location = resolve_semantic_location(text, entities, world_model)
    if _LOCATION_CUE_RE.search(text) is not None and location is None:
        return None
    area_id = location[1] if location else None
    floor_id = location[2] if location else None
    candidates = _scoped_candidates(
        entities, domain, ((location,) if location else ()), world_model
    )
    if has_battery:
        candidates = [
            entity for entity in candidates if entity.device_class == "battery"
        ]
    predicates = {
        "lt": lambda value: value < threshold,
        "gt": lambda value: value > threshold,
        "lte": lambda value: value <= threshold,
        "gte": lambda value: value >= threshold,
    }
    predicate = predicates.get(comparator)
    if predicate is None:
        return None

    def current_value(entity: EntitySnapshot) -> float | None:
        raw = (
            entity.state
            if domain == "sensor"
            else
            entity.attributes.get("current_temperature")
            if domain == "climate"
            else entity.attributes.get("current_position")
            if domain == "cover"
            else entity.attributes.get("brightness")
        )
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value / 255 * 100 if domain == "light" else value

    matches = [
        entity for entity in candidates
        if (value := current_value(entity)) is not None and predicate(value)
    ]
    if not matches:
        return None
    return ParseResult(
        frame=SemanticFrame(
            intent="HassGetState" if domain == "sensor" else "HassQueryComparison",
            target=TargetReference(text=domain, domain=domain),
            area=(
                AreaReference(text=location[0], area_id=area_id)
                if location is not None and area_id is not None else None
            ),
            quantifier=Quantifier(kind="all"),
            parameters={
                "comparison": Comparison(operator=comparator, value=threshold),
                **({"floor_id": floor_id} if floor_id is not None else {}),
            },
            source_text=text,
            action=SemanticAction.QUERY,
            quantity=SemanticQuantity.all(),
        ),
        resolved_entities=matches,
    )


def _tokens(text: str) -> set[str]:
    return {
        normalize_for_compare(token)
        for token in _TOKEN_RE.findall(text)
        if normalize_for_compare(token) not in _STOP_WORDS
    }


def _has_unexplained_meaning(analysis, dynamic_texts=()) -> bool:
    """Whether lexical coverage leaves non-registry meaning unexplained."""
    dynamic_tokens = {
        token
        for dynamic_text in dynamic_texts
        if dynamic_text
        for token in _tokens(dynamic_text)
    }
    return bool({
        normalize_for_compare(token)
        for token in analysis.unexplained_tokens
        if normalize_for_compare(token) not in _STOP_WORDS
    } - dynamic_tokens)


def _has_registry_typo(
    analysis: SemanticAnalysis, candidates: list[EntitySnapshot]
) -> bool:
    """Detect a bounded name typo hidden by an exact partial alias.

    The canonical resolver intentionally accepts distinctive fragments such
    as ``links``.  That fragment must not silently hide the misspelled first
    half of ``Deckenlicth links`` and authorize an action.
    """
    unexplained = {
        normalize_for_compare(token)
        for token in analysis.unexplained_tokens
        if len(normalize_for_compare(token)) >= 4
    }
    registry_tokens = {
        token
        for entity in candidates
        for name in (entity.friendly_name, *entity.aliases)
        for token in _tokens(name)
        if len(token) >= 4
    }
    return any(
        spoken != registered
        and bounded_name_similarity(spoken, registered).accepted
        for spoken in unexplained
        for registered in registry_tokens
    )


def _quantity(text: str) -> Quantifier | None:
    if _ORDERED_SUBSET_RE.search(text):
        return None
    if _BOTH_RE.search(text):
        return Quantifier("both")
    count_text = re.sub(
        r"\b-?\d+(?:[,.]\d+)?\s*(?:grad|°\s*c|°c|prozent|%)\b",
        "",
        text,
        flags=re.I,
    )
    count = _COUNT_RE.search(count_text)
    if count:
        raw = count.group(1).casefold()
        return Quantifier("count", _COUNT_WORDS.get(raw, int(raw) if raw.isdigit() else None))
    if _ALL_RE.search(text) or _PLURAL_RE.search(text):
        return Quantifier("all")
    return None


def _percent(text: str) -> int | None:
    if _HALF_RE.search(text):
        return 50
    if _ZERO_RE.search(text):
        return 0
    if _HUNDRED_RE.search(text):
        return 100
    if _FIFTY_PERCENT_RE.search(text):
        return 50
    match = _PERCENT_RE.search(text)
    return int(match.group("after") or match.group("unit")) if match else None


def _temperature(text: str) -> float | None:
    match = _TEMPERATURE_RE.search(text)
    if match is None:
        return None
    value = float(match.group("value").replace(",", "."))
    return value if 5 <= value <= 30 else None


def canonicalize_exclusion_clause(text: str) -> str:
    """Move an exclusion behind a separable particle for legacy grammars.

    The semantic compiler itself consumes the split representation directly.
    Automation action Hassil grammars still use the established canonical
    ``... aus außer Name`` shape, so this small adapter lets both word orders
    share the same safe entity-resolution path.
    """
    positive, targets = _split_exclusion(text)
    if not targets:
        return text
    return f"{positive} außer {' und '.join(targets)}"


def _resolve_exclusions(
    names: tuple[str, ...],
    entities: list[EntitySnapshot],
    candidates: list[EntitySnapshot],
    index: EntityIndex | None = None,
) -> tuple[list[EntitySnapshot], tuple[str, ...], tuple[str, ...]] | None:
    """Resolve every exclusion exactly and ensure it belongs to the scope."""
    if not names:
        return candidates, (), ()
    excluded: list[EntitySnapshot] = []
    for name in names:
        result = resolve_entity(name, entities, index=index)
        if result.status is not ResolveStatus.OK or result.entity is None:
            return None
        if result.entity not in candidates or result.entity in excluded:
            return None
        excluded.append(result.entity)
    excluded_ids = {entity.entity_id for entity in excluded}
    remaining = [entity for entity in candidates if entity.entity_id not in excluded_ids]
    if not remaining:
        return None
    return (
        remaining,
        tuple(entity.friendly_name for entity in excluded),
        tuple(entity.entity_id for entity in excluded),
    )


def _scoped_candidates(
    entities: list[EntitySnapshot],
    domain: str,
    locations: tuple[tuple[str, str | None, str | None], ...],
    world_model: WorldModel | None,
) -> list[EntitySnapshot]:
    """Return one stable union for one or more explicit location scopes."""
    scopes = locations or (("", None, None),)
    selected: list[EntitySnapshot] = []
    seen: set[str] = set()
    for _, area_id, floor_id in scopes:
        matches = list(world_model.select_entities(
            domain=domain, area_id=area_id, floor_id=floor_id,
        )) if world_model is not None else resolve_candidates(
            entities,
            Constraints(domain=domain, area_id=area_id, floor_id=floor_id),
        )
        for entity in matches:
            if entity.entity_id not in seen:
                seen.add(entity.entity_id)
                selected.append(entity)
    return selected


def _compile_relative_climate(
    text: str,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    increase = re.search(r"\b(?:wärmer|waermer|erhöh\w*)\b", text, re.I)
    decrease = re.search(r"\b(?:kälter|kaelter|senk\w*|reduzier\w*)\b", text, re.I)
    if bool(increase) == bool(decrease):
        return None
    named = mentioned_entities(
        text,
        entities,
        index=world_model.entity_index if world_model is not None else None,
    )
    candidates = [entity for entity in named if entity.domain == "climate"]
    if not candidates:
        # "Mach die Heizung im Schlafzimmer wärmer": the climate entity of
        # the named room, if it is the only one there (F11).
        location = resolve_semantic_location(text, entities, world_model)
        if location is not None:
            candidates = list(
                world_model.select_entities(
                    domain="climate", area_id=location[1], floor_id=location[2]
                )
                if world_model is not None
                else resolve_candidates(
                    entities,
                    Constraints(domain="climate", area_id=location[1], floor_id=location[2]),
                )
            )
    if len(candidates) != 1:
        return None
    entity = candidates[0]
    adjustment = extract_degree(text)
    intent = (
        "HassClimateIncreaseTemperature"
        if increase else "HassClimateDecreaseTemperature"
    )
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=None,
            parameters={"step": adjustment.climate_degrees},
            source_text=text,
            action=SemanticAction.ADJUST,
            property=SemanticProperty.TEMPERATURE,
            direction=(
                SemanticDirection.INCREASE
                if increase else SemanticDirection.DECREASE
            ),
            degree=adjustment.degree,
        ),
        resolved_entities=[entity],
    )


def _compile_relative_light(
    text: str,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    """Compile a bounded relative brightness command for exactly one light."""
    increase = re.search(r"\bheller\b", text, re.I)
    decrease = re.search(r"\bdunkler\b", text, re.I)
    if bool(increase) == bool(decrease):
        return None
    location = resolve_semantic_location(text, entities, world_model)
    named = [
        entity
        for entity in mentioned_entities(
            text,
            entities,
            index=world_model.entity_index if world_model is not None else None,
        )
        if entity.domain == "light"
    ]
    if named:
        candidates = named
    else:
        candidates = list(
            world_model.select_entities(
                domain="light",
                area_id=location[1] if location else None,
                floor_id=location[2] if location else None,
            )
            if world_model is not None
            else resolve_candidates(
                entities,
                Constraints(
                    domain="light",
                    area_id=location[1] if location else None,
                    floor_id=location[2] if location else None,
                ),
            )
        )
    candidates = [
        entity for entity in candidates if "BRIGHTNESS" in entity.capabilities
    ]
    if len(candidates) != 1:
        return None
    entity = candidates[0]
    adjustment = extract_degree(text)
    explicit_percent = _ANY_PERCENT_RE.search(text)
    step_percent = (
        int(explicit_percent.group("value"))
        if explicit_percent is not None
        else adjustment.light_percent
    )
    intent = "HassLightBrighten" if increase else "HassLightDim"
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=None,
            parameters={"step_percent": step_percent},
            source_text=text,
            action=SemanticAction.ADJUST,
            property=SemanticProperty.BRIGHTNESS,
            direction=(
                SemanticDirection.INCREASE
                if increase
                else SemanticDirection.DECREASE
            ),
            degree=adjustment.degree,
        ),
        resolved_entities=[entity],
    )


_LIGHT_COLORS = {
    "rot": "red",
    "grün": "green",
    "blau": "blue",
    "gelb": "yellow",
    "orange": "orange",
    "lila": "purple",
    "violett": "purple",
    "weiß": "white",
    "pink": "pink",
    "rosa": "pink",
    "türkis": "turquoise",
    "cyan": "cyan",
}
_NUMBER_WORDS = {
    "eine": 1,
    "einer": 1,
    "eins": 1,
    "fünf": 5,
    "zehn": 10,
}


def _single_named_capable_entity(
    text: str,
    entities: list[EntitySnapshot],
    domain: str,
    capability: str,
    world_model: WorldModel | None,
) -> EntitySnapshot | None:
    matches = [
        entity
        for entity in mentioned_entities(
            text,
            entities,
            index=world_model.entity_index if world_model is not None else None,
        )
        if entity.domain == domain and capability in entity.capabilities
    ]
    if not matches:
        # A distinctive part of the registry name ("den LED-Streifen" for
        # "LED-Streifen Wohnzimmer"), resolved by the same canonical
        # resolver as on/off commands - unique or nothing (F11).
        resolution = resolve_mentioned_target(
            text,
            entities,
            frozenset({domain}),
            index=world_model.entity_index if world_model is not None else None,
        )
        if (
            resolution.status is ResolutionStatus.RESOLVED
            and resolution.entity is not None
            and capability in resolution.entity.capabilities
        ):
            return resolution.entity
        spoken = {
            normalize_for_compare(token)
            for token in analyse_semantics(text).unexplained_tokens
            if normalize_for_compare(token) not in _STOP_WORDS
        }
        if spoken:
            matches = [
                entity
                for entity in entities
                if entity.domain == domain
                and capability in entity.capabilities
                and spoken <= set(_tokens(entity.friendly_name))
            ]
    return matches[0] if len(matches) == 1 else None


_ROOM_LIGHT_RE = re.compile(r"\b(?:das|den)\s+licht\b|^\s*licht\b", re.I)
_WH_LIST_QUESTION_RE = re.compile(r"^\s*(?:und\s+)?welche[nmrs]?\b", re.I)
_SETUP_WITHOUT_VALUE_RE = re.compile(
    r"^\s*(?:bitte\s+|kannst\s+du\s+)?stell\w*\s+.+\s+ein\s*[.!?]*$", re.I
)
_LOCK_VERB_RE = re.compile(
    r"^\s*(?:bitte\s+)?(?:(?P<lock>verriegl\w*|verriegel\w*|schlie(?:ß|ss)\w*\s+.+\s+ab|"
    r"sperr\w*\s+.+\s+ab)|(?P<unlock>entriegl\w*|entriegel\w*|"
    r"schlie(?:ß|ss)\w*\s+.+\s+auf|sperr\w*\s+.+\s+auf))\b",
    re.I,
)
_LOCK_FILLER = frozenset({
    "die", "der", "das", "den", "dem", "bitte", "mal", "ab", "auf", "jetzt", "sofort",
})


def _compile_lock_by_name_part(
    text: str, entities: list[EntitySnapshot]
) -> ParseResult | None:
    """ "Verriegle die Haustür" -> the lock "Haustürschloss" (F11).

    A lock verb only applies to locks. When the spoken name is not a lock
    itself (the door contact "Haustür"), it is resolved among the locks by
    the canonical resolver - unique or nothing. Unlocking stays subject to
    the unchanged confirmation policy downstream.
    """
    verb = _LOCK_VERB_RE.search(text)
    if verb is None:
        return None
    locks = [entity for entity in entities if entity.domain == "lock"]
    if not locks or mentioned_entities(text, locks):
        return None
    words = [
        word
        for word in _TOKEN_RE.findall(text)
        if normalize_for_compare(word) not in _LOCK_FILLER
        and not re.fullmatch(
            r"(?:verriegl|verriegel|entriegl|entriegel|schlie(?:ß|ss)|sperr)\w*", word, re.I
        )
    ]
    if not words:
        return None
    resolution = resolve_entity_scored(" ".join(words), locks)
    if resolution.status is not ResolutionStatus.RESOLVED or resolution.entity is None:
        return None
    entity = resolution.entity
    unlock = verb.group("unlock") is not None
    return ParseResult(
        frame=SemanticFrame(
            intent="HassUnlock" if unlock else "HassLock",
            target=TargetReference(entity.friendly_name, entity.entity_id, entity.domain),
            area=None,
            source_text=text,
            action=SemanticAction.UNLOCK if unlock else SemanticAction.LOCK,
        ),
        resolved_entities=[entity],
    )


def _compile_light_color(
    text: str,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    temperature = re.search(r"\b(warmweiß|kaltweiß)\b", text, re.I)
    color_matches = [
        (word, value)
        for word, value in _LIGHT_COLORS.items()
        if re.search(rf"\b{word}\b", text, re.I)
    ]
    if temperature is None and len(color_matches) != 1:
        return None
    if temperature is not None and color_matches:
        return None
    capability = "COLOR_TEMPERATURE" if temperature is not None else "COLOR"
    entity = _single_named_capable_entity(
        text, entities, "light", capability, world_model
    )
    if entity is None:
        return None
    if _has_unexplained_meaning(
        analyse_semantics(text),
        (entity.friendly_name, *entity.aliases),
    ):
        return None
    if temperature is not None:
        kelvin = 2700 if temperature.group(1).casefold().startswith("warm") else 6500
        intent = "HassLightSetColorTemp"
        parameters: dict[str, object] = {"color_temp_kelvin": kelvin}
        property_ = SemanticProperty.COLOR_TEMPERATURE
    else:
        word, color = color_matches[0]
        intent = "HassLightSetColor"
        parameters = {"color_name": color, "spoken_color": word}
        property_ = SemanticProperty.COLOR
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=None,
            parameters=parameters,
            source_text=text,
            action=SemanticAction.SET,
            property=property_,
        ),
        resolved_entities=[entity],
    )


def _compile_fan_speed(
    text: str,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> ParseResult | None:
    level = re.search(r"\b(?:stufe|stufen)\s+(10|[1-9])\b", text, re.I)
    faster = re.search(r"\bschneller\b", text, re.I)
    slower = re.search(r"\blangsamer\b", text, re.I)
    if sum((level is not None, faster is not None, slower is not None)) != 1:
        return None
    entity = _single_named_capable_entity(
        text, entities, "fan", "FAN_SPEED", world_model
    )
    if entity is None:
        return None
    if level is not None:
        intent = "HassFanSetSpeed"
        parameters: dict[str, object] = {"level": int(level.group(1))}
        direction = None
    elif faster is not None:
        intent = "HassFanIncreaseSpeed"
        parameters = {}
        direction = SemanticDirection.INCREASE
    else:
        intent = "HassFanDecreaseSpeed"
        parameters = {}
        direction = SemanticDirection.DECREASE
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=None,
            parameters=parameters,
            source_text=text,
            action=SemanticAction.ADJUST,
            property=SemanticProperty.FAN_SPEED,
            direction=direction,
        ),
        resolved_entities=[entity],
    )


def _temporal_expression(text: str) -> tuple[str, TemporalExpression] | None:
    amount = re.search(
        r"\b(?P<kind>in|für)\s+(?P<amount>\d+|eine|einer|eins|fünf|zehn)\s+"
        r"(?P<unit>minute(?:n)?|stunde(?:n)?)\b",
        text,
        re.I,
    )
    if amount is not None:
        raw = amount.group("amount").casefold()
        value = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw)
        if value is None or not 1 <= value <= 59:
            return None
        minutes = value * 60 if amount.group("unit").casefold().startswith("stunde") else value
        kind = "delay" if amount.group("kind").casefold() == "in" else "duration"
        stripped = re.sub(r"\s+", " ", text[: amount.start()] + " " + text[amount.end() :]).strip()
        return stripped, TemporalExpression(kind=kind, minutes=minutes)
    relative = re.search(r"\b(heute|morgen)\s+(früh|abend)\b", text, re.I)
    if relative is not None:
        day = "today" if relative.group(1).casefold() == "heute" else "tomorrow"
        part = "morning" if relative.group(2).casefold() == "früh" else "evening"
        stripped = re.sub(r"\s+", " ", text[: relative.start()] + " " + text[relative.end() :]).strip()
        return stripped, TemporalExpression(kind="relative_time", relative=f"{day}_{part}")
    absolute = re.search(r"\bum\s+(2[0-3]|1?\d)\s+uhr\b", text, re.I)
    if absolute is not None:
        stripped = re.sub(r"\s+", " ", text[: absolute.start()] + " " + text[absolute.end() :]).strip()
        return stripped, TemporalExpression(kind="absolute_time", hour=int(absolute.group(1)))
    return None


def _intents(
    text: str,
    domains: frozenset[str],
    percent: int | None,
    temperature: float | None,
    analysis=None,
    *,
    location_text: str | None = None,
) -> frozenset[str]:
    analysis = analysis or analyse_semantics(text)
    has_command_marker = bool(analysis.values(SemanticKind.COMMAND_MARKER))
    # Natural voice commands are often elliptical ("Küchenlicht bitte an",
    # "Rollladen nach oben") or expressed as a wish/passive construction.
    # Action + resolvable target is sufficient unless the utterance is an
    # indicative state statement; candidate resolution below remains the
    # safety boundary and never guesses an entity.
    actions = frozenset(
        span.value
        for span in analysis.matching(SemanticKind.ACTION)
        if not (
            location_text is not None
            and location_text.casefold() in {"oben", "unten"}
            and span.text.casefold() == location_text.casefold()
        )
    )
    # A separated German particle carries the decisive lock operation:
    # ``schließe die Tür auf/ab``.  The generic stem ``schließe`` is also a
    # CLOSE token, so retain only the explicit lock operation in that domain.
    if "lock" in domains:
        if "unlock" in actions:
            actions = frozenset({"unlock"})
        elif "lock" in actions:
            actions = frozenset({"lock"})
    elliptical_request = (
        bool(actions)
        and (
            _DIRECTIVE_RE.search(text) is not None
            or _COPULA_STATEMENT_RE.search(text) is None
        )
    ) or (
        (percent is not None or temperature is not None)
        and _DIRECTIVE_RE.search(text) is not None
    )
    if not has_command_marker and not elliptical_request:
        return frozenset()
    found: set[str] = set()
    if percent is not None:
        found.update(
            intent
            for domain in domains
            for property_name in ("position", "brightness")
            if (
                intent := VALUE_INTENT_BY_DOMAIN_PROPERTY.get(
                    (domain, property_name)
                )
            ) is not None
        )
    if temperature is not None:
        found.update(
            intent
            for domain in domains
            if (
                intent := VALUE_INTENT_BY_DOMAIN_PROPERTY.get(
                    (domain, "temperature")
                )
            ) is not None
        )
    for domain in domains:
        for action in actions:
            if percent is not None and action in {"open", "close"}:
                continue
            if (
                domain == "script"
                and action == "turn_on"
                and re.search(r"\baktivier", text, re.I) is None
            ):
                continue
            intent = INTENT_BY_DOMAIN_ACTION.get((domain, str(action)))
            if intent is not None:
                found.add(intent)
    return frozenset(found)


def _entity_candidates(
    text: str,
    entities: list[EntitySnapshot],
    domains: frozenset[str],
    *,
    area_id: str | None = None,
    floor_id: str | None = None,
    world_model: WorldModel | None = None,
) -> list[EntitySnapshot]:
    ignored = frozenset(SEMANTIC_RESOLUTION_WORDS | _STOP_WORDS)
    ranked = rank_semantic_targets(
        text,
        entities,
        domains=domains,
        area_id=area_id,
        floor_id=floor_id,
        ignored_tokens=ignored,
        index=world_model.entity_index if world_model is not None else None,
    )
    if not ranked:
        return []
    top = ranked[0].score
    return [
        item.entity
        for item in ranked
        if top - item.score <= V7_ENTITY_AMBIGUITY_MARGIN
    ]


def _has_distinctive_explicit_target(
    text: str,
    entity: EntitySnapshot,
    analysis: SemanticAnalysis,
) -> bool:
    """Whether an entity mention says more than its generic device noun.

    A registry entity named only ``Rolllade`` must not turn that generic noun
    into stronger identity evidence than the current voice-source area.  A
    name such as ``Rolllade Gäste WC`` remains an explicit remote target.
    """

    generic_tokens = {
        normalize_for_compare(word)
        for words in DOMAIN_WORDS.values()
        for word in words
    }
    generic_tokens.update(
        normalize_for_compare(token)
        for span in analysis.matching(SemanticKind.DOMAIN)
        for token in _TOKEN_RE.findall(span.text)
    )
    normalized_text = normalize_for_compare(text)
    for name in (
        entity.friendly_name,
        *entity.aliases,
        *(alias.text for alias in generate_aliases(entity)),
    ):
        normalized_name = normalize_for_compare(name)
        if not normalized_name or re.search(
            rf"(?<!\w){re.escape(normalized_name)}(?!\w)", normalized_text
        ) is None:
            continue
        name_tokens = {
            normalize_for_compare(token) for token in _TOKEN_RE.findall(name)
        }
        if name_tokens - generic_tokens:
            return True
    return False


class SemanticCommandCompiler:
    """Compile a direct command from semantic facts and live HA constraints."""

    @staticmethod
    def compile(
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        analysis: SemanticAnalysis | None = None,
        context: UnderstandingContext | None = None,
    ) -> ParseResult | ClarificationRequest | None:
        if _QUESTION_RE.search(text):
            return None
        temporal = _temporal_expression(text)
        if temporal is not None:
            stripped, expression = temporal
            compiled = SemanticCommandCompiler.compile(
                stripped, entities, world_model, context=context
            )
            if isinstance(compiled, ParseResult):
                return replace(
                    compiled,
                    frame=replace(
                        compiled.frame,
                        source_text=text,
                        parameters={
                            **compiled.frame.parameters,
                            "temporal": expression,
                        },
                    ),
                )
            return None
        light_color = _compile_light_color(text, entities, world_model)
        if light_color is not None:
            return light_color
        fan_speed = _compile_fan_speed(text, entities, world_model)
        if fan_speed is not None:
            return fan_speed
        relative_light = _compile_relative_light(text, entities, world_model)
        if relative_light is not None:
            return relative_light
        relative_climate = _compile_relative_climate(text, entities, world_model)
        if relative_climate is not None:
            return relative_climate
        lock_by_part = _compile_lock_by_name_part(text, entities)
        if lock_by_part is not None:
            return lock_by_part
        if _SETUP_WITHOUT_VALUE_RE.search(text) and not re.search(r"\d", text):
            # "Stelle die Heizung im Büro ein" asks for a value ("einstellen"),
            # it does not mean "switch on": leave it to the value dialog (F11).
            climates = [entity for entity in entities if entity.domain == "climate"]
            if mentioned_entities(text, climates) or climate_in_named_area(text, climates):
                return None
        if _ORDERED_SUBSET_RE.search(text):
            return None
        positive_text, exclusion_names = _split_exclusion(text)
        # Analyse only the positive clause: entity names after ``außer`` are
        # registry data, not command semantics, and must not introduce a
        # second domain/action into the requested operation.
        analysis = analyse_semantics(positive_text) if exclusion_names else (
            analysis or analyse_semantics(text)
        )
        spoken_percent = _ANY_PERCENT_RE.search(positive_text)
        if spoken_percent is not None and int(spoken_percent.group("value")) > 100:
            return None
        spoken_temperature = _TEMPERATURE_RE.search(positive_text)
        if spoken_temperature is not None and not (
            5
            <= float(spoken_temperature.group("value").replace(",", "."))
            <= 30
        ):
            return None

        domains = frozenset(
            value for value in analysis.values(SemanticKind.DOMAIN)
            if isinstance(value, str)
        )
        whole_home = whole_home_phrase(positive_text, entities, world_model)
        resolution_text = re.sub(
            r"\b(?:im|in\s+der|in\s+dem)\s+",
            "",
            positive_text.replace(whole_home, " ") if whole_home else positive_text,
            flags=re.I,
        )
        quantity = _quantity(positive_text)
        if quantity is None and whole_home is not None and not mentioned_entities(
            resolution_text,
            entities,
            index=world_model.entity_index if world_model is not None else None,
        ):
            # "Mach überall das Licht aus", "im ganzen Haus das Licht" (F21):
            # a whole-home scope is universal. The word "Haus" is scope, not
            # part of a device name such as "Stromverbrauch Haus"; a named
            # device still wins below, and the policy's target limit applies.
            quantity = Quantifier("all")
        # A quantified target (``alle Lichter im Wohnzimmer``) is resolved
        # by typed domain and location. Scanning thousands of registry names
        # cannot strengthen that meaning and used to dominate the 5k gate.
        explicit = (
            ()
            if quantity is not None
            else mentioned_entities(
                resolution_text,
                entities,
                index=world_model.entity_index if world_model is not None else None,
            )
        )
        distinctive_explicit_target = (
            len(explicit) == 1
            and _has_distinctive_explicit_target(
                positive_text, explicit[0], analysis
            )
        )
        coordinated_locations = resolve_coordinated_locations(
            positive_text, entities, world_model
        )
        location = (
            None
            if coordinated_locations
            else resolve_semantic_location(positive_text, entities, world_model)
        )
        # A room-like word can legitimately be part of an exact registry
        # name (``Heizung Wohnzimmer``, ``Rollladen Schlafzimmer``). Without
        # an explicit location preposition it describes that entity, not an
        # independent area constraint that may point at another device.
        if (
            location is not None
            and len(explicit) == 1
            and _LOCATION_CUE_RE.search(positive_text) is None
            and normalize_for_compare(location[0])
            in normalize_for_compare(explicit[0].friendly_name).split()
        ):
            location = None
        locations = coordinated_locations or ((location,) if location else ())
        source_area = context.source_area if context is not None else None
        source_area_applied = (
            source_area is not None
            and not locations
            and quantity is None
            and not distinctive_explicit_target
        )
        resolution_area_id = (
            location[1]
            if location is not None
            else source_area.area_id
            if source_area_applied and source_area is not None
            else None
        )
        resolution_floor_id = location[2] if location is not None else None
        # A spoken scope is a hard constraint.  If HA does not know it (or
        # two floors make "oben" ambiguous), never silently widen the
        # request to every device in the house.
        if (
            not locations
            and len(explicit) != 1
            and has_explicit_location_cue(positive_text, entities)
        ):
            return None
        temperature = _temperature(positive_text)
        # ``auf 22 Grad`` and ``auf 22 Prozent`` share the same numeric
        # preposition.  The explicit temperature unit owns the value and
        # prevents the bare-percentage shorthand from creating a second
        # intent.
        percent = None if temperature is not None else _percent(positive_text)
        if spoken_percent is None and (
            _ZERO_RE.search(positive_text) or _HUNDRED_RE.search(positive_text)
        ):
            # ``ganz auf/zu`` is the ordinary endpoint operation, not a
            # competing set-position interpretation.
            percent = None

        # An explicit complete registry name owns its real HA domain even if
        # the name itself contains another generic noun (for example a switch
        # named ``Licht Sportraum``). This prevents lexical words inside user
        # data from overriding the registry's typed identity.
        if len(explicit) == 1 and (
            not source_area_applied or distinctive_explicit_target
        ):
            domains = frozenset({explicit[0].domain})
        elif not domains:
            preliminary = _entity_candidates(
                positive_text,
                entities,
                domains,
                area_id=resolution_area_id,
                floor_id=resolution_floor_id,
                world_model=world_model,
            )
            if preliminary:
                domains = frozenset(entity.domain for entity in preliminary)

        intents = _intents(
            positive_text,
            domains,
            percent,
            temperature,
            analysis,
            location_text=location[0] if location else None,
        )
        facts = SemanticFacts(
            intents=intents,
            domains=domains,
            percent=percent,
            temperature=temperature,
            quantity=quantity,
            location_text=" und ".join(item[0] for item in locations) if locations else None,
            area_id=resolution_area_id,
            floor_id=resolution_floor_id,
        )
        if len(facts.intents) != 1 or len(facts.domains) != 1:
            return None
        intent = next(iter(facts.intents))
        domain = next(iter(facts.domains))
        if intent == "HassSetPercentage" and domain not in {"cover", "light"}:
            return None

        if (
            quantity is None
            and domain == "light"
            and not explicit
            and len(locations) == 1
            and locations[0][1] is not None
            and _ROOM_LIGHT_RE.search(positive_text) is not None
            and sum(
                1 for entity in entities
                if entity.domain == "light" and entity.area_id == locations[0][1]
            ) > 1
        ):
            # Documented rule (F20): "das Licht im <Raum>" means every light
            # of that room, like Home Assistant's own agent; the target limit
            # of the execution policy still applies. A named device or
            # "die Lampe" keeps selecting (and asking about) one device.
            quantity = Quantifier("all")

        if quantity is not None:
            if exclusion_names and quantity.kind in {"both", "count"}:
                # "genau drei außer X" has two plausible counts (before or
                # after exclusion). Refuse instead of choosing one silently.
                return None
            matches = _scoped_candidates(entities, domain, locations, world_model)
            if percent is not None:
                capability = "POSITION" if domain == "cover" else "BRIGHTNESS"
                matches = [entity for entity in matches if capability in entity.capabilities]
            if not matches:
                return None
            if quantity.kind == "both" and len(matches) != 2:
                return None
            if quantity.kind == "count" and len(matches) != quantity.value:
                return None
            exclusion_result = _resolve_exclusions(
                exclusion_names,
                entities,
                matches,
                world_model.entity_index if world_model is not None else None,
            )
            if exclusion_result is None:
                return None
            matches, excluded_names, excluded_entity_ids = exclusion_result
            target = TargetReference(text=domain, domain=domain)
            area = (
                AreaReference(text=facts.location_text or "", area_id=facts.area_id)
                if facts.area_id is not None and len(locations) == 1 else None
            )
        else:
            if exclusion_names or len(locations) > 1:
                return None
            ranked_candidates = rank_semantic_targets(
                resolution_text,
                entities,
                domains=facts.domains,
                area_id=facts.area_id,
                floor_id=facts.floor_id,
                ignored_tokens=frozenset(SEMANTIC_RESOLUTION_WORDS | _STOP_WORDS),
                index=world_model.entity_index if world_model is not None else None,
            )
            if ranked_candidates:
                top_score = ranked_candidates[0].score
                selected_ranked = [
                    item
                    for item in ranked_candidates
                    if top_score - item.score <= V7_ENTITY_AMBIGUITY_MARGIN
                ]
                candidates = [item.entity for item in selected_ranked]
            elif source_area_applied:
                selected_ranked = []
                candidates = list(
                    world_model.select_entities(
                        domain=domain, area_id=facts.area_id
                    )
                    if world_model is not None
                    else resolve_candidates(
                        entities,
                        Constraints(domain=domain, area_id=facts.area_id),
                    )
                )
                if percent is not None:
                    capability = "POSITION" if domain == "cover" else "BRIGHTNESS"
                    candidates = [
                        entity
                        for entity in candidates
                        if capability in entity.capabilities
                    ]
            else:
                selected_ranked = []
                candidates = []
            if len(candidates) > 1:
                return ClarificationRequest(
                    intent,
                    domain,
                    tuple(candidates),
                    {
                        **({"percent": percent} if percent is not None else {}),
                        **({"temperature": temperature} if temperature is not None else {}),
                    },
                )
            if selected_ranked and selected_ranked[0].source == "fuzzy":
                return ClarificationRequest(
                    intent,
                    domain,
                    tuple(candidates),
                    {
                        **({"percent": percent} if percent is not None else {}),
                        **({"temperature": temperature} if temperature is not None else {}),
                    },
                )
            if len(candidates) != 1:
                return None
            matches = candidates
            entity = matches[0]
            target = TargetReference(entity.friendly_name, entity.entity_id, entity.domain)
            area = (
                AreaReference(
                    text=source_area.name,
                    area_id=source_area.area_id,
                    area_name=source_area.name,
                )
                if source_area_applied and source_area is not None
                else None
            )

        if intent == "HassClimateSetTemperature" and temperature is not None:
            try:
                if any(
                    not float(entity.attributes.get("min_temp", 7))
                    <= temperature
                    <= float(entity.attributes.get("max_temp", 35))
                    for entity in matches
                ):
                    return None
            except (TypeError, ValueError):
                return None

        registry_texts = [facts.location_text or ""]
        if quantity is None:
            for entity in matches:
                registry_texts.extend((
                    entity.friendly_name,
                    entity.area_name or "",
                    entity.floor_name or "",
                    *(alias.text for alias in generate_aliases(entity)),
                ))
        else:
            # Quantified commands are grounded by domain and location rather
            # than by every individual registry name.  Tokenizing hundreds of
            # matched entity names adds no evidence and makes the safety check
            # scale with the result size.  Explicit exclusions remain valid
            # dynamic vocabulary and therefore stay part of the check.
            registry_texts.extend(exclusion_names)
        if _has_unexplained_meaning(analysis, registry_texts):
            fuzzy = resolve_entity_scored(
                " ".join(analysis.unexplained_tokens),
                [entity for entity in entities if entity.domain == domain],
                area_id=facts.area_id,
                domain=domain,
            )
            if fuzzy.status in {
                ResolutionStatus.AMBIGUOUS,
                ResolutionStatus.CONFIRMATION_REQUIRED,
            } and fuzzy.candidates:
                return ClarificationRequest(
                    intent,
                    domain,
                    fuzzy.candidates,
                    {
                        **({"percent": percent} if percent is not None else {}),
                        **({"temperature": temperature} if temperature is not None else {}),
                    },
                    (
                        fuzzy.candidate_set.ranked
                        if fuzzy.candidate_set is not None
                        else ()
                    ),
                )
            if _has_registry_typo(analysis, matches):
                return ClarificationRequest(
                    intent,
                    domain,
                    tuple(matches),
                    {
                        **({"percent": percent} if percent is not None else {}),
                        **(
                            {"temperature": temperature}
                            if temperature is not None
                            else {}
                        ),
                    },
                )
            return None

        action = {
            "HassTurnOn": SemanticAction.TURN_ON,
            "HassTurnOff": SemanticAction.TURN_OFF,
            "HassToggle": SemanticAction.TOGGLE,
            "HassOpenCover": SemanticAction.OPEN,
            "HassCloseCover": SemanticAction.CLOSE,
            "HassRunScript": SemanticAction.TURN_ON,
            "HassActivateScene": SemanticAction.TURN_ON,
            "HassMediaPlay": SemanticAction.START,
            "HassMediaPause": SemanticAction.PAUSE,
            "HassMediaStop": SemanticAction.STOP,
            "HassMediaMute": SemanticAction.MUTE,
            "HassVacuumStart": SemanticAction.START,
            "HassVacuumStop": SemanticAction.STOP,
            "HassVacuumLocate": SemanticAction.LOCATE,
            "HassPressButton": SemanticAction.PRESS,
            "HassOpenValve": SemanticAction.OPEN,
            "HassCloseValve": SemanticAction.CLOSE,
            "HassLock": SemanticAction.LOCK,
            "HassUnlock": SemanticAction.UNLOCK,
            "HassSetPercentage": SemanticAction.SET,
            "HassClimateSetTemperature": SemanticAction.SET,
        }[intent]
        property_ = None
        if intent == "HassSetPercentage":
            property_ = SemanticProperty.POSITION if domain == "cover" else SemanticProperty.BRIGHTNESS
        elif intent == "HassClimateSetTemperature":
            property_ = SemanticProperty.TEMPERATURE
        semantic_quantity = None
        if quantity is not None:
            semantic_quantity = (
                SemanticQuantity.exactly(2) if quantity.kind == "both"
                else SemanticQuantity.exactly(quantity.value) if quantity.kind == "count" and quantity.value is not None
                else SemanticQuantity.all()
            )
            if exclusion_names:
                semantic_quantity = SemanticQuantity.exclude(*excluded_names)
        comparison_value = percent if percent is not None else temperature
        return ParseResult(
            frame=SemanticFrame(
                intent=intent,
                target=target,
                area=area,
                quantifier=quantity,
                parameters={
                    **({"percent": percent} if percent is not None else {}),
                    **({"temperature": temperature} if temperature is not None else {}),
                    **({
                        "comparison": Comparison(
                            operator=str(next(iter(analysis.values(SemanticKind.COMPARATOR)))),
                            value=float(comparison_value),
                        )
                    } if analysis.values(SemanticKind.COMPARATOR)
                    and comparison_value is not None else {}),
                    **({"excluded": excluded_names} if exclusion_names else {}),
                    **(
                        {"excluded_entity_ids": excluded_entity_ids}
                        if exclusion_names
                        else {}
                    ),
                    **({
                        "locations": tuple(
                            {"text": spoken, "area_id": area_id, "floor_id": floor_id}
                            for spoken, area_id, floor_id in locations
                        )
                    } if len(locations) > 1 else {}),
                },
                source_text=text,
                action=action,
                property=property_,
                quantity=semantic_quantity,
            ),
            resolved_entities=matches,
        )


class SemanticQueryCompiler:
    """Compile read-only plural queries from freely ordered semantic facts."""

    @staticmethod
    def compile(
        text: str,
        entities: list[EntitySnapshot],
        world_model: WorldModel | None = None,
        analysis: SemanticAnalysis | None = None,
    ) -> ParseResult | None:
        # A question mark alone also supports terse chat-style questions such
        # as "Offene Fenster im Erdgeschoss?".  An imperative verb always
        # wins the command interpretation and is never converted to a query.
        analysis = analysis or analyse_semantics(text)
        speech_act = analyse_utterance(text).speech_act
        if speech_act is not SpeechAct.QUERY and not (
            speech_act is SpeechAct.STATEMENT
            and analysis.values(SemanticKind.PROPERTY)
        ):
            return None
        device_query = _compile_device_query(text, entities, analysis, world_model)
        if device_query is not None:
            return device_query
        if re.search(r"^\s*wie\s+hoch\s+ist\b", text, re.I) is None:
            entity_state_query = _compile_entity_state_query(
                text, entities, analysis, world_model
            )
            if entity_state_query is not None:
                return entity_state_query
        if analysis.values(SemanticKind.COMPARATOR):
            return _compile_comparison_query(text, entities, analysis, world_model)
        if analysis.values(SemanticKind.PROPERTY):
            measurement = _compile_measurement_query(
                text, entities, analysis, world_model
            )
            if measurement is not None:
                return measurement
            property_mentions = mentioned_entities(
                text,
                entities,
                index=(world_model.entity_index if world_model is not None else None),
            )
            if any(
                entity.domain not in {"sensor", "light"}
                for entity in property_mentions
            ):
                return None
        if re.search(r"^\s*wie\s+(?:hoch|warm)\s+ist\b", text, re.I):
            inferred_measurement = _compile_measurement_query(
                text,
                entities,
                analysis,
                world_model,
                inferred_property="temperature",
            )
            if inferred_measurement is not None:
                return inferred_measurement
            if re.search(r"^\s*wie\s+hoch\s+ist\b", text, re.I):
                named_high = mentioned_entities(
                    text,
                    entities,
                    index=(world_model.entity_index if world_model is not None else None),
                )
                if len(named_high) == 1 and named_high[0].domain == "cover":
                    return None
        # A uniquely named sensor/light may be queried without repeating a
        # generic property noun (``Wie hoch ist Außentemperatur?``). Registry
        # identity is the complete target; this path is read-only by type.
        named_query = re.search(
            r"^\s*(?:wie\s+(?:hoch|warm|ist)|was\s+zeigt|"
            r"welchen\s+(?:status|zustand)|(?:der\s+)?status\s+von)\b",
            text,
            re.I,
        )
        if (
            named_query is not None
            and not analysis.values(SemanticKind.COMMAND_MARKER)
        ):
            named = mentioned_entities(
                text,
                entities,
                index=world_model.entity_index if world_model is not None else None,
            )
            if len(named) == 1 and named[0].domain in {"sensor", "light"}:
                entity = named[0]
                return ParseResult(
                    frame=SemanticFrame(
                        intent="HassGetState",
                        target=TargetReference(
                            entity.friendly_name, entity.entity_id, entity.domain
                        ),
                        area=None,
                        source_text=text,
                        action=SemanticAction.QUERY,
                    ),
                    resolved_entities=[entity],
                )
        if (
            not (_QUERY_MARKER_RE.search(text) or text.rstrip().endswith("?"))
            or analysis.values(SemanticKind.COMPARATOR)
            or _DURATION_QUESTION_RE.search(text)
        ):
            return None

        targets = _device_class_targets(analysis)
        targets.extend(
            (domain, None)
            for domain in analysis.values(SemanticKind.DOMAIN)
            if isinstance(domain, str)
        )
        targets = list(dict.fromkeys(targets))
        named_mentions: tuple[EntitySnapshot, ...] = ()
        needs_named_resolution = (
            not targets
            or _SINGULAR_NAMED_QUERY_RE.search(text) is not None
            or _BARE_NAMED_STATE_QUERY_RE.search(text) is not None
        )
        if needs_named_resolution:
            named_mentions = mentioned_entities(
                text,
                entities,
                index=(world_model.entity_index if world_model is not None else None),
            )
        if (
            len(named_mentions) == 1
            and _SINGULAR_NAMED_QUERY_RE.search(text) is not None
            and (named_mentions[0].domain, named_mentions[0].device_class) not in targets
            and all(
                domain != named_mentions[0].domain for domain, _device_class in targets
            )
            and normalize_for_compare(named_mentions[0].friendly_name)
            not in {
                normalize_for_compare(name)
                for entity in entities
                for name in (entity.area_name or "", *entity.area_aliases)
                if name
            }
        ):
            # "Ist das Garagentor offen?" names one entity (a cover) while the
            # class word "Garagentor" means a garage-door contact: the named
            # entity decides the question (F12).
            targets = [(named_mentions[0].domain, named_mentions[0].device_class)]
        if not targets:
            if named_mentions:
                named_domains = {entity.domain for entity in named_mentions}
                named_device_classes = {
                    entity.device_class for entity in named_mentions
                }
                if len(named_domains) == 1 and len(named_device_classes) == 1:
                    targets.append(
                        (
                            next(iter(named_domains)),
                            next(iter(named_device_classes)),
                        )
                    )
        exists_question = (
            "exists" in analysis.values(SemanticKind.QUERY_SCOPE)
            or _INDEFINITE_EXISTS_RE.search(text) is not None
        ) and _WH_LIST_QUESTION_RE.search(text) is None
        # "Welche Rollläden gibt es ...?" asks for the names, not a yes/no
        # existence answer (F12): it is a list question.
        states = [
            span.value
            for span in analysis.matching(SemanticKind.STATE)
            if isinstance(span.value, SemanticState)
            and not (exists_question and span.text.casefold() in {"ein", "eine"})
        ]
        location = resolve_semantic_location(text, entities, world_model)
        if not targets and len(states) == 1 and location is not None:
            requested = states[0]
            eligible = [
                entity
                for entity in entities
                if entity.domain in QUERYABLE_STATE_DOMAINS
                and (location[1] is None or entity.area_id == location[1])
                and (location[2] is None or entity.floor_id == location[2])
                and supports_state_predicate(
                    entity.domain, requested, entity.device_class
                )
            ]
            eligible_domains = {entity.domain for entity in eligible}
            if len(eligible_domains) == 1:
                targets.append((next(iter(eligible_domains)), None))
        if len(targets) != 1 or len(states) > 1:
            return None
        domain, device_class = targets[0]
        if domain not in QUERYABLE_STATE_DOMAINS:
            return None
        requested_state = states[0] if states else None
        if requested_state is not None and not supports_state_predicate(
            domain, requested_state, device_class
        ):
            return None

        coordinated_locations = resolve_coordinated_locations(
            text, entities, world_model
        )
        location = (
            None
            if coordinated_locations
            else location or resolve_semantic_location(text, entities, world_model)
        )
        locations = coordinated_locations or ((location,) if location else ())
        has_location_cue = _LOCATION_CUE_RE.search(text) is not None
        if has_location_cue and not locations:
            return None
        # Unknown modifiers may materially change a question (for example
        # "normalerweise", "gestern" or "nicht").  Do not pretend they were
        # understood. Dynamic registry location and entity names are removed
        # from this check because they cannot live in the lexicon.
        registry_texts = [item[0] for item in locations]
        # Resolve the typed location before attempting the O(n) registry-name
        # scan. Area/floor names intentionally arrive as unexplained lexical
        # tokens; once they are accounted for, a plural category query does
        # not need to compare its text with every entity name. If meaningful
        # residue remains, retain the established safe named-entity fallback.
        if (
            not named_mentions
            and analysis.unexplained_tokens
            and _has_unexplained_meaning(analysis, tuple(registry_texts))
        ):
            named_mentions = mentioned_entities(
                text,
                entities,
                index=(world_model.entity_index if world_model is not None else None),
            )
        for entity in named_mentions:
            registry_texts.extend((entity.friendly_name, *entity.aliases))
        if _has_unexplained_meaning(
            analysis, tuple(registry_texts)
        ):
            return None
        area_id = location[1] if location else None
        floor_id = location[2] if location else None
        location_name = location[0] if location else None

        named_candidates = [
            entity
            for entity in named_mentions
            if entity.domain == domain
            and (device_class is None or entity.device_class == device_class)
        ]
        if named_candidates:
            candidates = named_candidates
        elif len(locations) > 1:
            candidates = _scoped_candidates(entities, domain, locations, world_model)
            if device_class is not None:
                candidates = [
                    entity for entity in candidates
                    if entity.device_class == device_class
                ]
        else:
            candidates = list(world_model.select_entities(
                domain=domain,
                device_class=device_class,
                area_id=area_id,
                floor_id=floor_id,
            )) if world_model is not None else resolve_candidates(
                entities, Constraints(
                    domain=domain,
                    device_class=device_class,
                    area_id=area_id,
                    floor_id=floor_id,
                ),
            )
        # The target category itself must exist. An empty *state match* is a
        # valid answer, but inventing an answer for a home with no such
        # selected entities would hide configuration errors.
        if not candidates:
            category_exists = any(
                entity.domain == domain
                and (device_class is None or entity.device_class == device_class)
                for entity in entities
            )
            if not locations or not category_exists:
                return None

        scopes = analysis.values(SemanticKind.QUERY_SCOPE)
        quantities = analysis.values(SemanticKind.QUANTIFIER)
        singular_state_question = (
            _SINGULAR_NAMED_QUERY_RE.search(text) is not None
            or (
                _BARE_NAMED_STATE_QUERY_RE.search(text) is not None
                and len(named_candidates) == 1
            )
        )
        if singular_state_question:
            if len(candidates) != 1 or (
                requested_state is None
                and _BARE_NAMED_STATE_QUERY_RE.search(text) is None
            ):
                return None
            scope = QueryScope.SINGLE
        elif "none" in scopes:
            if requested_state is None:
                return None
            scope = QueryScope.NONE
        elif "all" in quantities or "both" in quantities:
            if requested_state is None:
                return None
            if "both" in quantities and len(candidates) != 2:
                return None
            scope = QueryScope.ALL
        elif "count" in scopes:
            scope = QueryScope.COUNT
        elif "locations" in scopes:
            scope = QueryScope.LOCATIONS
        elif exists_question:
            scope = QueryScope.EXISTS
        else:
            scope = QueryScope.LIST

        intent = (
            "HassCheckState"
            if scope is QueryScope.SINGLE
            else "HassExistsQuery" if scope is QueryScope.EXISTS else "HassStateQuery"
        )
        area_snapshot = (
            AreaSnapshot(area_id=area_id, name=location_name)
            if area_id is not None and location_name is not None and len(locations) == 1
            else None
        )
        query_command = QueryCommand(
            intent=intent,
            scope=scope,
            target=QueryTarget(
                domain=domain,
                device_class=device_class,
                area=area_snapshot,
                floor_id=floor_id,
                entity_id=(candidates[0].entity_id if scope is QueryScope.SINGLE else None),
            ),
            filter=QueryFilter(state=requested_state),
        )
        query_result = _QUERY_EXECUTOR.execute(query_command, candidates)

        semantic_quantity = (
            SemanticQuantity.exactly(2)
            if "both" in quantities
            else SemanticQuantity.all()
        )
        return ParseResult(
            frame=SemanticFrame(
                intent=intent,
                target=TargetReference(
                    text=device_class or domain,
                    domain=domain,
                    device_class=device_class,
                ),
                area=(
                    AreaReference(
                        text=location_name,
                        area_id=area_id,
                        area_name=location_name,
                    )
                    if area_id is not None and location_name is not None
                    else None
                ),
                quantifier=Quantifier("all"),
                parameters={
                    "semantic_state": requested_state,
                    "count_only": scope is QueryScope.COUNT,
                    "all_requested": scope is QueryScope.ALL,
                    "none_requested": scope is QueryScope.NONE,
                    "locations_requested": scope is QueryScope.LOCATIONS,
                    "device_class": device_class,
                    "domain": domain,
                    "floor_id": floor_id,
                    "floor_name": location_name if floor_id is not None else None,
                    "locations": tuple(
                        {"text": spoken, "area_id": item_area_id, "floor_id": item_floor_id}
                        for spoken, item_area_id, item_floor_id in locations
                    ) if len(locations) > 1 else (),
                    "query_command": query_command,
                    "query_result": query_result,
                },
                source_text=text,
                action=SemanticAction.QUERY,
                quantity=semantic_quantity,
            ),
            resolved_entities=list(query_result.entities),
        )
