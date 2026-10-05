"""Typed grounding of automation event roles against the house (7.2.0).

``automation_language.read_event_roles`` says *what* an event clause means
(subject words, comparator, value, state, ...).  This module decides *which
entities* it is about and projects the result into the existing
``TriggerModel`` - directly, structure to structure, never by generating a
German sentence for another parser (V8 rule).

Grounding is typed, not fuzzy: a device noun ("Rolllade", "Fenster",
"Temperatur") fixes the domain/device class, a known area name or a locative
fixes the room, and any remaining word must literally occur in a candidate's
name.  Free-text similarity is never used to pick an entity - the registry's
fuzzy resolver happily maps "Rolllade im Büro" to a *light* in the Büro, so
that shortcut is not taken.  Several matches for a definite reference are a
clarification ("Welche Rolllade im Büro meinst du?"), never the first match.

The resolved domain alone decides what "50 Prozent" means (cover position,
light brightness, fan speed, or a percentage sensor's state).

Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

from functools import lru_cache

import re
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Sequence

from .automation_language import METER_PERIOD_WORDS, EventRoles, ValueUnit
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.automation_lexicon import NounClass, noun_class, split_compound
from .nlu.automation_model import NumericComparator, TriggerModel, TriggerTarget, TriggerType
from .nlu.constraint_resolver import Constraints, resolve_candidates
from .nlu.german_morphology import (
    GrammaticalGender,
    dative_location_phrase,
    definite_entity_phrase,
    entity_name_gender,
)
from .nlu.measurement import (
    MeasurementProperty,
    is_valid_value,
    percent_property_for_domain,
)
from .missing_part import MissingPart
from .nlu.place_model import Place, PlaceKind, PlaceLexicon, build_place_lexicon
from .nlu.semantic_state import SemanticState
from .nlu.target_resolution import genus_members
from .situation_detection import APPLIANCE_FINISHED_STATES, APPLIANCE_RUNNING_STATES


class GroundingStatus(Enum):
    RESOLVED = auto()
    AMBIGUOUS = auto()  # several entities fit a definite reference
    NOT_FOUND = auto()  # the described device does not exist
    MISSING_SUBJECT = auto()  # "wenn es 50 Prozent erreicht"
    UNSUPPORTED = auto()  # understood, but no safe trigger exists
    NOT_APPLICABLE = auto()  # not an entity-state event (time, sun, presence ...)


class Quantifier(Enum):
    DEFINITE = auto()  # "die Rolllade"
    ANY = auto()  # "ein/irgendein Fenster", "einer der Rollläden"
    ALL = auto()  # "alle Fenster" - an aggregate, never a single trigger
    BARE = auto()  # "Bürofenster", "Rolllade Büro"


@dataclass(frozen=True)
class SubjectReading:
    """The grounded-to-be subject noun phrase."""

    noun: NounClass | None
    noun_word: str | None
    area_id: str | None
    area_name: str | None
    unknown_location: str | None
    modifiers: tuple[str, ...]
    quantifier: Quantifier
    implicit: bool  # "es", "etwas" or no subject at all
    # A floor, level word or whole-house scope ("im Obergeschoss",
    # "im Keller", "draußen") from the shared place model (7.3.0).
    place: Place | None = None


@dataclass(frozen=True)
class GroundedEvent:
    status: GroundingStatus
    trigger: TriggerModel | None = None
    candidates: tuple[EntitySnapshot, ...] = ()
    question: str | None = None
    reason: str | None = None
    subject: SubjectReading | None = None
    roles: EventRoles | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    # "alle Fenster zu" (7.9 W1): the trigger fires when any member reaches
    # the state; the whole set must be in it (a condition over all members).
    aggregate: bool = False
    # The one part the question asks for (7.9.1 A6): the answer in the
    # next turn is read as exactly this part.
    missing: MissingPart | None = None


_ANY_WORDS = frozenset({
    "ein", "eine", "einer", "eines", "einem", "einen", "eins", "irgendein", "irgendeine",
    "irgendeiner", "irgendeines", "irgendeins", "irgendeinem", "irgendwelche", "jede", "jeder",
    "jedes", "beliebige", "beliebiges", "beliebiger", "ne",
})
_DEFINITE_WORDS = frozenset({"der", "die", "das", "den", "dem", "des", "mein", "meine", "unser", "unsere"})
_LOCATIVES = ("in der", "in dem", "im", "in", "am", "vom", "von der", "aus dem", "aus der", "beim", "auf dem", "auf der")
_PRONOUN_SUBJECTS = frozenset({"es", "etwas", "was", "das", "dies", "alles"})
_OUTDOOR_WORDS = frozenset({"draußen", "draussen", "außen", "aussen", "außerhalb"})
_SIDE_WORDS = {
    "links": "links", "linke": "links", "linken": "links", "linker": "links", "linkes": "links",
    "rechts": "rechts", "rechte": "rechts", "rechten": "rechts", "rechter": "rechts", "rechtes": "rechts",
    "vorne": "vorne", "vordere": "vorne", "vorderen": "vorne",
    "hinten": "hinten", "hintere": "hinten", "hinteren": "hinten",
    "oben": "oben", "obere": "oben", "oberen": "oben", "unten": "unten", "untere": "unten",
}
_PLURAL_PARTITIVE_RE = re.compile(
    r"^(?:einer|eines|eins|eine|irgendeiner|irgendeins|irgendeines)\s+(?:der|von\s+den)$"
)


def _area_index(entities: Sequence[EntitySnapshot]) -> dict[str, tuple[str, str]]:
    # Normalise each distinct area once; registries repeat an area for
    # every entity in it.
    areas = tuple(dict.fromkeys(
        (entity.area_id, entity.area_name, tuple(entity.area_aliases))
        for entity in entities
        if entity.area_id is not None and entity.area_name
    ))
    return _area_index_of(areas)


@lru_cache(maxsize=16)
def _area_index_of(
    areas: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for area_id, area_name, aliases in areas:
        for name in (area_name, *aliases):
            key = normalize_for_compare(name).strip()
            if key:
                index.setdefault(key, (area_id, area_name))
    return index


def read_subject(words: Sequence[str], entities: Sequence[EntitySnapshot]) -> SubjectReading:
    """Split subject words into noun, area, modifiers and quantifier."""
    areas = _area_index(entities)
    lowered = [word.casefold() for word in words]
    joined = " ".join(lowered)
    quantifier = Quantifier.BARE
    noun: NounClass | None = None
    noun_word: str | None = None
    area: tuple[str, str] | None = None
    unknown_location: str | None = None
    modifiers: list[str] = []
    implicit = False

    index = 0
    tokens = list(words)
    keys = list(lowered)
    if _PLURAL_PARTITIVE_RE.match(" ".join(keys[:2])) or _PLURAL_PARTITIVE_RE.match(" ".join(keys[:3])):
        quantifier = Quantifier.ANY
    scope: Place | None = None
    noun_index = -1
    lexicon = build_place_lexicon(entities)
    exact_names: dict[str, list[EntitySnapshot]] = {}
    for entity in entities:
        for name in (entity.friendly_name, *entity.aliases):
            key_name = normalize_for_compare(name)
            if key_name and " " not in key_name:
                exact_names.setdefault(key_name, []).append(entity)
    normalized_keys = [normalize_for_compare(word) for word in tokens]
    while index < len(tokens):
        key = keys[index]
        # Shared place model first: floors, floor aliases and level words
        # ("im Obergeschoss", "im Keller", "oben", "draußen").
        preposition = 0
        while (
            index + preposition < len(tokens)
            and normalized_keys[index + preposition] in _PLACE_PREPOSITIONS
            and preposition < 2
        ):
            preposition += 1
        spoken_place = _scan_place(lexicon, normalized_keys[index + preposition:index + preposition + 3])
        if spoken_place is not None and spoken_place[0].kind in {
            PlaceKind.FLOOR, PlaceKind.HOUSE,
        } | ({PlaceKind.AREA} if preposition else set()):
            found_place, used = spoken_place
            if found_place.kind is PlaceKind.AREA and len(found_place.area_ids) == 1:
                area_id = next(iter(found_place.area_ids))
                area = (area_id, found_place.name)
            else:
                scope = found_place
            index += preposition + used
            continue
        # Locative phrase: "im Büro", "in der Küche"
        locative = next(
            (
                prep for prep in _LOCATIVES
                if keys[index:index + len(prep.split())] == prep.split()
            ),
            None,
        )
        if locative is not None:
            span = len(locative.split())
            place_words = tokens[index + span:index + span + 2]
            place = _match_area(place_words, areas)
            if place is not None:
                area_value, used = place
                area = area_value
                index += span + used
                continue
            if place_words:
                first = place_words[0]
                place_key = first.casefold()
                if place_key in _OUTDOOR_WORDS:
                    modifiers.append("aussen")
                    index += span + 1
                    continue
                if locative in {"vom", "von der", "beim"} or noun_class(first) is None and first[:1].isupper():
                    if locative in {"vom", "von der"}:
                        modifiers.append(normalize_for_compare(first))
                    else:
                        unknown_location = first
                    index += span + 1
                    continue
        if key in _OUTDOOR_WORDS:
            modifiers.append("aussen")
            index += 1
            continue
        if key == "alle" or key == "sämtliche":
            quantifier = Quantifier.ALL
            index += 1
            continue
        if key in _ANY_WORDS:
            quantifier = Quantifier.ANY
            index += 1
            continue
        if key in _DEFINITE_WORDS:
            if quantifier is Quantifier.BARE:
                quantifier = Quantifier.DEFINITE
            index += 1
            continue
        if key in _PRONOUN_SUBJECTS and noun is None:
            implicit = True
            index += 1
            continue
        if key in {"von", "der", "und", "oder"}:
            index += 1
            continue
        exact = exact_names.get(normalized_keys[index])
        if exact is not None and noun is None:
            # An exact registry name wins over compound splitting:
            # "Terrassentür" is the device of that name, not "a door on the
            # terrace" (7.3.0, Abschnitt 1).
            entity = exact[0]
            compound = split_compound(key)
            noun = noun_class(key) or (compound[1] if compound is not None else None)
            if noun is None:
                noun = NounClass(entity.domain, entity.device_class)
            noun_word = tokens[index]
            modifiers.append(normalized_keys[index])
            index += 1
            continue
        entry = noun_class(key)
        if entry is not None and noun is None:
            noun, noun_word = entry, tokens[index]
            noun_index = index
            index += 1
            continue
        if entry is not None and noun is not None and noun_index == index - 1:
            # German noun sequences are head-final: in "Handy Akku" the
            # battery is the subject and "Handy" names whose it is.
            modifiers.append(normalize_for_compare(noun_word or ""))
            noun, noun_word = entry, tokens[index]
            noun_index = index
            index += 1
            continue
        place = _match_area(tokens[index:index + 2], areas)
        if place is not None:
            area, used = place
            index += used
            continue
        compound = split_compound(key)
        if compound is not None and noun is None:
            prefix, entry = compound
            noun, noun_word = entry, tokens[index]
            prefix_area = area_by_key(normalize_for_compare(prefix), areas)
            prefix_place = lexicon.resolve_modifier(normalize_for_compare(prefix))
            if prefix_area is not None:
                area = prefix_area
            elif prefix_place is not None and prefix_place.kind is PlaceKind.FLOOR:
                scope = prefix_place
            elif prefix.strip("-") in _OUTDOOR_WORDS or prefix.startswith("außen"):
                modifiers.append("aussen")
            else:
                modifiers.append(normalize_for_compare(prefix.strip("-")))
            index += 1
            continue
        side = _SIDE_WORDS.get(key)
        modifiers.append(side if side is not None else normalize_for_compare(tokens[index]))
        index += 1
    if noun is None and not modifiers and area is None:
        implicit = True
    del joined
    return SubjectReading(
        noun=noun,
        noun_word=noun_word,
        area_id=area[0] if area is not None else None,
        area_name=area[1] if area is not None else None,
        unknown_location=unknown_location,
        modifiers=tuple(normalize_for_compare(item) for item in modifiers if item),
        quantifier=quantifier,
        implicit=implicit and noun is None and scope is None,
        place=scope,
    )


_PLACE_PREPOSITIONS = frozenset({"im", "in", "der", "dem", "am", "auf", "beim"})


def _scan_place(lexicon: PlaceLexicon, words: Sequence[str]) -> tuple[Place, int] | None:
    for size in range(min(3, len(words)), 0, -1):
        found = lexicon.phrases.get(" ".join(words[:size]))
        if found is not None and found.kind is not PlaceKind.HERE:
            return found, size
    return None


def area_by_key(key: str, areas: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Exact area name, or its German compound/linking form ("Küchen", "Bads")."""
    key = key.strip(" -")
    for candidate in (key, key[:-1] if key.endswith(("n", "s")) else "", key[:-2] if key.endswith("en") else ""):
        if candidate and candidate in areas:
            return areas[candidate]
    return None


