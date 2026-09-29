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
from .clause_reading import FILLER_WORDS, ClauseMeaning, read_clauses, read_operation
from .degree_semantics import extract_degree
from .device_ontology import GENERA, entity_genera, genus
from .frame import AreaReference, Quantifier, SemanticFrame, TargetReference
from .language_frontend import tokenize_language
from .normalize import normalize
from .entity_clarification import which_question
from .parser import ClarificationRequest, ParseResult
from .place_model import Place, PlaceKind, build_place_lexicon
from .primitives import SemanticAction, SemanticDirection, SemanticProperty
from .semantic_catalog import (
    DEGREE_OPERATIONS,
    GROUP_PREVIEW_THRESHOLD,
    ONTOLOGY_OPERATIONS,
    COLOR_TEMPERATURE_WORDS,
    OPTION_OPERATIONS,
    PROPERTY_GENUS,
    TIME_BOUND_WORDS,
    OperationTarget,
)
from .target_resolution import (
    Quantity,
    ResolutionOutcome,
    TargetDescription,
    TargetResolution,
    name_index,
    describe_with_residue,
    resolve_description,
)

__all__ = ("OntologyCommand", "compile_ontology_command", "compile_release")


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
_MAX_CLARIFICATION_CANDIDATES = 8
_EVERYDAY_GENERA = frozenset(item.key for item in GENERA if item.in_everything)
# Kinds that "alles" never switches silently; they are named in the preview.
_KEPT_BY_EVERYTHING_DOMAINS = frozenset({"climate", "lock", "alarm_control_panel", "water_heater", "valve"})


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
        adjustment = extract_degree(normalize(clause.text))
        if operation[0] in {"HassLightBrighten", "HassLightDim"}:
            parameters["step_percent"] = adjustment.light_percent
        elif operation[0] in {"HassClimateIncreaseTemperature", "HassClimateDecreaseTemperature"}:
            parameters["step"] = adjustment.climate_degrees
        elif domain in {"cover", "media_player"} and (
            domain == "cover" or adjustment.amount is not None
        ):
            # A spoken step from the current value; built per device.
            parameters["relative_step"] = direction * adjustment.percent_step
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


def _relative_results(
    clause: ClauseMeaning,
    domain: str,
    members: tuple[EntitySnapshot, ...],
    step: float,
    source_text: str,
    area: AreaReference | None,
) -> list[ParseResult] | None:
    """A spoken step from each device's current value (7.6.1).

    Volume and cover position have no relative service with an amount;
    the new absolute value is current ± step, bounded by the device range.
    An unknown current value is never guessed.
    """
    results: list[ParseResult] = []
    for entity in members:
        stepped = apply_relative_step(entity, step)
        if stepped is None:
            return None
        results.append(_build_result(clause, (entity,), stepped[0], stepped[1], source_text, area))
    return results


def apply_relative_step(
    entity: EntitySnapshot, step: float
) -> tuple[OperationTarget, dict[str, object]] | None:
    """Current volume/position ± step (percent points), bounded to 0-100."""
    attribute, scale = (
        ("volume_level", 100.0) if entity.domain == "media_player" else ("current_position", 1.0)
    )
    try:
        current = float(entity.attributes[attribute]) * scale
    except (KeyError, TypeError, ValueError):
        return None
    target = max(0.0, min(100.0, current + step))
    if entity.domain == "media_player":
        return ("svc", "media_player", "volume_set"), {"volume_level": round(target / 100.0, 2)}
    return ("HassSetPercentage",), {"percent": int(round(target))}


def _names(entities: Sequence[EntitySnapshot]) -> str:
    labels = [entity.friendly_name for entity in entities]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " und " + labels[-1]


