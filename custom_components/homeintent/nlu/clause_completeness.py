"""Completeness of a coordinated write command (7.13.1).

A write plan may only run when every coordinated part of the sentence is
reflected in it.  Two structural gaps were silently dropped before:

* a coordinated part without content ("Mach das Licht im Büro aus und dann
  das.") - an unfinished clause, never a reason to run the first one;
* a coordinated place that none of the planned targets lies in ("Pausiere
  das Radio in der Küche und im Wohnzimmer") - the named target set would
  silently shrink;
* a coordinated target phrase no planned target covers ("Öffne die
  Rollläden im Wohnzimmer und das Garagentor" opened the shutters and
  dropped the garage door).

Both are read from word classes (joiners, fillers, references, the place
lexicon, registry names); there is no sentence template.  The check never
widens a plan: it only refuses one.  Home-Assistant-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .device_ontology import entity_genera
from .language_frontend import tokenize_language
from .place_model import PlaceKind
from .self_correction import registry_name_table, utterance_fields
from .semantic_utterance import SpeechAct, analyse_utterance
from .semantic_exclusion import split_exclusion

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
    unbound_targets: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.dangling and not self.unbound_places and not self.unbound_targets

    @property
    def parts(self) -> tuple[str, ...]:
        return self.dangling + self.unbound_places + self.unbound_targets


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
    if analyse_utterance(text).speech_act is SpeechAct.AUTOMATION:
        # Trigger and condition clauses name entities that are observed, not
        # acted on; the automation reader validates its own clauses.
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
    # Places of an exception ("… außer im Flur") restrict, they bind nothing;
    # a place whose named members were all excepted ("in Küche und Flur …
    # außer dem Flurlicht") was considered by the reading, not dropped.
    positive, exceptions = split_exclusion(text)
    names = registry_name_table(entities)
    entities_by_id = {entity.entity_id: entity for entity in entities}
    excepted = [
        entity
        for exception in exceptions
        for entity_id in names.by_words.get(
            tuple(normalize_for_compare(exception).replace("-", " ").split()), []
        )
        if (entity := entities_by_id.get(entity_id)) is not None
    ]
    located = [
        entity for entity in (*targets, *excepted) if entity.area_id or entity.floor_id
    ]
    fields, _rest = utterance_fields(positive, entities, names=names)
    # A coordinated target phrase ("… und das Garagentor") must be covered
    # by a planned target: by one of its registry entities, or for a genus
    # noun by a target of that genus.  Otherwise the named set silently
    # shrank.
    unbound_targets: list[str] = []
    target_ids = {entity.entity_id for entity in targets}
    target_genera = {genus for entity in targets for genus in entity_genera(entity)}
    for item in fields:
        if item.kind != "target":
            continue
        if item.registry_ids:
            if not target_ids & set(item.registry_ids):
                unbound_targets.append(item.text)
        elif item.genera and not target_genera & set(item.genera):
            unbound_targets.append(item.text)
    if located:
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
    return SentenceCompleteness(tuple(dangling), tuple(unbound), tuple(unbound_targets))