def _match_area(
    words: Sequence[str], areas: dict[str, tuple[str, str]]
) -> tuple[tuple[str, str], int] | None:
    for size in (2, 1):
        if len(words) < size:
            continue
        found = area_by_key(normalize_for_compare(" ".join(words[:size])), areas)
        if found is not None:
            return found, size
    return None


# --- candidate selection ---------------------------------------------------------


def subject_candidates(
    subject: SubjectReading, entities: Sequence[EntitySnapshot]
) -> list[EntitySnapshot]:
    """Entities of the subject's genus at its place (shared with commands)."""
    noun = subject.noun
    assert noun is not None
    if noun.genus is not None:
        members = genus_members(noun.genus, entities)
    else:
        members = [
            entity for entity in entities
            if entity.domain == noun.domain
            and (noun.device_class is None or entity.device_class == noun.device_class)
        ]
    return [
        entity for entity in members
        if (subject.area_id is None or entity.area_id == subject.area_id)
        and (subject.place is None or subject.place.contains(entity))
    ]


_class_candidates = subject_candidates


def _name_keys(entity: EntitySnapshot) -> tuple[str, ...]:
    return tuple(
        normalize_for_compare(name)
        for name in (entity.friendly_name, *entity.aliases)
        if name
    )


def _matches_modifiers(entity: EntitySnapshot, modifiers: Sequence[str]) -> bool:
    names = _name_keys(entity)
    return all(any(modifier in name for name in names) for modifier in modifiers)


