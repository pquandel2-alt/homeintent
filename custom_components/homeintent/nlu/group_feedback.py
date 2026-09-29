"""Why a group percentage command found no targets (7.7 B5).

"Fahre alle Rollläden im Erdgeschoss halb runter" names a device kind and a
place, but no device. When nothing in that place can take the position, the
turn explains the live-data reason (unknown place, no selected devices, no
position/brightness control) instead of a generic error. This replaces the
explanation path of the deleted percentage grammar: a word-level reading of
kind, quantity and place, no sentence pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..areas import AreaResolveStatus, resolve_area_name
from ..entities import EntitySnapshot, normalize_for_compare
from ..floors import FloorResolveStatus, resolve_floor_name
from .constraint_resolver import Constraints, resolve_candidates
from .domain_operations import DOMAIN_WORDS
from .parse_outcome import ParseFailureReason, UnderstandingFeedback

_VERBS = frozenset({"fahre", "fahr", "mach", "mache", "stelle", "stell", "setze", "setz"})
_LEADING = frozenset({"kannst", "du", "bitte"})
_QUANTIFIERS = frozenset({"alle", "saemtliche", "beide", "beiden"})
_LOCATIVES = (("in", "der"), ("in", "dem"), ("im",), ("am",), ("beim",))
_STOP = frozenset({"auf", "halb", "zur", "die", "den", "das"}) | _QUANTIFIERS
_KINDS = {
    normalize_for_compare(word): domain
    for domain in ("cover", "light") for word in DOMAIN_WORDS[domain]
}


def _place(words: Sequence[str], spoken: Sequence[str]) -> str | None:
    for index in range(len(words)):
        for locative in _LOCATIVES:
            end = index + len(locative)
            if tuple(words[index:end]) != locative:
                continue
            place: list[str] = []
            for word, original in zip(words[end:], spoken[end:]):
                if word in _STOP or word in _KINDS:
                    break
                place.append(original)
            if place:
                return " ".join(place)
    return None


def group_percentage_feedback(
    text: str, entities: Sequence[EntitySnapshot],
) -> UnderstandingFeedback | None:
    spoken = text.strip(" .!?").split()
    words = [normalize_for_compare(word) for word in spoken]
    start = 0
    while start < len(words) and words[start] in _LEADING:
        start += 1
    if start >= len(words) or words[start] not in _VERBS:
        return None
    domain = next((_KINDS[word] for word in words if word in _KINDS), None)
    if domain is None:
        return None
    location = _place(words, spoken)
    if location is None:
        return None
    entity_list = list(entities)
    floor = resolve_floor_name(location, entity_list)
    area = resolve_area_name(location, entity_list)
    area_id = floor_id = None
    if floor.status is FloorResolveStatus.OK:
        floor_id = floor.floor_id
    elif area.status is AreaResolveStatus.OK:
        area_id = area.area_id
    elif floor.status is FloorResolveStatus.AMBIGUOUS:
        return UnderstandingFeedback(
            ParseFailureReason.AMBIGUOUS_TARGET, f"Die Etage „{location}“ ist nicht eindeutig.",
        )
    elif area.status is AreaResolveStatus.AMBIGUOUS:
        return UnderstandingFeedback(
            ParseFailureReason.AMBIGUOUS_TARGET, f"Der Bereich „{location}“ ist nicht eindeutig.",
        )
    else:
        return UnderstandingFeedback(
            ParseFailureReason.UNKNOWN_ENTITY,
            f"Ich kenne den Bereich oder die Etage „{location}“ nicht. "
            "Bitte ordne die Geräte in Home Assistant einem Bereich und den Bereich einer Etage zu.",
        )
    matches = resolve_candidates(
        entity_list, Constraints(domain=domain, area_id=area_id, floor_id=floor_id),
    )
    noun = "Rollläden" if domain == "cover" else "Lichter"
    if not matches:
        return UnderstandingFeedback(
            ParseFailureReason.UNKNOWN_ENTITY,
            f"Ich finde keine für HomeIntent ausgewählten {noun} in „{location}“.",
        )
    capability = "POSITION" if domain == "cover" else "BRIGHTNESS"
    if not any(capability in entity.capabilities for entity in matches):
        detail = "Positionssteuerung" if domain == "cover" else "Helligkeitssteuerung"
        return UnderstandingFeedback(
            ParseFailureReason.UNSUPPORTED_CAPABILITY,
            f"Die {noun} in „{location}“ melden keine unterstützte {detail}.",
        )
    return None
