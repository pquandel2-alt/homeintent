"""Genus-based command compiler over the shared language document.

This compiler composes one command from independent meaning components of a
``LanguageDocument`` clause:

* an operation (lexical ACTION, a comparative degree such as "leiser", a
  percentage or a temperature),
* one or more target descriptions (genus × place × feature × quantity,
  ``target_resolution``),

and grounds them against the live registry.  Operations map to executable
intents per device domain through ``ONTOLOGY_OPERATIONS``/
``DEGREE_OPERATIONS`` (data in ``semantic_catalog``), so a new genus or a new
operation word works in every combination without a sentence template.

The compiler never executes.  It returns typed ``ParseResult`` objects that
run through the ordinary validator, execution policy and confirmation flow;
a preview is requested for group operations over several device kinds or
more than ``GROUP_PREVIEW_THRESHOLD`` targets.  Critical kinds (locks,
alarm, garage doors) and heating never join an unspecific "alles".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..areas import AreaSnapshot
from ..entities import EntitySnapshot, normalize_for_compare
from ..service_call import REGISTERED_OPERATION_INTENT
from .degree_semantics import extract_degree
from .device_ontology import GENERA, entity_genera, genus
from .frame import AreaReference, Quantifier, SemanticFrame, TargetReference
from .german_structure import ClauseKind
from .language_frontend import tokenize_language
from .normalize import normalize
from .parser import ClarificationRequest, ParseResult
from .place_model import Place, PlaceKind, build_place_lexicon
from .primitives import SemanticAction, SemanticDirection, SemanticProperty
from .semantic_catalog import (
    DEGREE_OPERATIONS,
    DEGREE_WORDS,
    GROUP_PREVIEW_THRESHOLD,
    ONTOLOGY_OPERATIONS,
    PROPERTY_GENUS,
    TIME_BOUND_WORDS,
    OperationTarget,
)
from .semantic_compiler import _percent, _temperature
from .semantic_lexicon import SemanticKind, analyse_semantics
from .target_resolution import (
    Quantity,
    ResolutionOutcome,
    TargetDescription,
    TargetResolution,
    _name_index,
    describe_with_residue,
    resolve_description,
)
from .utterance_meaning import segment_clauses

__all__ = ("OntologyCommand", "compile_ontology_command")


_ACTION_TO_SEMANTIC = {
    "turn_on": SemanticAction.TURN_ON,
    "turn_off": SemanticAction.TURN_OFF,
    "toggle": SemanticAction.TOGGLE,
    "open": SemanticAction.OPEN,
    "close": SemanticAction.CLOSE,
    "start": SemanticAction.START,
    "play": SemanticAction.START,
    "pause": SemanticAction.PAUSE,
    "stop": SemanticAction.STOP,
    "mute": SemanticAction.MUTE,
    "locate": SemanticAction.LOCATE,
    "press": SemanticAction.PRESS,
    "lock": SemanticAction.LOCK,
    "unlock": SemanticAction.UNLOCK,
}
_PROPERTY = {
    "brightness": SemanticProperty.BRIGHTNESS,
    "temperature": SemanticProperty.TEMPERATURE,
    "volume": SemanticProperty.VOLUME,
    "speed": SemanticProperty.FAN_SPEED,
}
_FILLER_WORDS = frozenset({
    "etwas", "bisschen", "ein", "wenig", "bitte", "mal", "prozent", "grad",
    "noch", "mehr", "viel", "deutlich", "ganz", "wieder", "sofort", "jetzt",
    "gleich", "kurz", "schnell", "auch", "dann", "danach", "anschliessend",
})
_MAX_CLARIFICATION_CANDIDATES = 8
_EVERYDAY_GENERA = frozenset(item.key for item in GENERA if item.in_everything)
# Kinds that "alles" never switches silently; they are named in the preview.
_KEPT_BY_EVERYTHING_DOMAINS = frozenset({"climate", "lock", "alarm_control_panel", "water_heater", "valve"})


@dataclass(frozen=True)
class ClauseMeaning:
    """Operation and targets of one coordinated clause."""

    text: str
    actions: frozenset[str]
    degree: tuple[str, int] | None
    percent: int | None
    temperature: float | None
    descriptions: tuple[TargetDescription, ...]
    residue: tuple[str, ...] = ()

    @property
    def has_operation(self) -> bool:
        return bool(
            self.actions or self.degree or self.percent is not None
            or self.temperature is not None
        )


@dataclass(frozen=True)
class OntologyCommand:
    """Outcome of the genus compiler for one utterance."""

    results: tuple[ParseResult, ...] = ()
    clarification: ClarificationRequest | None = None
    message: str | None = None
    preview: str | None = None
    kept: tuple[EntitySnapshot, ...] = ()
    clauses: int = 1

    @property
    def executable(self) -> bool:
        return bool(self.results) and self.clarification is None and self.message is None


def _operation_words(analysis_text: str) -> tuple[frozenset[str], frozenset[str]]:
    """ACTION values and the words that expressed an operation."""
    analysis = analyse_semantics(analysis_text)
    actions: set[str] = set()
    words: set[str] = set()
    for span in analysis.spans:
        if span.kind not in {SemanticKind.ACTION, SemanticKind.COMMAND_MARKER}:
            continue
        parts = normalize_for_compare(span.text).split()
        if span.kind is SemanticKind.ACTION:
            actions.add(str(span.value))
        # Discontinuous verb frames ("mach ... auf") only own their first
        # and last word; the object in between stays a target.
        words.update({parts[0], parts[-1]} if len(parts) > 1 else set(parts))
    return frozenset(actions), frozenset(words)


_SUBORDINATING_OR_EXCEPTING = frozenset({
    "dass", "ob", "wo", "wohin", "woher", "weil", "damit", "obwohl", "nachdem",
    "bevor", "welche", "welcher", "welches", "dessen", "deren", "ausser",
    "ausnahme", "ausgenommen", "sondern", "stattdessen", "nachricht",
    "benachrichtigung", "nachrichten",
})
_NON_COORDINATE_CLAUSES = frozenset({
    ClauseKind.RELATIVE, ClauseKind.REPAIR, ClauseKind.EXCLUSION,
    ClauseKind.CONDITION, ClauseKind.TEMPORAL,
})


def _clause_meanings(
    document: object, entities: Sequence[EntitySnapshot]
) -> tuple[ClauseMeaning, ...]:
    structure = getattr(document, "structure")
    if any(clause.kind in _NON_COORDINATE_CLAUSES for clause in structure.clauses):
        # Repairs ("äh nein, das Wohnzimmerlicht"), relative restrictions
        # (", die noch an sind"), exclusions and conditions have their own
        # dedicated semantics; this compiler only composes coordination.
        return ()
    # The normalized surface has shells and fillers removed ("Wäre es
    # möglich, ..." -> "bitte ...") while keeping every meaning word.
    source = getattr(getattr(document, "utterance"), "normalized_text")
    tokens = tokenize_language(source)
    words = [token.canonical for token in tokens if token.is_word]
    if any(word in _SUBORDINATING_OR_EXCEPTING for word in words):
        # Subordinate content ("…, dass das Essen fertig ist"), relational
        # clauses ("…, wo ein Fenster offen steht") and exceptions ("mit
        # Ausnahme von") belong to their dedicated compilers.
        return ()
    lexicon = build_place_lexicon(entities)
    names = _name_index(entities)
    meanings: list[ClauseMeaning] = []
    for start, end in segment_clauses(tokens):
        clause_tokens = tokens[start:end]
        if not any(token.is_word for token in clause_tokens):
            continue
        text = source[clause_tokens[0].start:clause_tokens[-1].end]
        normalized = normalize(text)
        actions, operation_words = _operation_words(normalized)
        degree_words = [
            DEGREE_WORDS[token.canonical]
            for token in clause_tokens
            if token.canonical in DEGREE_WORDS
        ]
        degree = degree_words[0] if len(set(degree_words)) == 1 else None
        temperature = _temperature(normalized)
        percent = None if temperature is not None else _percent(normalized)
        spoken_values = [
            int(clause_tokens[index].canonical)
            for index in range(len(clause_tokens) - 1)
            if clause_tokens[index].is_number
            and clause_tokens[index].canonical.isdigit()
            and clause_tokens[index + 1].canonical in {"prozent", "%"}
        ]
        if any(value > 100 for value in spoken_values):
            return ()
        if degree is not None and percent is not None and "%" not in text and "prozent" not in normalized.casefold():
            percent = None
        words_here = [token.canonical for token in clause_tokens if token.is_word]
        if (
            "ein" in words_here
            and any(word.startswith("stell") for word in words_here)
            and percent is None and temperature is None and degree is None
        ):
            # "Stelle die Heizung ein" asks to configure a value, it does not
            # mean "switch on" (existing contract F11).
            return ()
        ignore = frozenset(operation_words | _FILLER_WORDS | frozenset(DEGREE_WORDS))
        descriptions, residue = describe_with_residue(
            clause_tokens, entities, lexicon=lexicon, ignore=ignore, names=names
        )
        if residue:
            # An unexplained content word may change the meaning entirely;
            # never execute around it.  The clause is kept (with its
            # residue) so a multi-clause turn can name the part it did not
            # understand instead of silently dropping it (finding S3).
            meanings.append(ClauseMeaning(
                text=text, actions=actions, degree=degree, percent=percent,
                temperature=temperature, descriptions=descriptions,
                residue=residue,
            ))
            continue
        meanings.append(ClauseMeaning(
            text=text,
            actions=actions,
            degree=degree,
            percent=percent,
            temperature=temperature,
            descriptions=descriptions,
        ))
    # Coordinated objects share the following predicate:
    # "Mach das Licht und die Heizung aus" -> both clauses operate "aus".
    merged: list[ClauseMeaning] = []
    carried: list[TargetDescription] = []
    for meaning in meanings:
        if meaning.residue and not meaning.has_operation:
            merged.append(meaning)
            continue
        if not meaning.has_operation:
            if meaning.descriptions:
                carried.extend(meaning.descriptions)
                continue
            continue
        if carried:
            meaning = ClauseMeaning(
                meaning.text, meaning.actions, meaning.degree, meaning.percent,
                meaning.temperature, (*carried, *meaning.descriptions),
                meaning.residue,
            )
            carried = []
        merged.append(meaning)
    if carried and merged:
        last = merged[-1]
        merged[-1] = ClauseMeaning(
            last.text, last.actions, last.degree, last.percent, last.temperature,
            (*last.descriptions, *carried), last.residue,
        )
    return tuple(merged)


def _within_reported_range(entity: EntitySnapshot, temperature: float) -> bool:
    try:
        low = float(entity.attributes.get("min_temp", 5))
        high = float(entity.attributes.get("max_temp", 35))
    except (TypeError, ValueError):
        return False
    return low <= temperature <= high


def _operation_for(domain: str, clause: ClauseMeaning) -> tuple[OperationTarget, dict[str, object]] | None:
    """Executable operation of one clause for one device domain."""
    if clause.temperature is not None:
        return (("HassClimateSetTemperature",), {"temperature": clause.temperature}) if domain == "climate" else None
    if clause.percent is not None:
        if domain in {"cover", "light"}:
            return ("HassSetPercentage",), {"percent": clause.percent}
        if domain == "media_player":
            return ("svc", "media_player", "volume_set"), {"volume_level": clause.percent / 100}
        return None
    if clause.degree is not None:
        property_name, direction = clause.degree
        operation = DEGREE_OPERATIONS.get((domain, property_name, direction))
        if operation is None:
            return None
        parameters: dict[str, object] = {}
        if operation[0] in {"HassLightBrighten", "HassLightDim"}:
            parameters["step_percent"] = extract_degree(clause.text).light_percent
        return operation, parameters
    candidates = {
        ONTOLOGY_OPERATIONS[(domain, action)]
        for action in clause.actions
        if (domain, action) in ONTOLOGY_OPERATIONS
    }
    if len(candidates) != 1:
        return None
    return next(iter(candidates)), {}


def _required_domains(clause: ClauseMeaning) -> frozenset[str]:
    if clause.temperature is not None:
        return frozenset({"climate"})
    if clause.percent is not None:
        return frozenset({"cover", "light", "media_player"})
    if clause.degree is not None:
        return frozenset(domain for domain, prop, direction in DEGREE_OPERATIONS if (prop, direction) == clause.degree)
    return frozenset(
        domain for domain, action in ONTOLOGY_OPERATIONS if action in clause.actions
    )


def _build_result(
    clause: ClauseMeaning,
    targets: tuple[EntitySnapshot, ...],
    operation: OperationTarget,
    parameters: dict[str, object],
    source_text: str,
    area: AreaReference | None,
) -> ParseResult:
    single = len(targets) == 1
    first = targets[0]
    target = (
        TargetReference(first.friendly_name, first.entity_id, first.domain)
        if single else TargetReference(first.domain, domain=first.domain)
    )
    action = (
        SemanticAction.SET if clause.percent is not None or clause.temperature is not None
        else SemanticAction.ADJUST if clause.degree is not None
        else _ACTION_TO_SEMANTIC.get(next(iter(sorted(clause.actions))), SemanticAction.SET)
        if clause.actions else SemanticAction.SET
    )
    property_ = None
    direction = None
    if clause.degree is not None:
        property_ = _PROPERTY.get(clause.degree[0])
        direction = SemanticDirection.INCREASE if clause.degree[1] > 0 else SemanticDirection.DECREASE
    elif clause.temperature is not None:
        property_ = SemanticProperty.TEMPERATURE
    elif clause.percent is not None and first.domain in {"cover", "light"}:
        property_ = SemanticProperty.POSITION if first.domain == "cover" else SemanticProperty.BRIGHTNESS
    if operation[0] == "svc":
        intent = REGISTERED_OPERATION_INTENT
        frame_parameters: dict[str, object] = {
            "service_domain": operation[1],
            "service_name": operation[2],
            "service_data": {
                key: value for key, value in parameters.items() if key == "volume_level"
            },
        }
    else:
        intent = operation[0]
        frame_parameters = dict(parameters)
    return ParseResult(
        frame=SemanticFrame(
            intent=intent,
            target=target,
            area=None if single else area,
            quantifier=None if single else Quantifier("all"),
            parameters=frame_parameters,
            source_text=source_text,
            action=action,
            property=property_,
            direction=direction,
        ),
        resolved_entities=list(targets),
    )


def _names(entities: Sequence[EntitySnapshot]) -> str:
    labels = [entity.friendly_name for entity in entities]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " und " + labels[-1]


def compile_ontology_command(
    document: object,
    entities: Sequence[EntitySnapshot],
    *,
    source_area: AreaSnapshot | None = None,
) -> OntologyCommand | None:
    """Compile a command from genus/place/quantity meaning, or ``None``.

    ``None`` means the utterance holds no operation or no target the
    ontology can describe; a message means it was understood but cannot be
    grounded honestly (for example "Im Büro gibt es keinen Ventilator.").
    """
    tokens = tokenize_language(getattr(getattr(document, "utterance"), "normalized_text"))
    if any(token.canonical in TIME_BOUND_WORDS for token in tokens) or any(
        tokens[index].canonical == ":"
        and tokens[index - 1].is_number and tokens[index + 1].is_number
        for index in range(1, len(tokens) - 1)
    ):
        # Time-bound commands belong to scheduling/automation.
        return None
    clauses = _clause_meanings(document, entities)
    if not clauses:
        return None
    unclear = [clause for clause in clauses if clause.residue]
    if unclear:
        if len(clauses) == 1:
            return None
        parts = " und ".join(f"„{clause.text.strip(' ,.')}“" for clause in unclear)
        return OntologyCommand(
            message=(
                f"Den Teil {parts} habe ich nicht verstanden. "
                "Ich habe deshalb nichts ausgeführt."
            ),
            clauses=len(clauses),
        )
    lexicon = build_place_lexicon(entities)
    source_place: Place | None = (
        lexicon.place_for_area(source_area.area_id) if source_area is not None else None
    )
    results: list[ParseResult] = []
    kept: list[EntitySnapshot] = []
    domains_seen: set[str] = set()
    total_targets = 0
    universal = False
    plural = False
    for clause in clauses:
        descriptions = list(clause.descriptions)
        if not descriptions:
            # Degree without a device ("Mach es im Kinderzimmer kühler"):
            # the property implies the genus at the spoken place.
            if clause.degree is None:
                return None
            places = [
                mention.place for mention in lexicon.scan(
                    [token.canonical for token in getattr(document, "tokens") if token.is_word]
                )
            ]
            genus_key = PROPERTY_GENUS.get(clause.degree[0])
            if genus_key is None or not places:
                return None
            descriptions = [TargetDescription(genera=(genus_key,), head=genus_key, place=places[0])]
        required = _required_domains(clause)
        if not required:
            return None
        for description in descriptions:
            if any(
                clause.actions & genus(key).forbidden_actions
                for key in description.genera[:1]
            ) and clause.degree is None:
                return None
            resolution: TargetResolution = resolve_description(
                description, entities, source_area=source_place, domains=required,
            )
            if resolution.outcome is ResolutionOutcome.NONE:
                return OntologyCommand(message=resolution.message, clauses=len(clauses))
            if (
                resolution.outcome is ResolutionOutcome.AMBIGUOUS
                and description.quantity in {Quantity.BOTH, Quantity.COUNT}
            ):
                wanted = "zwei" if description.quantity is Quantity.BOTH else str(description.count)
                place = resolution.description.place
                where = place.label if place is not None else "im Haus"
                return OntologyCommand(
                    message=(
                        f"{where[:1].upper()}{where[1:]} gibt es {len(resolution.entities)} "
                        f"passende Geräte, nicht genau {wanted}. Ich habe nichts ausgeführt."
                    ),
                    clauses=len(clauses),
                )
            if (
                resolution.outcome is ResolutionOutcome.AMBIGUOUS
                and len(resolution.entities) > _MAX_CLARIFICATION_CANDIDATES
            ):
                key = description.genus_key or "device"
                place = resolution.description.place
                where = place.label if place is not None else "im Haus"
                return OntologyCommand(
                    message=(
                        f"{where[:1].upper()}{where[1:]} gibt es {len(resolution.entities)} "
                        f"{genus(key).plural}. Welches meinst du? Nenne bitte Raum oder Namen."
                    ),
                    clauses=len(clauses),
                )
            if resolution.outcome is ResolutionOutcome.AMBIGUOUS:
                first_domain = resolution.entities[0].domain
                operation = _operation_for(first_domain, clause)
                intent = operation[0][0] if operation is not None else "HassTurnOn"
                if operation is not None and operation[0][0] == "svc":
                    intent = REGISTERED_OPERATION_INTENT
                parameters = dict(operation[1]) if operation is not None else {}
                if operation is not None and operation[0][0] == "svc":
                    parameters = {
                        "service_domain": operation[0][1],
                        "service_name": operation[0][2],
                        "service_data": {k: v for k, v in operation[1].items() if k == "volume_level"},
                    }
                if len({entity.domain for entity in resolution.entities}) != 1 or len(clauses) > 1:
                    names = _names(resolution.entities)
                    return OntologyCommand(
                        message=f"Welches Gerät meinst du: {names}?",
                        clauses=len(clauses),
                    )
                return OntologyCommand(
                    clarification=ClarificationRequest(
                        intent,
                        first_domain,
                        tuple(resolution.entities),
                        parameters,
                    ),
                    clauses=len(clauses),
                )
            targets = tuple(resolution.entities)
            if description.universal:
                universal = True
                everyday = [
                    entity for entity in targets
                    if entity_genera(entity) & _EVERYDAY_GENERA
                ]
                place = resolution.description.place
                kept.extend(
                    entity for entity in entities
                    if entity.domain in _KEPT_BY_EVERYTHING_DOMAINS
                    and place is not None and place.contains(entity)
                    and entity not in kept
                )
                targets = tuple(everyday)
                if clause.actions & {"turn_off"}:
                    # Switching "everything" off never needs to touch what is
                    # already off; the preview stays short and honest.
                    active = tuple(entity for entity in targets if entity.state not in {"off", "unavailable", "idle", "standby"})
                    targets = active or targets
            if description.is_plural or description.universal or resolution.description.quantity is Quantity.ALL:
                plural = True
            by_domain: dict[str, list[EntitySnapshot]] = {}
            for entity in targets:
                by_domain.setdefault(entity.domain, []).append(entity)
            if clause.temperature is not None and not all(
                _within_reported_range(entity, clause.temperature) for entity in targets
            ):
                return None
            for domain, members in sorted(by_domain.items()):
                operation = _operation_for(domain, clause)
                if operation is None:
                    if description.universal or len(by_domain) > 1:
                        continue
                    return None
                place = resolution.description.place
                area = (
                    AreaReference(place.name, next(iter(place.area_ids)), place.name)
                    if place is not None and place.kind is PlaceKind.AREA and len(place.area_ids) == 1
                    else None
                )
                results.append(_build_result(
                    clause, tuple(members), operation[0], operation[1],
                    getattr(document, "source_text"), area,
                ))
                domains_seen.add(domain)
                total_targets += len(members)
    if not results:
        return None
    preview = None
    if universal or len(domains_seen) > 1 and len(clauses) == 1 or (plural and total_targets > GROUP_PREVIEW_THRESHOLD):
        targets = [entity for result in results for entity in result.resolved_entities]
        preview = _names(targets)
    return OntologyCommand(
        results=tuple(results), preview=preview, kept=tuple(kept), clauses=len(clauses),
    )
