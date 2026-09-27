"""Discourse: bind elliptical and referential turns to the conversation.

"Etwas heller bitte", "noch mehr", "die andere", "mach alle aus", "mach's da
wärmer", "dann mach die Heizung da aus", "und das Bürolicht auch" have no
complete target of their own.  They combine an operation from the current
turn with a referent from the conversation context:

* pronouns / bare quantifiers ("es", "sie", "die", "alle", "beide")
  -> the last target set or the last answer's result set;
* "der/die/das andere" -> the one other device of the same kind at the
  same place (or the remaining clarification candidate);
* "da", "dort" -> the last place; "hier" -> the speaker's room;
* "auch" / "und <target>" -> the previous operation on a new target;
* "noch mehr" / "weiter" -> the previous comparative step again.

The rules are over word classes and the typed context, never over whole
sentences.  The result is compiled by the same genus compiler as every
other command, so validator, policy and confirmations apply unchanged.
Context only lives as long as the conversation TTL.
"""

from __future__ import annotations

from typing import Sequence

from ..areas import AreaSnapshot
from ..entities import EntitySnapshot, normalize_for_compare
from .context import ConversationContext
from .device_ontology import entity_genera
from .language_frontend import tokenize_language
from .normalize import normalize
from .ontology_compiler import (
    ClauseMeaning,
    OntologyCommand,
    _FILLER_WORDS,
    _compile_clauses,
    _operation_words,
)
from .place_model import Place, PlaceKind, build_place_lexicon
from .semantic_catalog import DEGREE_WORDS, PROPERTY_GENUS
from .semantic_compiler import _percent, _temperature
from .target_resolution import Quantity, TargetDescription, _name_index, describe_with_residue

__all__ = ("compile_discourse",)

_REFERENTIAL = frozenset({
    "es", "ihn", "sie", "ihm", "ihnen", "das", "den", "die", "dem", "der", "alle", "allen",
    "beide", "beiden", "diese", "dieses", "diesen", "dieser", "davon", "mach's", "machs",
})
_OTHER = frozenset({"andere", "anderen", "anderer", "anderes", "zweite", "zweiten"})
_SIDES = frozenset({"links", "rechts", "vorne", "hinten", "oben", "unten", "gross", "klein"})
_DEICTIC_PLACE = frozenset({"da", "dort", "dorthin", "drin", "drueben"})
_ALSO = frozenset({"auch", "ebenfalls", "genauso"})
_REPEAT = frozenset({"mehr", "weiter", "nochmal", "nochmals"})
_GLUE = frozenset({"und", "dann", "noch", "wieder", "jetzt", "bitte", "etwas", "bisschen", "ein", "mal", "s"})
_INTENT_ACTIONS = {
    "HassTurnOn": "turn_on", "HassTurnOff": "turn_off", "HassOpenCover": "open",
    "HassCloseCover": "close", "HassMediaPlay": "play", "HassMediaPause": "pause",
    "HassVacuumStart": "start", "HassVacuumStop": "stop", "HassOpenValve": "open",
    "HassCloseValve": "close", "HassLock": "lock", "HassUnlock": "unlock",
}
_INTENT_DEGREES = {
    "HassLightBrighten": ("brightness", 1), "HassLightDim": ("brightness", -1),
    "HassClimateIncreaseTemperature": ("temperature", 1),
    "HassClimateDecreaseTemperature": ("temperature", -1),
    "HassFanIncreaseSpeed": ("speed", 1), "HassFanDecreaseSpeed": ("speed", -1),
}
_SERVICE_ACTIONS = {
    ("media_player", "turn_on"): "turn_on", ("media_player", "turn_off"): "turn_off",
}
_SERVICE_DEGREES = {
    ("media_player", "volume_up"): ("volume", 1), ("media_player", "volume_down"): ("volume", -1),
}


def _previous_operation(
    context: ConversationContext,
) -> tuple[frozenset[str], tuple[str, int] | None]:
    command = context.last_command
    if command is None:
        return frozenset(), None
    intent = command.intent
    if intent in _INTENT_ACTIONS:
        return frozenset({_INTENT_ACTIONS[intent]}), None
    if intent in _INTENT_DEGREES:
        return frozenset(), _INTENT_DEGREES[intent]
    parameters = command.parameters
    key = (str(parameters.get("service_domain")), str(parameters.get("service_name")))
    if key in _SERVICE_ACTIONS:
        return frozenset({_SERVICE_ACTIONS[key]}), None
    if key in _SERVICE_DEGREES:
        return frozenset(), _SERVICE_DEGREES[key]
    return frozenset(), None


def _context_place(
    context: ConversationContext, entities: Sequence[EntitySnapshot]
) -> Place | None:
    lexicon = build_place_lexicon(entities)
    if context.last_area is not None:
        place = lexicon.place_for_area(context.last_area.area_id)
        if place is not None:
            return place
    areas = {entity.area_id for entity in context.last_entities if entity.area_id}
    if len(areas) == 1:
        return lexicon.place_for_area(next(iter(areas)))
    return None