def _named_candidates(
    subject: SubjectReading, entities: Sequence[EntitySnapshot]
) -> list[EntitySnapshot]:
    """No device noun: the modifiers must *be* an entity's name."""
    spoken = " ".join(subject.modifiers)
    if not spoken:
        return []
    exact = [
        entity for entity in entities
        if spoken in _name_keys(entity)
        and (subject.area_id is None or entity.area_id == subject.area_id)
    ]
    return exact


# --- projection ------------------------------------------------------------------


def target_for(
    candidates: Sequence[EntitySnapshot],
    entities: Sequence[EntitySnapshot],
    *,
    area_spoken: bool = True,
) -> TriggerTarget:
    """The narrowest ``TriggerTarget`` whose re-resolution yields exactly ``candidates``.

    Mirrors ``automation_target_resolver.build_named_target``: an entity
    that is the only one of its domain/device class/area keeps the
    class-level target, so "Bürofenster" and "das Fenster im Büro" produce
    the identical model.
    """
    wanted = sorted(entity.entity_id for entity in candidates)
    domains = {entity.domain for entity in candidates}
    device_classes = {entity.device_class for entity in candidates}
    areas = {entity.area_id for entity in candidates}
    if len(domains) == 1:
        domain = next(iter(domains))
        for device_class in (
            next(iter(device_classes)) if len(device_classes) == 1 else None,
            None,
        ):
            area_order = (
                (next(iter(areas)) if len(areas) == 1 else None, None)
                if area_spoken
                else (None, next(iter(areas)) if len(areas) == 1 else None)
            )
            for area_id in area_order:
                resolved = resolve_candidates(
                    list(entities),
                    Constraints(domain=domain, device_class=device_class, area_id=area_id),
                )
                if sorted(entity.entity_id for entity in resolved) == wanted:
                    return TriggerTarget(domain=domain, device_class=device_class, area_id=area_id)
    if len(candidates) == 1:
        entity = candidates[0]
        return TriggerTarget(
            domain=entity.domain,
            device_class=entity.device_class,
            area_id=entity.area_id,
            entity_id=entity.entity_id,
        )
    return TriggerTarget(domain=next(iter(domains)) if len(domains) == 1 else None, entity_ids=tuple(wanted))


_STATE_DOMAINS: dict[str, frozenset[SemanticState]] = {
    "binary_sensor": frozenset({SemanticState.OPEN, SemanticState.CLOSED, SemanticState.ON, SemanticState.OFF}),
    "cover": frozenset({SemanticState.OPEN, SemanticState.CLOSED}),
    "light": frozenset({SemanticState.ON, SemanticState.OFF}),
    "switch": frozenset({SemanticState.ON, SemanticState.OFF}),
    "fan": frozenset({SemanticState.ON, SemanticState.OFF}),
    "media_player": frozenset({SemanticState.ON, SemanticState.OFF}),
    "climate": frozenset({SemanticState.ON, SemanticState.OFF}),
    "input_boolean": frozenset({SemanticState.ON, SemanticState.OFF}),
}
_OPENING_CLASSES = frozenset({"window", "door", "garage_door", "opening"})
# Definite generic "das Fenster"/"die Tür" historically watches every such
# opening (7.1.2 behaviour, protected by tests); every other definite
# reference to several devices is a clarification.
_GENERIC_ANY_CLASSES = frozenset({"window", "door", "motion"})


_KIND_LABELS = {
    "binary_sensor": "Sensoren", "cover": "Antriebe", "lock": "Schlösser",
    "light": "Lichter", "switch": "Schalter",
}


def _device_kinds(candidates: Sequence[EntitySnapshot]) -> list[tuple[str, list[str]]]:
    """Candidates grouped by the domain that decides the state's meaning."""
    grouped: dict[str, list[str]] = {}
    for entity in sorted(candidates, key=lambda item: item.friendly_name):
        grouped.setdefault(entity.domain, []).append(entity.friendly_name)
    return [(_KIND_LABELS.get(domain, domain), names) for domain, names in grouped.items()]


_PRONOUN_GENDERS = {
    "es": GrammaticalGender.NEUTER,
    "er": GrammaticalGender.MASCULINE,
    "sie": GrammaticalGender.FEMININE,
}
_PLURAL_DETERMINERS = frozenset({"die", "alle", "meine", "unsere", "sämtliche"})


def _reference_agrees(
    reference: str, antecedent: Sequence[str], subject: SubjectReading
) -> bool:
    """"es" needs a neuter antecedent, "er" a masculine one, "sie" a feminine
    or plural one.  Without a typed noun there is nothing to check against."""
    gender = _PRONOUN_GENDERS.get(reference)
    if gender is None or subject.noun is None:
        return True
    first = antecedent[0].casefold() if antecedent else ""
    plural = first in _PLURAL_DETERMINERS and subject.noun.gender is not GrammaticalGender.FEMININE
    if reference == "sie":
        return subject.noun.gender is GrammaticalGender.FEMININE or plural
    return subject.noun.gender is gender and not plural


