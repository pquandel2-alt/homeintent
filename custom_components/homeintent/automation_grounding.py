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

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Sequence

from .automation_language import EventRoles, ValueUnit
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.automation_lexicon import NounClass, noun_class, split_compound
from .nlu.automation_model import NumericComparator, TriggerModel, TriggerTarget, TriggerType
from .nlu.constraint_resolver import Constraints, resolve_candidates
from .nlu.german_morphology import GrammaticalGender, dative_location_phrase
from .nlu.measurement import (
    MeasurementProperty,
    is_valid_value,
    percent_property_for_domain,
)
from .nlu.semantic_state import SemanticState


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


_ANY_WORDS = frozenset({
    "ein", "eine", "einer", "eines", "einem", "einen", "irgendein", "irgendeine",
    "irgendeiner", "irgendeines", "irgendeinem", "irgendwelche", "jede", "jeder",
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
_PLURAL_PARTITIVE_RE = re.compile(r"^(?:einer|eines|eine|irgendeiner)\s+(?:der|von\s+den)$")


def _area_index(entities: Sequence[EntitySnapshot]) -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for entity in entities:
        if entity.area_id is None or not entity.area_name:
            continue
        for name in (entity.area_name, *entity.area_aliases):
            key = normalize_for_compare(name).strip()
            if key:
                index.setdefault(key, (entity.area_id, entity.area_name))
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
    while index < len(tokens):
        key = keys[index]
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
        entry = noun_class(key)
        if entry is not None and noun is None:
            noun, noun_word = entry, tokens[index]
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
            if prefix_area is not None:
                area = prefix_area
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
        implicit=implicit and noun is None,
    )


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


def _class_candidates(
    subject: SubjectReading, entities: Sequence[EntitySnapshot]
) -> list[EntitySnapshot]:
    noun = subject.noun
    assert noun is not None
    return [
        entity for entity in entities
        if entity.domain == noun.domain
        and (noun.device_class is None or entity.device_class == noun.device_class)
        and (subject.area_id is None or entity.area_id == subject.area_id)
    ]


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
    which = _GENDER_WHICH[noun.gender] if noun is not None else "Welches"
    location = f" {dative_location_phrase(subject.area_name)}" if subject.area_name else ""
    options = " oder ".join(entity.friendly_name for entity in candidates[:4])
    return f"{which} {word}{location} meinst du: {options}?"


def _missing_subject_question(roles: EventRoles) -> str:
    if roles.half:
        return "Was soll halb geöffnet sein?"
    if roles.value is None:
        return "Welches Gerät meinst du?"
    number = f"{roles.value:g}".replace(".", ",")
    unit = {ValueUnit.PERCENT: " Prozent", ValueUnit.DEGREE: " Grad"}.get(roles.unit or ValueUnit.NONE, "")
    comparator = roles.comparator or NumericComparator.EQUAL
    if comparator is NumericComparator.EQUAL:
        return f"Was soll {number}{unit} erreichen?"
    word = {
        NumericComparator.ABOVE: "über", NumericComparator.BELOW: "unter",
        NumericComparator.AT_LEAST: "mindestens", NumericComparator.AT_MOST: "höchstens",
    }[comparator]
    return f"Was soll {word} {number}{unit} liegen?"


def _sensor_unit_ok(entity: EntitySnapshot, unit: ValueUnit) -> bool:
    if unit is ValueUnit.NONE:
        return True
    if unit is ValueUnit.DEGREE:
        return entity.unit in {"°C", "°F", "K"}
    return entity.unit == "%"


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
    if roles.value is None and roles.state is None:
        return GroundedEvent(GroundingStatus.NOT_APPLICABLE, roles=roles)
    subject = read_subject(roles.subject_words, entities)
    if roles.motion and subject.noun is None:
        subject = SubjectReading(
            noun=noun_class("bewegungsmelder"), noun_word="Bewegungsmelder",
            area_id=subject.area_id, area_name=subject.area_name,
            unknown_location=subject.unknown_location,
            modifiers=tuple(m for m in subject.modifiers if m not in {"bewegung", "eine"}),
            quantifier=Quantifier.ANY, implicit=False,
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
    if subject.quantifier is Quantifier.ALL:
        return GroundedEvent(
            GroundingStatus.UNSUPPORTED, reason="aggregate", subject=subject, roles=roles
        )

    if subject.noun is None and not subject.modifiers and roles.value is not None:
        # "im Büro 50 Prozent erreicht": a room is not a measurable subject.
        subject = SubjectReading(
            noun=None, noun_word=None, area_id=subject.area_id, area_name=subject.area_name,
            unknown_location=None, modifiers=(), quantifier=subject.quantifier, implicit=True,
        )
    if subject.noun is None and subject.implicit:
        if roles.value is not None and roles.unit is ValueUnit.DEGREE:
            subject = SubjectReading(
                noun=noun_class("temperatur"), noun_word="Temperatur",
                area_id=subject.area_id, area_name=subject.area_name, unknown_location=None,
                modifiers=subject.modifiers, quantifier=Quantifier.DEFINITE, implicit=False,
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
        if not candidates:
            # Not a device we can type - leave it to the established parsers
            # (presence "Julia", time, sun, ...).
            return GroundedEvent(GroundingStatus.NOT_APPLICABLE, subject=subject, roles=roles)

    if roles.state is not None and roles.value is None:
        candidates = [entity for entity in candidates if _state_ok(entity, roles.state)]
    if not candidates:
        where = f" {dative_location_phrase(subject.area_name)}" if subject.area_name else ""
        noun = (subject.noun_word or "dieses Gerät").strip("-")
        return GroundedEvent(
            GroundingStatus.NOT_FOUND,
            question=f"Ich finde{where} kein passendes Gerät für „{noun}“. Welches Gerät meinst du?",
            subject=subject, roles=roles,
        )
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
            type=TriggerType.STATE, target=target, state=roles.state, for_seconds=roles.for_seconds
        )
        return GroundedEvent(GroundingStatus.RESOLVED, trigger=trigger,
                             candidates=tuple(candidates), subject=subject, roles=roles)

    if len(domains) != 1:
        return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="mixed_domains",
                             subject=subject, roles=roles)
    domain = next(iter(domains))
    unit = roles.unit or ValueUnit.NONE
    measurement: MeasurementProperty | None = None
    if domain == "sensor":
        if roles.half or not all(_sensor_unit_ok(entity, unit) for entity in candidates):
            return GroundedEvent(GroundingStatus.UNSUPPORTED, reason="unit_mismatch",
                                 subject=subject, roles=roles)
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
        threshold=roles.value,
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
    "restrict_to",
    "target_for",
)
