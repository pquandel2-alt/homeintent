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
from .clause_reading import FILLER_WORDS, ClauseMeaning, directional_as_degree, read_operation
from .ontology_compiler import OntologyCommand, compile_clauses
from .place_model import Place, PlaceKind, build_place_lexicon
from .semantic_catalog import DEGREE_WORDS, PROPERTY_GENUS
from .degree_semantics import percent_value, temperature_value
from .target_resolution import Quantity, TargetDescription, name_index, describe_with_residue

__all__ = ("compile_discourse",)

_REFERENTIAL = frozenset({
    "es", "ihn", "sie", "ihm", "ihnen", "das", "den", "die", "dem", "der", "alle", "allen",
    "beide", "beiden", "diese", "dieses", "diesen", "dieser", "davon", "mach's", "machs",
})
_OTHER = frozenset({"andere", "anderen", "anderer", "anderes", "zweite", "zweiten"})
_SIDES = frozenset({"links", "rechts", "vorne", "hinten", "oben", "unten", "gross", "klein"})
_DEICTIC_PLACE = frozenset({"da", "dort", "dorthin", "drin", "drueben"})
# "ebenfalls/ebenso/genauso/das Gleiche/dasselbe" take over the previous
# operation (7.8 B4).
_ALSO = frozenset({"auch", "ebenfalls", "genauso", "ebenso", "gleiche", "gleichen", "dasselbe", "selbe", "selben"})
_REPEAT = frozenset({"mehr", "weiter", "nochmal", "nochmals", "eins", "tick", "stueck", "stufe", "stufen"})
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


_VALUE_INTENTS = {"HassClimateSetTemperature": "temperature", "HassSetPercentage": "percent"}


def _previous_value(context: ConversationContext) -> tuple[str, float] | None:
    command = context.last_command
    if command is None or command.intent not in _VALUE_INTENTS:
        return None
    kind = _VALUE_INTENTS[command.intent]
    value = command.parameters.get(kind)
    return (kind, float(value)) if isinstance(value, (int, float)) else None


def _bare_number(words: Sequence[str]) -> float | None:
    from .normalize import german_number

    for word in words:
        if word.replace(",", "").isdigit():
            return float(word.replace(",", "."))
        value = german_number(word) if word not in {"ein", "eine", "einen", "eins"} else None
        if value is not None:
            return float(value)
    return None


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


_SIDE_FORMS = frozenset({
    "linke", "linken", "linker", "linkes", "rechte", "rechten", "rechter", "rechtes",
    "obere", "oberen", "oberer", "oberes", "untere", "unteren", "unterer", "unteres",
    "vordere", "vorderen", "vorderer", "vorderes", "hintere", "hinteren", "hinterer", "hinteres",
})
_SIDE_FILLERS = frozenset({"und", "den", "die", "das", "der", "dem", "auch", "ebenfalls", "bitte", "noch", "jetzt", "genauso"})