def _state_ok(entity: EntitySnapshot, state: SemanticState) -> bool:
    allowed = _STATE_DOMAINS.get(entity.domain)
    if allowed is None or state not in allowed:
        return False
    if entity.domain == "binary_sensor":
        opening = entity.device_class in _OPENING_CLASSES
        return (state in {SemanticState.OPEN, SemanticState.CLOSED}) == opening
    return True


_GENDER_WHICH = {
    GrammaticalGender.MASCULINE: "Welchen",
    GrammaticalGender.FEMININE: "Welche",
    GrammaticalGender.NEUTER: "Welches",
}


def _which_question(subject: SubjectReading, candidates: Sequence[EntitySnapshot]) -> str:
    noun = subject.noun
    word = (subject.noun_word or "Gerät").strip("-")
    if subject.noun is not None and split_compound(word) is not None and noun_class(word) is None:
        # "Büro-Rolllade" -> ask about the head noun
        head = split_compound(word)
        word = word[len(head[0]):].lstrip("-") if head is not None else word
    word = word[:1].upper() + word[1:]
    # The spoken word's own gender ("der Stromverbrauch") before the genus'.
    lexical = entity_name_gender(word)
    which = _GENDER_WHICH[lexical or noun.gender] if noun is not None else "Welches"
    location = f" {dative_location_phrase(subject.area_name)}" if subject.area_name else ""
    options = " oder ".join(entity.friendly_name for entity in candidates[:4])
    return f"{which} {word}{location} meinst du: {options}?"


def _missing_subject_question(roles: EventRoles) -> str:
    if roles.half:
        return "Was soll halb geöffnet sein?"
    if roles.value is None:
        return "Welches Gerät meinst du?"
    number = f"{roles.value:g}".replace(".", ",")
    unit = {ValueUnit.PERCENT: " Prozent", ValueUnit.DEGREE: " Grad", ValueUnit.PPM: " ppm"}.get(
        roles.unit or ValueUnit.NONE, ""
    )
    comparator = roles.comparator or NumericComparator.EQUAL
    if comparator is NumericComparator.EQUAL:
        return f"Was soll {number}{unit} erreichen?"
    word = {
        NumericComparator.ABOVE: "über", NumericComparator.BELOW: "unter",
        NumericComparator.AT_LEAST: "mindestens", NumericComparator.AT_MOST: "höchstens",
    }[comparator]
    return f"Was soll {word} {number}{unit} liegen?"


_POWER_UNITS = {ValueUnit.WATT: ("W", 1.0), ValueUnit.KILOWATT: ("kW", 1000.0)}
_ENERGY_UNITS = {ValueUnit.WATT_HOUR: ("Wh", 1.0), ValueUnit.KILOWATT_HOUR: ("kWh", 1000.0)}
_SCALE = {"W": 1.0, "kW": 1000.0, "Wh": 1.0, "kWh": 1000.0}
# Nouns that name a rate (power), never an amount (energy).
_RATE_NOUNS = ("leistung", "stromaufnahme")


def _sensor_unit_ok(entity: EntitySnapshot, unit: ValueUnit) -> bool:
    if unit is ValueUnit.NONE:
        return True
    if unit is ValueUnit.DEGREE:
        return entity.unit in {"°C", "°F", "K"}
    if unit in _POWER_UNITS:
        return entity.unit in {"W", "kW"}
    if unit in _ENERGY_UNITS:
        return entity.unit in {"Wh", "kWh"}
    if unit is ValueUnit.PPM:
        return entity.unit == "ppm"
    return entity.unit == "%"


def _in_sensor_unit(value: float, unit: ValueUnit, entity: EntitySnapshot) -> float:
    """"3 kW" against a sensor in W is 3000 (7.9 W4); other units unchanged."""
    spoken = _POWER_UNITS.get(unit) or _ENERGY_UNITS.get(unit)
    if spoken is None or entity.unit not in _SCALE:
        return value
    return value * spoken[1] / _SCALE[entity.unit]


def _ground_energy(
    roles: EventRoles, entities: Sequence[EntitySnapshot]
) -> GroundedEvent | None:
    """"der Stromverbrauch heute über 10 kWh" (7.9 W4): an energy amount is
    only answerable by a meter that restarts with the spoken period - a
    utility meter with that cycle (attribute ``meter_period``).  HomeIntent
    never computes it from a total counter; names are no evidence."""
    if roles.unit not in _ENERGY_UNITS:
        return None
    period_words = [w for w in roles.subject_words if w.casefold() in METER_PERIOD_WORDS]
    period = METER_PERIOD_WORDS[period_words[0].casefold()] if period_words else None
    words = tuple(
        w for w in roles.subject_words
        if w.casefold() not in METER_PERIOD_WORDS and w.casefold() not in {"diese", "diesen", "dieser", "verbraucht", "insgesamt"}
    )
    subject = read_subject(words, entities)
    if (subject.noun_word or "").casefold().endswith(_RATE_NOUNS):
        # "die Leistung über 2 kWh": a rate noun with an amount unit.
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED, reason="unit_mismatch",
            question=(
                f"„{subject.noun_word}“ misst man in Watt, nicht in "
                f"{_ENERGY_UNITS[roles.unit][0]}. Leistung (W) und Energie (kWh) sind verschiedene "
                "Größen – meinst du Watt oder den Verbrauch?"
            ),
            subject=subject, roles=roles,
        )
    energy = [
        e for e in entities
        if e.domain == "sensor" and e.device_class == "energy" and e.unit in {"Wh", "kWh"}
        and (subject.area_id is None or e.area_id == subject.area_id)
    ]
    spoken_period = {"daily": "täglichem", "weekly": "wöchentlichem", "monthly": "monatlichem"}
    if period is None:
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED, reason="energy_period",
            question=(
                "Ab wann soll ich den Verbrauch zählen – heute, diese Woche oder diesen Monat? "
                "Einen Gesamtzähler rechne ich nicht selbst um."
            ),
            subject=subject, roles=roles, missing=MissingPart.PERIOD,
        )
    meters = [e for e in energy if str(e.attributes.get("meter_period", "")).casefold() == period]
    if len(meters) == 1:
        target = TriggerTarget(domain="sensor", device_class="energy", entity_id=meters[0].entity_id)
        assert roles.value is not None and roles.unit is not None
        trigger = TriggerModel(
            type=TriggerType.NUMERIC_STATE, target=target,
            comparator=roles.comparator or NumericComparator.ABOVE,
            threshold=_in_sensor_unit(roles.value, roles.unit, meters[0]),
            for_seconds=roles.for_seconds,
        )
        return GroundedEvent(
            GroundingStatus.RESOLVED, trigger=trigger, candidates=(meters[0],),
            subject=subject, roles=roles,
        )
    if len(meters) > 1:
        ordered = sorted(meters, key=lambda item: item.friendly_name)
        return GroundedEvent(
            GroundingStatus.AMBIGUOUS, candidates=tuple(ordered),
            question="Welchen Verbrauchszähler meinst du: " + " oder ".join(
                item.friendly_name for item in ordered[:4]
            ) + "?",
            subject=subject, roles=roles,
        )
    source = next((e.friendly_name for e in energy if e.state_class == "total_increasing"), None)
    base = f" auf „{source}“" if source else ""
    return GroundedEvent(
        GroundingStatus.UNSUPPORTED, reason="energy_meter_missing",
        question=(
            f"Dafür brauche ich einen Verbrauchszähler, der mit {spoken_period[period]} Zyklus neu "
            f"beginnt. Lege in Home Assistant einen Verbrauchszähler-Helfer mit {spoken_period[period]} "
            f"Zyklus{base} an, dann richte ich das ein. Aus einem Gesamtzähler rechne ich nicht selbst."
        ),
        subject=subject, roles=roles,
    )


