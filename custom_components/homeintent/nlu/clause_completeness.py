"""Completeness of a coordinated write command (7.13.1).

A write plan may only run when every coordinated part of the sentence is
reflected in it.  Two structural gaps were silently dropped before:

* a coordinated part without content ("Mach das Licht im Büro aus und dann
  das.") - an unfinished clause, never a reason to run the first one;
* a coordinated place that none of the planned targets lies in ("Pausiere
  das Radio in der Küche und im Wohnzimmer") - the named target set would
  silently shrink.

Both are read from word classes (joiners, fillers, references, the place
lexicon, registry names); there is no sentence template.  The check never
widens a plan: it only refuses one.  Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot
from .language_frontend import tokenize_language
from .place_model import PlaceKind
from .self_correction import utterance_fields

__all__ = ("SentenceCompleteness", "analyse_write_completeness")

_JOINERS = frozenset({
    "und", "oder", "sowie", "dann", "danach", "anschliessend", "ausserdem", "zudem",
})
# Words that carry no operation, target, place or value of their own.
_EMPTY = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
    "es", "ihn", "sie", "ihm", "ihr", "dies", "diese", "diesen", "dieses", "dort", "da",
    "bitte", "danke", "mal", "eben", "doch", "halt", "ja", "also", "gleich", "jetzt",
    "noch", "auch", "ebenfalls", "bitteschoen", "dankeschoen",
})
_COURTESY = frozenset({"bitte", "danke", "bitteschoen", "dankeschoen"})


@dataclass(frozen=True)
class SentenceCompleteness:
    dangling: tuple[str, ...] = ()
    unbound_places: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.dangling and not self.unbound_places

    @property
    def parts(self) -> tuple[str, ...]:
        return self.dangling + self.unbound_places


def analyse_write_completeness(
    text: str,
    entities: Sequence[EntitySnapshot],
    targets: Sequence[EntitySnapshot],
    scoped_places: frozenset[str] = frozenset(),
) -> SentenceCompleteness:
    """Coordinated parts of ``text`` that the planned ``targets`` leave out.

    ``scoped_places`` are the area/floor ids the reading itself bound as its
    location scope ("alle Lichter in Küche und Flur"): an explicitly scoped
    place without members is an honest empty part of a universal scope, not
    a silently dropped one.
    """
    tokens = [token for token in tokenize_language(text) if token.is_word or token.is_number]
    joiners = [index for index, token in enumerate(tokens) if token.canonical in _JOINERS]
    if not joiners:
        return SentenceCompleteness()
    dangling: list[str] = []
    # Parts after a joiner run up to the next joiner; "und dann" is one joiner.
    index = 0
    while index < len(tokens):
        if tokens[index].canonical not in _JOINERS:
            index += 1
            continue
        first_joiner = index
        while index < len(tokens) and tokens[index].canonical in _JOINERS:
            index += 1
        part_start = index
        while index < len(tokens) and tokens[index].canonical not in _JOINERS:
            index += 1
        words = [token.canonical for token in tokens[part_start:index]]
        if first_joiner == 0:
            continue  # a leading "Und …" joins the sentence to the dialog
        if all(word in _EMPTY for word in words) and not (
            words and all(word in _COURTESY for word in words)
        ):
            last = tokens[index - 1]
            dangling.append(text[tokens[first_joiner].start:last.end])
    unbound: list[str] = []
    located = [entity for entity in targets if entity.area_id or entity.floor_id]
    if located:
        fields, _rest = utterance_fields(text, list(entities))
        for item in fields:
            place = item.place
            if item.kind != "place" or place is None:
                continue
            if place.kind not in {PlaceKind.AREA, PlaceKind.FLOOR}:
                continue
            if (place.area_ids | place.floor_ids) & scoped_places:
                continue
            if not any(place.contains(entity) for entity in located):
                unbound.append(item.text)
    return SentenceCompleteness(tuple(dangling), tuple(unbound))