def _option_command(
    document: object,
    entities: Sequence[EntitySnapshot],
    source_area: AreaSnapshot | None,
) -> OntologyCommand | None:
    """"<target> auf <listed option>": select a value the device reports.

    The value must literally be one of the option list entries Home
    Assistant exposes for the grounded device; nothing is invented.
    """
    source = getattr(getattr(document, "utterance"), "normalized_text")
    tokens = tokenize_language(source)
    positions = [index for index, token in enumerate(tokens) if token.canonical == "auf"]
    if len(positions) != 1:
        return None
    split = positions[0]
    value_tokens = [token for token in tokens[split + 1:] if token.is_word or token.is_number]
    if not value_tokens or all(token.is_number for token in value_tokens):
        return None
    value_text = source[value_tokens[0].start:value_tokens[-1].end]
    value_key = normalize_for_compare(value_text)
    if not value_key or value_key in FILLER_WORDS:
        return None
    lexicon = build_place_lexicon(entities)
    descriptions, _residue = describe_with_residue(
        tokens[:split], entities, lexicon=lexicon,
        ignore=frozenset(read_operation(normalize(source[:tokens[split].start]))[1] | FILLER_WORDS),
    )
    if len(descriptions) != 1:
        return None
    place = lexicon.place_for_area(source_area.area_id) if source_area is not None else None
    domains = frozenset(domain for domain, _ in OPTION_OPERATIONS)
    resolution = resolve_description(descriptions[0], entities, source_area=place, domains=domains)
    if resolution.outcome is not ResolutionOutcome.RESOLVED or len(resolution.entities) != 1:
        return None
    entity = resolution.entities[0]
    for (domain, attribute), (service_domain, service, key) in OPTION_OPERATIONS.items():
        if entity.domain != domain:
            continue
        options = entity.attributes.get(attribute) or ()
        chosen = next(
            (option for option in options if normalize_for_compare(str(option)) == value_key),
            None,
        )
        if chosen is None:
            continue
        return OntologyCommand(results=(ParseResult(
            frame=SemanticFrame(
                intent=REGISTERED_OPERATION_INTENT,
                target=TargetReference(entity.friendly_name, entity.entity_id, entity.domain),
                area=None,
                parameters={
                    "service_domain": service_domain,
                    "service_name": service,
                    "service_data": {key: str(chosen)},
                },
                source_text=getattr(document, "source_text"),
                action=SemanticAction.SET,
            ),
            resolved_entities=[entity],
        ),))
    return None


_TONE_WORDS = {
    normalize_for_compare(word): kelvin for word, kelvin in COLOR_TEMPERATURE_WORDS.items()
}
_TONE_GLUE = frozenset({"auf", "in", "weiss", "licht", "lichtfarbe", "farbe", "stellen", "stelle", "stell"})


def _tone_command(
    document: object,
    entities: Sequence[EntitySnapshot],
    source_area: AreaSnapshot | None,
) -> OntologyCommand | None:
    """"<Licht-Ziel> (auf) warmweiß": a white tone for every capable light.

    Only lights reporting colour-temperature support are set; when none of
    the described lights can, the answer says so instead of "not
    understood".  Singular with several capable members asks.
    """
    source = getattr(getattr(document, "utterance"), "normalized_text")
    tokens = tokenize_language(source)
    words = [token.canonical for token in tokens]
    kelvin: int | None = None
    tone_indices: set[int] = set()
    for index, word in enumerate(words):
        if word in _TONE_WORDS:
            kelvin, tone_indices = _TONE_WORDS[word], {index}
        elif index + 1 < len(words) and words[index + 1] == "weiss" and f"{word}weiss" in _TONE_WORDS:
            kelvin, tone_indices = _TONE_WORDS[f"{word}weiss"], {index, index + 1}
    if kelvin is None:
        return None
    target_tokens = [token for index, token in enumerate(tokens) if index not in tone_indices]
    lexicon = build_place_lexicon(entities)
    descriptions, residue = describe_with_residue(
        target_tokens, entities, lexicon=lexicon,
        ignore=frozenset(read_operation(normalize(source))[1] | FILLER_WORDS | {"auf"}),
        names=name_index(entities),
    )
    if residue or len(descriptions) != 1:
        return None
    place = lexicon.place_for_area(source_area.area_id) if source_area is not None else None
    resolution = resolve_description(
        descriptions[0], entities, source_area=place, domains=frozenset({"light"}),
    )
    lights = [entity for entity in resolution.entities if entity.domain == "light"]
    if not lights or resolution.outcome not in {ResolutionOutcome.RESOLVED, ResolutionOutcome.AMBIGUOUS}:
        return None
    capable = [entity for entity in lights if "COLOR_TEMPERATURE" in entity.capabilities]
    tone = next(word for word, value in COLOR_TEMPERATURE_WORDS.items() if value == kelvin)
    if not capable:
        verb = "kann" if len(lights) == 1 else "können"
        return OntologyCommand(message=(
            f"{_names(lights)} {verb} keine Lichtfarbe wie {tone} einstellen. "
            "Ich habe nichts ausgeführt."
        ))
    if resolution.outcome is ResolutionOutcome.AMBIGUOUS and len(capable) > 1:
        return OntologyCommand(clarification=ClarificationRequest(
            "HassLightSetColorTemp", "light", tuple(capable), {"color_temp_kelvin": kelvin},
        ))
    results = tuple(
        ParseResult(
            frame=SemanticFrame(
                intent="HassLightSetColorTemp",
                target=TargetReference(entity.friendly_name, entity.entity_id, entity.domain),
                area=None,
                parameters={"color_temp_kelvin": kelvin},
                source_text=getattr(document, "source_text"),
                action=SemanticAction.SET,
                property=SemanticProperty.COLOR_TEMPERATURE,
            ),
            resolved_entities=[entity],
        )
        for entity in capable
    )
    kept = tuple(entity for entity in lights if entity not in capable)
    return OntologyCommand(results=results, kept=kept, clauses=1)