def ground_event(roles: EventRoles, entities: Sequence[EntitySnapshot]) -> GroundedEvent:
    """Ground one event clause; never guesses a device."""
    if roles.unsupported is not None:
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED,
            reason=roles.unsupported,
            roles=roles,
        )
    if roles.presence is not None:
        return _ground_presence(roles, entities)
    if roles.change is not None:
        return _ground_change(roles, entities)
    energy = _ground_energy(roles, entities)
    if energy is not None:
        return energy
    if roles.absent is not None and not roles.motion:
        idle = _ground_appliance_idle(roles, entities)
        if idle is not None:
            return idle
    if roles.value is None and roles.state is None:
        finished = _ground_appliance_finished(roles, entities)
        if finished is not None:
            return finished
        return GroundedEvent(GroundingStatus.NOT_APPLICABLE, roles=roles)
    subject = read_subject(roles.subject_words, entities)
    if roles.reference is not None:
        # The subject came from a monitored object (7.8.3).
        if not _reference_agrees(roles.reference, roles.subject_words, subject):
            return GroundedEvent(
                GroundingStatus.MISSING_SUBJECT,
                question=f"Worauf bezieht sich „{roles.reference}“? Bitte nenne das Gerät.",
                subject=subject, roles=roles,
            )
        if roles.reference == "member":
            subject = replace(subject, quantifier=Quantifier.ANY)
        elif subject.quantifier is Quantifier.ALL:
            # "Beobachte alle Fenster und melde dich, wenn eins/es ...": the
            # watched set, never an aggregate state.
            subject = replace(subject, quantifier=Quantifier.ANY)
    unknown_detector_words = tuple(
        item for item in subject.modifiers if item not in {"bewegung", "eine", "ein"}
    )
    if roles.motion and subject.noun is None and unknown_detector_words:
        # "…, wenn der Leckmelder auslöst": an unknown detector name is never
        # corrected into another kind of detector (finding S7).
        spoken = " ".join(unknown_detector_words)
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=f"Ich finde kein Gerät „{spoken}“. Welches Gerät meinst du?",
            subject=subject, roles=roles, missing=MissingPart.DEVICE,
        )
    if roles.motion and subject.noun is None:
        detector = "bewegungsmelder"

        def _here(entity: EntitySnapshot, classes: set[str]) -> bool:
            return (
                (entity.device_class or "") in classes
                and (subject.area_id is None or entity.area_id == subject.area_id)
                and (subject.place is None or subject.place.contains(entity))
            )

        occupancy_here = any(_here(entity, {"occupancy", "presence"}) for entity in entities)
        motion_here = any(_here(entity, {"motion"}) for entity in entities)
        if occupancy_here and (roles.occupancy or not motion_here):
            # "niemand", or a place whose only detector is a presence sensor
            # (it notices movement, too) - 7.9.1 A4/A7.
            detector = "präsenzmelder"
        subject = SubjectReading(
            noun=noun_class(detector), noun_word=detector.capitalize(),
            area_id=subject.area_id, area_name=subject.area_name,
            unknown_location=subject.unknown_location,
            modifiers=tuple(m for m in subject.modifiers if m not in {"bewegung", "eine"}),
            quantifier=Quantifier.ANY, implicit=False,
            # A spoken floor or "draußen" stays a hard limit (7.9.1 A4).
            place=subject.place,
        )
    if subject.unknown_location is not None:
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=(
                f"Einen Bereich „{subject.unknown_location}“ kenne ich nicht. "
                "Welches Gerät meinst du?"
            ),
            subject=subject, roles=roles,
        )
    aggregate = subject.quantifier is Quantifier.ALL
    if aggregate and (roles.value is not None or roles.state is None or roles.for_seconds is not None):
        # A whole set above a value or for a duration has no single
        # Home Assistant trigger that means it.
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED, reason="aggregate", subject=subject, roles=roles
        )

    if subject.noun is None and not subject.modifiers and roles.value is not None:
        # "im Büro 50 Prozent erreicht": a room is not a measurable subject.
        subject = SubjectReading(
            noun=None, noun_word=None, area_id=subject.area_id, area_name=subject.area_name,
            unknown_location=None, modifiers=(), quantifier=subject.quantifier, implicit=True,
            place=subject.place,
        )
    if subject.noun is None and subject.implicit:
        if roles.value is not None and roles.unit is ValueUnit.DEGREE:
            subject = SubjectReading(
                noun=noun_class("temperatur"), noun_word="Temperatur",
                area_id=subject.area_id, area_name=subject.area_name, unknown_location=None,
                modifiers=subject.modifiers, quantifier=Quantifier.DEFINITE, implicit=False,
                place=subject.place,
            )
        else:
            return GroundedEvent(
                GroundingStatus.MISSING_SUBJECT,
                question=_missing_subject_question(roles),
                subject=subject, roles=roles,
            )

    if subject.noun is not None:
        candidates = _class_candidates(subject, entities)
        if subject.modifiers:
            filtered = [entity for entity in candidates if _matches_modifiers(entity, subject.modifiers)]
            candidates = filtered
    else:
        candidates = _named_candidates(subject, entities)
        if not candidates and roles.reference is not None:
            # The monitored object is no device of this house: say so.
            spoken = " ".join(word for word in roles.subject_words if word.casefold() not in _DEFINITE_WORDS)
            return GroundedEvent(
                GroundingStatus.NOT_FOUND,
                question=f"Ich finde kein Gerät „{spoken}“. Welches Gerät soll ich überwachen?",
                subject=subject, roles=roles, missing=MissingPart.DEVICE,
            )
        if not candidates:
            # Not a device we can type - leave it to the established parsers
            # (presence "Julia", time, sun, ...).
            return GroundedEvent(GroundingStatus.NOT_APPLICABLE, subject=subject, roles=roles)

    if roles.state is not None and roles.value is None:
        candidates = [entity for entity in candidates if _state_ok(entity, roles.state)]
        kinds = _device_kinds(candidates)
        if len(kinds) > 1:
            # "Fenster" = window contacts and window drives: one state trigger
            # cannot mean both, and picking one kind would be a guess.
            noun = (subject.noun_word or "dieses Gerät").strip("-")
            options = " oder ".join(
                f"{label} ({', '.join(names[:2])}{', …' if len(names) > 2 else ''})"
                for label, names in kinds
            )
            return GroundedEvent(
                GroundingStatus.UNSUPPORTED,
                reason="mixed_kinds",
                question=f"Mit „{noun}“ können {options} gemeint sein. Welche meinst du?",
                subject=subject, roles=roles,
            )
    if subject.place is not None:
        # "wenn es draußen kälter als 5 Grad wird", "im Keller": the spoken
        # place limits the measured quantity (7.8 B5) - always, also with a
        # single candidate, and never falls back to every device (7.9.1 A4).
        candidates = [entity for entity in candidates if subject.place.contains(entity)]
    if not candidates:
        where = (
            f" {dative_location_phrase(subject.area_name)}" if subject.area_name
            else f" {subject.place.label}" if subject.place is not None else ""
        )
        noun = (subject.noun_word or "dieses Gerät").strip("-")
        if roles.motion and roles.absent is not None:
            # Inactivity needs a detector at that place (7.9 W2): say what is
            # missing instead of asking for a device that does not exist.
            place = where.strip() or "Dort"
            return GroundedEvent(
                GroundingStatus.NOT_FOUND,
                question=(
                    f"{place[:1].upper()}{place[1:]} gibt es keinen Bewegungs- oder Präsenzmelder. "
                    "Ohne ihn kann ich nicht erkennen, ob sich dort etwas bewegt. "
                    "Welchen Melder soll ich stattdessen nehmen?"
                ),
                subject=subject, roles=roles, missing=MissingPart.DEVICE,
            )
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=f"Ich finde{where} kein passendes Gerät für „{noun}“. Welches Gerät meinst du?",
            subject=subject, roles=roles, missing=MissingPart.DEVICE,
        )
    if aggregate:
        if not candidates:
            noun = (subject.noun_word or "diese Geräte").strip("-")
            return GroundedEvent(
                GroundingStatus.NOT_FOUND,
                question=f"Ich finde keine Geräte für „alle {noun}“. Welche Geräte meinst du?",
                subject=subject, roles=roles,
            )
        return replace(_project(roles, subject, candidates, entities), aggregate=True)
    if len(candidates) > 1 and subject.quantifier is not Quantifier.ANY:
        generic_any = (
            roles.value is None
            and subject.noun is not None
            and subject.noun.device_class in _GENERIC_ANY_CLASSES
            and not subject.modifiers
        )
        if not generic_any:
            return GroundedEvent(
                GroundingStatus.AMBIGUOUS,
                candidates=tuple(sorted(candidates, key=lambda item: item.friendly_name)),
                question=_which_question(subject, sorted(candidates, key=lambda item: item.friendly_name)),
                subject=subject, roles=roles,
            )
    return _project(roles, subject, candidates, entities)