def _side_sibling(
    words: Sequence[str], entities: Sequence[EntitySnapshot], context: ConversationContext
) -> EntitySnapshot | None:
    """The partner named only by its side ("den rechten") of the one
    previous device whose name carries a side."""
    from ..conversation_correction import sibling_name
    from .target_resolution import resolve_phrase

    sides = [word for word in words if word in _SIDE_FORMS]
    if len(sides) != 1 or len(context.last_entities) != 1:
        return None
    if any(word not in _SIDE_FORMS and word not in _SIDE_FILLERS for word in words):
        return None
    name = sibling_name(sides[0], context.last_entities[0])
    if name is None:
        return None
    resolved = resolve_phrase(name, list(entities))
    return resolved.entity


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
    actions, operation_words = read_operation(normalize(text))
    if any(
        word == "auf" and index + 1 < len(words) and _bare_number(words[index + 1:index + 2]) is not None
        for index, word in enumerate(words)
    ):
        # "auf 18": a value, not the operation "open" (7.8 B4).
        actions = frozenset(actions) - {"open"}
    degrees = [DEGREE_WORDS[word] for word in words if word in DEGREE_WORDS]
    degree = degrees[0] if len(set(degrees)) == 1 else None
    temperature = temperature_value(text)
    percent = None if temperature is not None else percent_value(text)
    previous_actions, previous_degree = _previous_operation(context)
    values = [token.canonical for token in tokens if token.is_word or token.is_number]
    spoken_unit = bool(word_set & {"prozent", "grad"}) or "%" in text or "°" in text
    previous_value = _previous_value(context)
    if (
        previous_value is not None and previous_value[0] == "temperature" and not spoken_unit
        and percent is not None and temperature is None and not actions and degree is None
    ):
        # A bare number after a temperature setting is a temperature.
        percent = None
    if not actions and degree is None and percent is None and temperature is None:
        # "Und in der Küche auf 18" / "Im Kinderzimmer auch" after a setting:
        # the previous setting, with the new number if one is spoken.
        number = _bare_number(values)
        if previous_value is not None and (number is not None or word_set & _ALSO):
            kind, old = previous_value
            value = number if number is not None else old
            if kind == "temperature":
                temperature = float(value)
            else:
                percent = int(value)
    if not actions and degree is None and word_set & _REPEAT and previous_degree is not None:
        degree = previous_degree
    sibling = _side_sibling(
        [word for word in words if word not in operation_words], entities, context
    )
    if sibling is not None:
        # "… und den rechten auch" after "den linken Rollladen": the named
        # partner of the previous device, same operation (7.6.0); "Den
        # rechten runter" names its own operation (7.7.1 A3).
        if not actions and degree is None:
            actions, degree = previous_actions, previous_degree
        if not actions and degree is None:
            return None
        clause = ClauseMeaning(
            text=text, actions=frozenset(actions), degree=degree, percent=None, temperature=None,
            descriptions=(TargetDescription(explicit=(sibling,), quantity=Quantity.ALL),),
        )
        return compile_clauses((clause,), document, entities, source_area)
    ignore = frozenset(
        operation_words | FILLER_WORDS | frozenset(DEGREE_WORDS) | _REFERENTIAL | _OTHER
        | _DEICTIC_PLACE | _ALSO | _REPEAT | _GLUE
    )
    descriptions, residue = describe_with_residue(
        tokens, entities, lexicon=build_place_lexicon(entities), ignore=ignore,
        names=name_index(entities),
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
        not has_operation and not descriptions and (words[0] == "und" or word_set & _ALSO)
        and len(spoken_places) == 1 and context.last_entities
        and (previous_actions or previous_degree)
    ):
        # "Und im Bad(?)" / "Oben auch" after an action: the same operation,
        # same kind of device, at the new place. The set never widens: one
        # device before is one device now (7.7.1 A3) - the one with the same
        # role ("Flurlicht" -> "Flurlicht oben") or a question.
        kinds = sorted(entity_genera(context.last_entities[0]) - {"device"})
        if not kinds:
            return None
        descriptions = (_role_at_place(context.last_entities, kinds, spoken_places[0], entities) or TargetDescription(
            genera=tuple(kinds), head=kinds[0], place=spoken_places[0],
            quantity=Quantity.ALL if len(context.last_entities) > 1 else Quantity.ONE,
            mass=len(context.last_entities) > 1,
        ),)
        actions, degree = previous_actions, previous_degree
        has_operation = True
    if not has_operation:
        bare_other = bool(word_set & _OTHER) and not descriptions and len(words) <= 4
        if not bare_other and (
            not (word_set & _ALSO or words[0] == "und") or not (descriptions or word_set & _OTHER)
        ):
            # "Die andere." alone repeats the last operation on the partner.
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
    elif spoken_places and context.last_entities and not any(
        spoken_places[0].contains(entity) for entity in context.last_entities
    ):
        # An explicit new place outranks the remembered referent: "Mach es
        # im Kinderzimmer kühler" after the office means the kind the
        # property implies (or the remembered kind) in the children's room.
        if degree is not None and degree[0] in PROPERTY_GENUS:
            kinds = [PROPERTY_GENUS[degree[0]]]
        else:
            kinds = sorted(entity_genera(context.last_entities[0]) - {"device"})
        if not kinds:
            return None
        descriptions = (TargetDescription(
            genera=tuple(kinds), head=kinds[0], place=spoken_places[0],
            quantity=Quantity.ALL if len(context.last_entities) > 1 else Quantity.ONE,
            mass=len(context.last_entities) > 1,
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
    genera = {key for item in descriptions for key in item.genera} | {
        key for item in descriptions for entity in item.explicit
        for key in entity_genera(entity) - {"device"}
    }
    actions, degree = directional_as_degree(frozenset(actions), degree, words, genera)
    clause = ClauseMeaning(
        text=text, actions=actions, degree=degree, percent=percent,
        temperature=temperature, descriptions=tuple(descriptions),
    )
    return compile_clauses((clause,), document, entities, source_area)


def _role_at_place(
    previous: Sequence[EntitySnapshot],
    kinds: Sequence[str],
    place: Place,
    entities: Sequence[EntitySnapshot],
) -> TargetDescription | None:
    """The one device at ``place`` with the role of the previous device.

    "Flurlicht" in the hall -> "Flurlicht oben" in the upper hall: same
    kind, and its area or name repeats the previous area or name stem.
    """
    if len(previous) != 1:
        return None
    reference = previous[0]
    stems = {
        normalize_for_compare(value).replace(" ", "")
        for value in (reference.area_name or "", reference.friendly_name)
        if value
    }
    members = [
        entity for entity in entities
        if place.contains(entity) and entity_genera(entity) & set(kinds)
        and entity.entity_id != reference.entity_id
    ]
    if len(members) == 1:
        return TargetDescription(explicit=(members[0],), quantity=Quantity.ALL)
    role = [
        entity for entity in members
        if any(
            stem and stem in normalize_for_compare(value).replace(" ", "")
            for stem in stems
            for value in (entity.area_name or "", entity.friendly_name)
        )
    ]
    if len(role) != 1:
        # "Küchenlicht" is the room's own light: at the new place the device
        # named after that room with the same head ("Bürolicht").
        role = [
            entity for entity in members
            if _room_named(entity) and _room_named(reference)
            and _head_of(entity) == _head_of(reference)
        ]
    if len(role) == 1:
        return TargetDescription(explicit=(role[0],), quantity=Quantity.ALL)
    return None


def _head_of(entity: EntitySnapshot) -> str | None:
    from .device_ontology import analyse_word

    analysis = analyse_word(normalize_for_compare(entity.friendly_name).replace(" ", ""))
    return analysis.head if analysis is not None else None


def _room_named(entity: EntitySnapshot) -> bool:
    """"Küchenlicht" in the kitchen: the name is its area plus a kind."""
    from .device_ontology import analyse_word

    analysis = analyse_word(normalize_for_compare(entity.friendly_name).replace(" ", ""))
    area = normalize_for_compare(entity.area_name or "").replace(" ", "")
    if analysis is None or not analysis.modifier or not area:
        return False
    stem = analysis.modifier.rstrip("n") if len(analysis.modifier) > 4 else analysis.modifier
    return area.startswith(stem[: max(4, len(stem) - 1)])


def _with_place(description: TargetDescription, place: Place) -> TargetDescription:
    from dataclasses import replace

    if place.kind is PlaceKind.HERE:
        return description
    return replace(description, place=place)