def _with_exceptions(
    document: object,
    entities: Sequence[EntitySnapshot],
    source_area: AreaSnapshot | None,
) -> OntologyCommand | None:
    """"Alle Lichter in Küche und Flur aus, außer der Kücheninsel".

    The positive command is compiled on its own; every named exception must
    be exactly one registry device among its targets, otherwise ``None``
    (the honest explanation is given by the failure feedback).
    """
    from dataclasses import replace as _replace

    from .language_frontend import analyse_language
    from .semantic_exclusion import has_exclusion_clause, split_exclusion

    source = getattr(getattr(document, "utterance"), "normalized_text")
    if not has_exclusion_clause(source):
        return None
    positive, names = split_exclusion(source)
    if not names or positive.strip() == source.strip():
        return None
    compiled = compile_ontology_command(
        analyse_language(positive, entities), entities, source_area=source_area,
    )
    if compiled is None or not compiled.executable:
        return None
    index = name_index(entities)
    targets = {entity.entity_id for result in compiled.results for entity in result.resolved_entities}
    excluded: set[str] = set()
    for name in names:
        matches = index.phrases.get(normalize_for_compare(name))
        if not matches or len(matches) != 1 or matches[0].entity_id not in targets:
            return None
        excluded.add(matches[0].entity_id)
    results = []
    for result in compiled.results:
        kept = [entity for entity in result.resolved_entities if entity.entity_id not in excluded]
        if kept:
            results.append(_replace(result, resolved_entities=kept))
    if not results:
        return None
    return _replace(compiled, results=tuple(results))


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
    excepted = _with_exceptions(document, entities, source_area)
    if excepted is not None:
        return excepted
    option = _option_command(document, entities, source_area)
    if option is not None:
        return option
    tone = _tone_command(document, entities, source_area)
    if tone is not None:
        return tone
    clauses = read_clauses(document, entities)
    if not clauses:
        return None
    return compile_clauses(clauses, document, entities, source_area)


def compile_release(
    document: object,
    object_text: str,
    action: str,
    entities: Sequence[EntitySnapshot],
    *,
    source_area: AreaSnapshot | None = None,
) -> OntologyCommand | None:
    """Ground a release ("X muss nicht an sein") as ``action`` on X."""
    tokens = tokenize_language(object_text)
    descriptions, residue = describe_with_residue(
        tokens, entities, lexicon=build_place_lexicon(entities),
        ignore=FILLER_WORDS, names=name_index(entities),
    )
    if not descriptions:
        return None
    clause = ClauseMeaning(
        text=object_text, actions=frozenset({action}), degree=None, percent=None,
        temperature=None, descriptions=descriptions, residue=residue,
    )
    return compile_clauses((clause,), document, entities, source_area)


def compile_clauses(
    clauses: Sequence[ClauseMeaning],
    document: object,
    entities: Sequence[EntitySnapshot],
    source_area: AreaSnapshot | None,
) -> OntologyCommand | None:
    unclear = [clause for clause in clauses if clause.residue]
    if unclear:
        if len(clauses) == 1:
            clause = clauses[0]
            if clause.descriptions and (clause.actions or clause.degree or clause.percent is not None
                                        or clause.temperature is not None):
                # Operation and target are understood, a rest is not (7.8 B1):
                # say the rest, never a capability the device does have.
                rest = " ".join(clause.residue)
                return OntologyCommand(
                    message=(
                        f"Den Teil „{rest}“ habe ich nicht verstanden. "
                        "Ich habe deshalb nichts ausgeführt."
                    ),
                    clauses=1,
                )
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
                    return OntologyCommand(
                        message=which_question(tuple(resolution.entities)),
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
                if "relative_step" in operation[1]:
                    stepped = _relative_results(
                        clause, domain, tuple(members), float(str(operation[1]["relative_step"])),
                        getattr(document, "source_text"), area,
                    )
                    if stepped is None:
                        return None
                    results.extend(stepped)
                else:
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