def _ground_change(roles: EventRoles, entities: Sequence[EntitySnapshot]) -> GroundedEvent:
    """"die Temperatur im Keller um 3 Grad fällt" (7.9 W3): exactly one sensor
    whose unit fits the spoken unit; the window must have been said."""
    change = roles.change
    assert change is not None
    subject = read_subject(roles.subject_words, entities)
    if subject.noun is None:
        return GroundedEvent(
            GroundingStatus.MISSING_SUBJECT,
            question="Welcher Messwert soll sich ändern? Nenne zum Beispiel „die Temperatur im Keller“.",
            subject=subject, roles=roles,
        )
    candidates = [
        entity for entity in subject_candidates(subject, entities)
        if entity.domain == "sensor" and _sensor_unit_ok(entity, change.unit)
        and change.unit is not ValueUnit.NONE
    ]
    if not candidates:
        where = f" {dative_location_phrase(subject.area_name)}" if subject.area_name else ""
        unit = "Grad" if change.unit is ValueUnit.DEGREE else "Prozent"
        noun = (subject.noun_word or "Messwert").strip("-")
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=f"Ich finde{where} keinen Sensor „{noun}“, der in {unit} misst. Welchen Sensor meinst du?",
            subject=subject, roles=roles, missing=MissingPart.DEVICE,
        )
    if len(candidates) > 1:
        ordered = sorted(candidates, key=lambda item: item.friendly_name)
        return GroundedEvent(
            GroundingStatus.AMBIGUOUS, candidates=tuple(ordered),
            question=_which_question(subject, ordered), subject=subject, roles=roles,
        )
    if change.window_seconds is None:
        # A span is never assumed: "um 3 Grad" over a day and over ten
        # minutes are different warnings.
        return GroundedEvent(
            GroundingStatus.MISSING_SUBJECT,
            question=(
                "In welchem Zeitraum? Sag zum Beispiel: „…, wenn die Temperatur innerhalb "
                "einer Stunde um 3 Grad fällt.“"
            ),
            subject=subject, roles=roles, missing=MissingPart.WINDOW,
        )
    return GroundedEvent(GroundingStatus.RESOLVED, candidates=tuple(candidates), subject=subject, roles=roles)