def _others(
    context: ConversationContext, entities: Sequence[EntitySnapshot]
) -> tuple[EntitySnapshot, ...]:
    """The remaining device(s) of the same kind at the same place."""
    last = {entity.entity_id for entity in context.last_entities}
    clarification = context.pending_clarification
    if clarification is not None:
        return tuple(item for item in clarification.candidates if item.entity_id not in last)
    if not context.last_entities:
        return ()
    reference = context.last_entities[0]
    kinds = entity_genera(reference) - {"device"}
    siblings = tuple(
        entity for entity in entities
        if entity.entity_id not in last
        and entity.domain == reference.domain
        and entity.area_id == reference.area_id
        and (not kinds or entity_genera(entity) & kinds)
    )
    if len(siblings) > 1:
        # "die andere" contrasts with a named partner: prefer the device that
        # shares the name apart from its distinguishing word ("links").
        stem = set(normalize_for_compare(reference.friendly_name).split()) - _SIDES
        paired = tuple(
            entity for entity in siblings
            if stem and stem <= set(normalize_for_compare(entity.friendly_name).split())
        )
        if paired:
            return paired
    return siblings


def compile_discourse(
    document: object,
    entities: Sequence[EntitySnapshot],
    context: ConversationContext | None,
    *,
    source_area: AreaSnapshot | None = None,
) -> OntologyCommand | None:
    """Compile a context-dependent turn, or ``None`` if it is self-contained."""
    if context is None or (not context.last_entities and context.last_area is None):
        return None
    text = getattr(getattr(document, "utterance"), "normalized_text")
    tokens = tokenize_language(text)
    words = [token.canonical for token in tokens if token.is_word]
    if not words:
        return None
    word_set = set(words)
    actions, operation_words = _operation_words(normalize(text))
    degrees = [DEGREE_WORDS[word] for word in words if word in DEGREE_WORDS]
    degree = degrees[0] if len(set(degrees)) == 1 else None
    temperature = _temperature(text)
    percent = None if temperature is not None else _percent(text)
    previous_actions, previous_degree = _previous_operation(context)
    if not actions and degree is None and word_set & _REPEAT and previous_degree is not None:
        degree = previous_degree
    ignore = frozenset(
        operation_words | _FILLER_WORDS | frozenset(DEGREE_WORDS) | _REFERENTIAL | _OTHER
        | _DEICTIC_PLACE | _ALSO | _REPEAT | _GLUE
    )
    descriptions, residue = describe_with_residue(
        tokens, entities, lexicon=build_place_lexicon(entities), ignore=ignore,
        names=_name_index(entities),
    )
    if residue:
        return None
    referential = bool(word_set & (_REFERENTIAL | _OTHER | _DEICTIC_PLACE | _ALSO | _REPEAT))
    has_operation = bool(actions or degree or percent is not None or temperature is not None)
    if descriptions and not (word_set & (_ALSO | _DEICTIC_PLACE) or (words[0] == "und" and not has_operation)) and not (
        words[0] == "und" and not has_operation
    ):
        # A complete new target without discourse cue is self-contained.
        return None
    if not descriptions and not referential and has_operation and len(words) > 3:
        return None
    lexicon = build_place_lexicon(entities)
    spoken_places = [mention.place for mention in lexicon.scan(words)]
    if (
        not has_operation and not descriptions and words[0] == "und"
        and len(spoken_places) == 1 and context.last_entities
        and (previous_actions or previous_degree)
    ):
        # "Und im Bad(?)" after an action: the same operation, same kind of
        # device, at the new place.
        kinds = sorted(entity_genera(context.last_entities[0]) - {"device"})
        if not kinds:
            return None
        descriptions = (TargetDescription(
            genera=tuple(kinds), head=kinds[0], place=spoken_places[0],
            quantity=Quantity.ALL if len(context.last_entities) > 1 else Quantity.ONE,
            mass=len(context.last_entities) > 1,
        ),)
        actions, degree = previous_actions, previous_degree
        has_operation = True
    if not has_operation:
        if not (word_set & _ALSO or words[0] == "und") or not (descriptions or word_set & _OTHER):
            return None
        actions, degree = previous_actions, previous_degree
        if not actions and degree is None:
            return None
    place = _context_place(context, entities)
    if descriptions:
        if word_set & _DEICTIC_PLACE and place is not None:
            descriptions = tuple(
                item if item.place is not None or item.explicit else _with_place(item, place)
                for item in descriptions
            )
    elif word_set & _OTHER:
        others = _others(context, entities)
        if not others:
            return OntologyCommand(message="Ich weiß nicht, welches andere Gerät du meinst.")
        descriptions = (TargetDescription(
            explicit=others, quantity=Quantity.ALL if len(others) == 1 else Quantity.ONE,
        ),)
    elif context.last_entities and not (word_set & _DEICTIC_PLACE and degree is not None):
        by_id = {entity.entity_id: entity for entity in entities}
        referents = tuple(
            by_id.get(entity.entity_id, entity) for entity in context.last_entities
        )
        descriptions = (TargetDescription(explicit=referents, quantity=Quantity.ALL),)
    elif degree is not None and place is not None and degree[0] in PROPERTY_GENUS:
        descriptions = (TargetDescription(
            genera=(PROPERTY_GENUS[degree[0]],), head=PROPERTY_GENUS[degree[0]], place=place,
        ),)
    else:
        return None
    clause = ClauseMeaning(
        text=text, actions=actions, degree=degree, percent=percent,
        temperature=temperature, descriptions=tuple(descriptions),
    )
    return _compile_clauses((clause,), document, entities, source_area)


def _with_place(description: TargetDescription, place: Place) -> TargetDescription:
    from dataclasses import replace

    if place.kind is PlaceKind.HERE:
        return description
    return replace(description, place=place)
