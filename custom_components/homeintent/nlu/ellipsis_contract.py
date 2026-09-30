"""The ellipsis contract (7.7.1 A3).

A follow-up turn ("Den rechten runter.", "Und das Deckenlicht aus.",
"Morgen früh wieder an.", "Oben auch.") takes over from its predecessor only
the fields it does **not** name itself:

* a newly named object, side, place, value or time replaces that field; it
  is never ignored,
* a change of place carries the kind/role of the predecessor and never
  widens the set (no floor set instead of one device),
* a time makes the follow-up time-bound; it never runs now,
* a word in object position that names nothing HomeIntent knows ("Und
  Deckenfluter aus." when the Deckenfluter is not exposed) is an unknown new
  object: the previous target is never substituted for it (7.8.1).

``violation`` checks a context reading against these rules. Every context
reader of the conversation passes through it, so no reader can substitute
the previous target for a newly named one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .device_ontology import GENERA, entity_genera
from .self_correction import utterance_fields

__all__ = ("EllipsisFields", "ellipsis_fields", "violation")

_SIDE_WORDS = {
    "left": ("links", "linke"), "right": ("rechts", "rechte"),
    "upper": ("oben", "obere"), "lower": ("unten", "untere"),
    "front": ("vorne", "vordere"), "back": ("hinten", "hintere"),
    "big": ("gross",), "small": ("klein",), "middle": ("mittlere", "mitte"),
}
_ALL_WORDS = frozenset({"alle", "allen", "saemtliche", "beide", "beiden", "ueberall"})
# Rest words that never name an object of a follow-up ("Und nochmal.",
# "Die andere auch.", "Ruhig wieder aus.").
_NEUTRAL_REST = frozenset({
    "noch", "nochmal", "nochmals", "mal", "einmal", "andere", "anderen", "anderer", "anderes",
    "wieder", "auch", "bitte", "doch", "eben", "schnell", "kurz", "gleich", "sofort", "jetzt",
    "dann", "und", "aus", "an", "ein", "so", "genauso", "ebenso", "ebenfalls", "danke", "okay",
    "gerne", "halt", "einfach", "ruhig", "mehr", "weniger", "etwas", "bisschen", "ganz",
    "komplett", "zurueck", "oder", "aber", "dafuer", "bei", "fuer", "mit", "von", "zu",
})


@dataclass(frozen=True)
class EllipsisFields:
    targets: tuple[tuple[tuple[str, ...], tuple[str, ...], str | None], ...]  # (registry ids, genera, modifier)
    sides: frozenset[str]
    places: tuple[object, ...]
    value: bool
    time: bool
    all: bool
    # Words in the sentence that name nothing HomeIntent knows.
    unknown: tuple[str, ...] = ()

    @property
    def names_object(self) -> bool:
        return bool(self.targets or self.sides)


_SENSOR_GENERA = frozenset(item.key for item in GENERA if item.sensor)


def _first_word_start(text: str) -> int:
    stripped = text.lstrip(" \t\"„‚'«»")
    return len(text) - len(stripped)


def _unknown_objects(text: str, fields: Sequence[object], rest: Sequence[object]) -> tuple[str, ...]:
    """Unknown nouns in object position. A noun is capitalised; inside the
    sentence that tells it from verbs and adverbs ("Und Deckenfluter aus.").
    A capitalised first word counts only in the verbless short form
    "Deckenfluter aus." - one unknown word, no reference, no question."""
    words = [
        token for token in rest
        if getattr(token, "is_word", False) and len(getattr(token, "key", "")) >= 3
        and getattr(token, "key", "") not in _NEUTRAL_REST and getattr(token, "text", "")[:1].isupper()
    ]
    first = _first_word_start(text)
    inner = [token for token in words if getattr(token, "start", 0) > first]
    if inner:
        return tuple(getattr(token, "text", "") for token in inner)
    kinds = {getattr(item, "kind", "") for item in fields}
    if (
        len(words) == 1 and len([token for token in rest if getattr(token, "is_word", False)]) == 1
        and "reference" not in kinds and "verb" not in kinds and kinds & {"particle", "value"}
        and not text.rstrip().endswith("?")
    ):
        return (getattr(words[0], "text", ""),)
    return ()


def ellipsis_fields(text: str, entities: Sequence[EntitySnapshot]) -> EllipsisFields:
    fields, rest = utterance_fields(text, entities)
    # "die Temperatur auf 22 Grad": a measured property names what changes,
    # not a new object.
    fields = [
        item for item in fields
        if not (item.kind == "target" and item.genera and set(item.genera) <= _SENSOR_GENERA)
    ]
    words = {normalize_for_compare(word) for word in text.split()}
    return EllipsisFields(
        targets=tuple(
            (item.registry_ids, item.genera, item.modifier)
            for item in fields if item.kind == "target"
        ),
        sides=frozenset(
            item.side for item in fields if item.kind in {"target", "feature"} and item.side
        ),
        places=tuple(item.place for item in fields if item.kind == "place" and item.place is not None),
        value=any(item.kind == "value" for item in fields),
        time=any(item.kind == "time" for item in fields),
        all=bool({word.strip(".,!?") for word in words} & _ALL_WORDS),
        unknown=_unknown_objects(text, fields, rest),
    )


def _matches_target(entity: EntitySnapshot, target: tuple[tuple[str, ...], tuple[str, ...], str | None]) -> bool:
    registry_ids, genera, modifier = target
    if registry_ids:
        return entity.entity_id in registry_ids
    if genera and not (entity_genera(entity) & set(genera)):
        return False
    if modifier:
        names = " ".join(
            normalize_for_compare(name) for name in (entity.friendly_name, *entity.aliases, entity.area_name or "")
        ).replace(" ", "")
        stem = modifier.rstrip("n") if len(modifier) > 4 else modifier
        if stem not in names:
            return False
    return True


def _has_side(entity: EntitySnapshot, side: str) -> bool:
    name = normalize_for_compare(" ".join((entity.friendly_name, *entity.aliases)))
    return any(word in name for word in _SIDE_WORDS.get(side, ()))


def violation(
    fields: EllipsisFields,
    written: Iterable[EntitySnapshot],
    previous: Sequence[EntitySnapshot],
    *,
    immediate: bool = True,
) -> str | None:
    """Why a context reading breaks the contract, or ``None``."""
    targets = list(written)
    if fields.time and immediate:
        return "time"
    if fields.unknown and not fields.targets:
        # A new object HomeIntent does not know is never the previous one.
        return "object"
    for entity in targets:
        if fields.targets and not any(_matches_target(entity, target) for target in fields.targets):
            return "object"
        for side in fields.sides:
            if not _has_side(entity, side):
                return "side"
    previous_ids = {entity.entity_id for entity in previous}
    if (
        previous and not fields.all and not fields.targets
        and len(targets) > len(previous)
        and not previous_ids >= {entity.entity_id for entity in targets}
    ):
        return "widened"
    return None