def _ground_appliance_idle(
    roles: EventRoles, entities: Sequence[EntitySnapshot]
) -> GroundedEvent | None:
    """"wenn die Waschmaschine bis 20 Uhr nicht gelaufen ist" (7.9 W2).

    Only observable evidence: a running binary sensor or a program status
    sensor of that appliance (any run changes it).  A power sensor alone
    cannot tell whether it ran earlier today without the recorder - that is
    said, not guessed.  Returns ``None`` when no appliance is named.
    """
    keys = [
        normalize_for_compare(word) for word in roles.subject_words
        if normalize_for_compare(word) not in _DEFINITE_WORDS
    ]
    if len(keys) != 1:
        return None
    appliance = keys[0]

    def named(entity: EntitySnapshot) -> bool:
        return appliance in normalize_for_compare(entity.friendly_name).replace("-", " ").split()

    power = [e for e in entities if e.domain == "sensor" and e.device_class == "power" and named(e)]
    running = [
        e for e in entities
        if e.domain == "binary_sensor" and e.device_class in {"running", "power"} and named(e)
    ]
    status = [
        e for e in entities
        if e.domain == "sensor" and e.device_class is None and not e.unit and named(e)
        and e.state.casefold() in APPLIANCE_RUNNING_STATES | APPLIANCE_FINISHED_STATES
    ]
    if not (power or running or status):
        return None
    label = next(
        (word for word in roles.subject_words if normalize_for_compare(word) == appliance), appliance
    )
    article = next(
        (word.casefold() for word in roles.subject_words if word.casefold() in _DEFINITE_WORDS), ""
    )
    spoken = f"{article} {label}".strip()
    subject = SubjectReading(
        noun=None, noun_word=label, area_id=None, area_name=None, unknown_location=None,
        modifiers=(appliance,), quantifier=Quantifier.DEFINITE, implicit=False,
    )
    if roles.until is None and roles.for_seconds is None:
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=(
                f"Bis wann soll ich prüfen, ob {spoken} gelaufen ist? Sag zum Beispiel: "
                f"„Melde dich, wenn {spoken} bis 20 Uhr nicht gelaufen ist.“"
            ),
            subject=subject, roles=roles, missing=MissingPart.UNTIL,
        )
    evidence = running or status
    if len(evidence) != 1 or (roles.for_seconds is not None and not running):
        sensor = power[0].friendly_name if power else spoken
        phrase = definite_entity_phrase(label)
        accusative = phrase[1] if phrase is not None else label
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED,
            reason="appliance_run_unobservable",
            question=(
                f"Für {accusative} kenne ich nur den Leistungssensor „{sensor}“. Ob das Gerät "
                "gelaufen ist, kann Home Assistant ohne Verlauf nicht prüfen. Lege dafür in Home Assistant einen "
                "Binärsensor „läuft“ an (zum Beispiel einen Schwellenwert-Helfer auf die Leistung), "
                "dann richte ich das ein."
            ),
            subject=subject, roles=roles,
        )
    entity = evidence[0]
    trigger = TriggerModel(
        type=TriggerType.STATE,
        target=TriggerTarget(domain=entity.domain, entity_id=entity.entity_id),
        state=SemanticState.OFF if entity.domain == "binary_sensor" else SemanticState.INACTIVE,
        for_seconds=roles.for_seconds,
        absent_state=SemanticState.ON,
        appliance_label=spoken,
    )
    return GroundedEvent(
        GroundingStatus.RESOLVED, trigger=trigger, candidates=(entity,), subject=subject, roles=roles
    )


_FINISHED_WORDS = frozenset({"fertig", "durch", "beendet", "feddich", "fertiggewaschen"})
_APPLIANCE_IDLE_WATTS = 5.0
_APPLIANCE_IDLE_SECONDS = 60


def _ground_appliance_finished(
    roles: EventRoles, entities: Sequence[EntitySnapshot]
) -> GroundedEvent | None:
    """"wenn die Waschmaschine fertig ist": the run of a named appliance ends.

    Observable evidence only: a power sensor of that appliance dropping below
    the idle threshold for a minute (the proactive detector's rule), or its
    running binary sensor switching off.
    """
    keys = [normalize_for_compare(word) for word in roles.subject_words]
    if not any(key in _FINISHED_WORDS for key in keys):
        return None
    names = [
        key for key in keys
        if key not in _FINISHED_WORDS and key not in _DEFINITE_WORDS and key not in _ANY_WORDS
    ]
    if len(names) != 1:
        return None
    appliance = names[0]

    def named(entity: EntitySnapshot) -> bool:
        return appliance in normalize_for_compare(entity.friendly_name).replace("-", " ").split()

    power = [
        entity for entity in entities
        if entity.domain == "sensor" and entity.device_class == "power" and named(entity)
    ]
    running = [
        entity for entity in entities
        if entity.domain == "binary_sensor" and entity.device_class in {"running", "power", None}
        and named(entity)
    ]
    label = next(
        (word for word in roles.subject_words if normalize_for_compare(word) == appliance),
        appliance,
    )
    article = next(
        (word.casefold() for word in roles.subject_words if word.casefold() in _DEFINITE_WORDS),
        "",
    )
    spoken = f"{article} {label}".strip()
    status = [
        entity for entity in entities
        if entity.domain == "sensor" and entity.device_class is None and not entity.unit
        and named(entity)
        and entity.state.casefold() in APPLIANCE_RUNNING_STATES | APPLIANCE_FINISHED_STATES
    ]
    if len(status) == 1:
        # An observed program status is stronger evidence than a power value.
        trigger = TriggerModel(
            type=TriggerType.STATE,
            target=TriggerTarget(domain="sensor", entity_id=status[0].entity_id),
            state=SemanticState.INACTIVE,
            raw_to=tuple(sorted(APPLIANCE_FINISHED_STATES)),
            appliance_label=spoken,
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger, candidates=(status[0],), roles=roles)
    if len(power) == 1:
        trigger = TriggerModel(
            type=TriggerType.NUMERIC_STATE,
            target=TriggerTarget(domain="sensor", device_class="power", entity_id=power[0].entity_id),
            comparator=NumericComparator.BELOW,
            threshold=_APPLIANCE_IDLE_WATTS,
            for_seconds=_APPLIANCE_IDLE_SECONDS,
            appliance_label=spoken,
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger, candidates=(power[0],), roles=roles)
    if len(running) == 1:
        trigger = TriggerModel(
            type=TriggerType.STATE,
            target=TriggerTarget(domain="binary_sensor", entity_id=running[0].entity_id),
            state=SemanticState.OFF,
            appliance_label=spoken,
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger, candidates=(running[0],), roles=roles)
    return GroundedEvent(
        GroundingStatus.NOT_FOUND,
        question=(
            f"Für „{label}“ finde ich keinen Leistungs- oder Betriebssensor, "
            "an dem ich das Ende erkennen kann."
        ),
        roles=roles,
    )


_SPEAKER_WORDS = frozenset({"ich", "mich"})


def _ground_presence(roles: EventRoles, entities: Sequence[EntitySnapshot]) -> GroundedEvent:
    """"ich komme nach Hause" / "Julia verlässt das Haus" -> PRESENCE.

    "ich" stays the *speaker* until the conversation binds it to the
    authenticated user's person; a name must equal a person entity's name.
    """
    keys = [normalize_for_compare(word) for word in roles.subject_words]
    keys = [key for key in keys if key not in {"der", "die", "das", "unser", "unsere", "mein", "meine"}]
    if keys and all(key in _SPEAKER_WORDS for key in keys):
        trigger = TriggerModel(
            type=TriggerType.PRESENCE, target=TriggerTarget(domain="person"), zone_id="home",
            presence_event=roles.presence, presence_of_speaker=True,
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger, roles=roles)
    spoken = " ".join(keys)
    people = [
        entity for entity in entities
        if entity.domain == "person" and spoken and spoken in _name_keys(entity)
    ]
    if len(people) != 1:
        return GroundedEvent(GroundingStatus.NOT_APPLICABLE, roles=roles)
    person = people[0]
    trigger = TriggerModel(
        type=TriggerType.PRESENCE, target=TriggerTarget(domain="person", entity_id=person.entity_id),
        zone_id="home", presence_event=roles.presence,
    )
    return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger, candidates=(person,), roles=roles)


def _project(
    roles: EventRoles,
    subject: SubjectReading,
    candidates: Sequence[EntitySnapshot],
    entities: Sequence[EntitySnapshot],
) -> GroundedEvent:
    # "eine Tür" means any door in the house; a room constraint that was
    # never spoken must not narrow it (it only coincides today).
    target = target_for(
        candidates,
        entities,
        area_spoken=subject.area_id is not None or subject.quantifier is not Quantifier.ANY,
    )
    domains = {entity.domain for entity in candidates}
    if roles.value is None:
        assert roles.state is not None
        if roles.direction is not None:
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="direction_without_position",
                                 subject=subject, roles=roles)
        trigger = TriggerModel(
            type=TriggerType.STATE, target=target, state=roles.state, for_seconds=roles.for_seconds,
            absent_state=roles.absent,
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger,
                             candidates=tuple(candidates), subject=subject, roles=roles)

    if len(domains) != 1:
        return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="mixed_domains",
                             subject=subject, roles=roles)
    domain = next(iter(domains))
    unit = roles.unit or ValueUnit.NONE
    measurement: MeasurementProperty | None = None
    threshold = roles.value
    if domain == "sensor":
        if roles.half or not all(_sensor_unit_ok(entity, unit) for entity in candidates):
            if unit in _POWER_UNITS or unit in _ENERGY_UNITS:
                # "10 kWh" against a power sensor: an amount is no rate.
                names = ", ".join(f"„{entity.friendly_name}“" for entity in candidates[:2])
                measures = sorted({entity.unit or "ohne Einheit" for entity in candidates})
                return GroundedEvent(
                    GroundingStatus.UNSUPPORTED, reason="unit_mismatch",
                    question=(
                        f"{names} misst in {', '.join(measures)}, nicht in "
                        f"{(_POWER_UNITS.get(unit) or _ENERGY_UNITS[unit])[0]}. "
                        "Leistung (W) und Energie (kWh) sind verschiedene Größen – welchen Sensor meinst du?"
                    ),
                    subject=subject, roles=roles,
                )
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="unit_mismatch",
                                 subject=subject, roles=roles)
        units = {entity.unit for entity in candidates}
        if len(units) == 1:
            threshold = _in_sensor_unit(roles.value, unit, candidates[0])
    else:
        measurement = percent_property_for_domain(domain)
        if measurement is None or unit is ValueUnit.DEGREE:
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="no_numeric_property",
                                 subject=subject, roles=roles)
        if roles.half and measurement is not MeasurementProperty.COVER_POSITION:
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="half_without_cover",
                                 subject=subject, roles=roles)
        if unit is ValueUnit.NONE and measurement is not MeasurementProperty.COVER_POSITION:
            # "bei fünfzig" only has a percentage meaning for covers.
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="bare_number",
                                 subject=subject, roles=roles)
        assert roles.value is not None
        if not is_valid_value(measurement, roles.value):
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="out_of_range",
                                 subject=subject, roles=roles)
    if roles.direction is not None and measurement is not MeasurementProperty.COVER_POSITION:
        return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="direction_without_position",
                             subject=subject, roles=roles)
    trigger = TriggerModel(
        type=TriggerType.NUMERIC_STATE,
        target=target,
        comparator=roles.comparator or NumericComparator.EQUAL,
        threshold=threshold,
        measurement=measurement,
        direction=roles.direction,
        for_seconds=roles.for_seconds,
    )
    return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger,
                         candidates=tuple(candidates), subject=subject, roles=roles)


def restrict_to(
    grounded: GroundedEvent, chosen: EntitySnapshot, entities: Sequence[EntitySnapshot]
) -> GroundedEvent:
    """Resolve a clarified ambiguity to the one chosen candidate."""
    assert grounded.roles is not None and grounded.subject is not None
    return _project(grounded.roles, grounded.subject, (chosen,), entities)


def choose_candidate(
    reply: str, candidates: Sequence[EntitySnapshot]
) -> EntitySnapshot | None:
    """"Die linke." / "den rechten" / "Büro Rollladen links" -> one candidate."""
    words = [word.strip(",.;:!?").casefold() for word in reply.split()]
    keys = [
        _SIDE_WORDS.get(word, normalize_for_compare(word))
        for word in words
        if word and word not in _DEFINITE_WORDS | _ANY_WORDS | {"meine", "ich", "nein", "bitte", "ja"}
    ]
    if not keys:
        return None
    exact = [entity for entity in candidates if normalize_for_compare(" ".join(keys)) in _name_keys(entity)]
    if len(exact) == 1:
        return exact[0]
    matching = [
        entity for entity in candidates
        if all(
            any(re.search(rf"(?:^|\s){re.escape(key)}(?:$|\s)", name) for name in _name_keys(entity))
            for key in keys
        )
    ]
    return matching[0] if len(matching) == 1 else None


_SELECTION_BLOCKERS = frozenset({
    "wenn", "sobald", "falls", "schalte", "mach", "schick", "benachrichtige", "sag", "wie",
    "was", "warum", "welche", "welcher", "abbrechen", "stopp", "nein", "ja",
})


def looks_like_selection_reply(reply: str) -> bool:
    """A short noun-phrase answer ("Die mittlere.") rather than a new request."""
    words = [word.strip(",.;:!?").casefold() for word in reply.split()]
    return 0 < len(words) <= 4 and not any(word in _SELECTION_BLOCKERS for word in words)


__all__ = (
    "looks_like_selection_reply",
    "GroundedEvent",
    "GroundingStatus",
    "Quantifier",
    "SubjectReading",
    "choose_candidate",
    "ground_event",
    "read_subject",
    "subject_candidates",
    "restrict_to",
    "target_for",
)
