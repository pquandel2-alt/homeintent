"""Compositional target resolution: genus × place × feature × quantity.

A spoken target is not a name lookup.  "das Wohnzimmerlicht", "die
Büro-Rolllade", "alle Fenster oben", "den Ventilator", "irgendein Fenster
im Obergeschoss" and "die Glotze" are descriptions made of independent
meaning components:

* **Name** – an exact registry name or alias always wins ("Terrassentür"
  is the device of that name, not "a door on the terrace").
* **Genus** – the kind of device (``device_ontology``), also as compound
  head ("Wohnzimmer|licht", "Decken|ventilator").
* **Place** – area, area alias, floor, floor alias, level words, whole
  house, the speaker's room (``place_model``); also as compound modifier.
* **Feature** – capability adjectives ("dimmbar", "farbig") and
  distinguishing name words ("links", "Decken").
* **Quantity** – singular (exactly one, otherwise ask), plural / "alle" /
  "die X" (all matches), "beide", a number, "irgendein" (any, for triggers).

``describe_targets`` reads these components from the tokens of one
``LanguageDocument`` clause; ``resolve_description`` combines them with the
live registry.  The result is RESOLVED, AMBIGUOUS (ask with numbers) or
NONE with an honest sentence naming genus and place ("Im Büro gibt es
keinen Ventilator.").  Nothing here executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Iterable, Mapping, Sequence

from .capabilities import describe_abilities
from ..entities import EntitySnapshot, ResolutionResult, normalize_for_compare
from ..name_similarity import edit_distance
from .device_ontology import (
    GENERA,
    analyse_word,
    entity_genera,
    genus,
    lookup_genus_word,
    negative_phrase,
)
from .normalize import german_number
from .place_model import LEVEL_WORDS, Place, PlaceKind, PlaceLexicon, PlaceMention, build_place_lexicon

__all__ = (
    "Quantity",
    "ResolutionOutcome",
    "TargetDescription",
    "TargetResolution",
    "describe_targets",
    "resolve_description",
    "resolve_text_target",
)


class Quantity(Enum):
    ONE = auto()        # singular, definite or bare: exactly one or ask
    ALL = auto()        # plural, "alle", "die X", "sämtliche"
    ANY = auto()        # "irgendein", "ein ... (Auslöser)"
    BOTH = auto()       # "beide"
    COUNT = auto()      # "drei Lampen"


class ResolutionOutcome(Enum):
    RESOLVED = auto()
    AMBIGUOUS = auto()
    NONE = auto()


_ALL_WORDS = frozenset({
    "alle", "allen", "aller", "saemtliche", "saemtlichen", "jede", "jeden",
    "jedes", "jeder", "saemtlicher", "ganzen",
})
_BOTH_WORDS = frozenset({"beide", "beiden", "beider"})
_ANY_WORDS = frozenset({
    "irgendein", "irgendeine", "irgendeinen", "irgendeinem", "irgendeiner",
    "irgendwelche", "irgendwelchen", "einer", "eines",
})
_INDEFINITE = frozenset({"ein", "eine", "einen", "einem"})
_PLURAL_ARTICLES = frozenset({"die"})
_SINGULAR_ARTICLES = frozenset({"der", "den", "dem", "das", "des"})
_ARTICLE_LIKE = (
    _ALL_WORDS | _BOTH_WORDS | _ANY_WORDS | _INDEFINITE | _PLURAL_ARTICLES
    | _SINGULAR_ARTICLES | frozenset({"mein", "meine", "meinen", "unser", "unsere", "unseren", "dein", "deine"})
)
# Capability adjectives (stem -> capability).  Stems are matched against the
# adjective without its inflection ending.
_FEATURE_STEMS: Mapping[str, str] = {
    "dimmbar": "BRIGHTNESS",
    "farbig": "COLOR",
    "bunt": "COLOR",
    "farb": "COLOR",
    "rgb": "COLOR",
    "positionierbar": "POSITION",
}
_ADJECTIVE_ENDINGS = ("eren", "erem", "eres", "en", "em", "er", "es", "e")
# Words that never carry target meaning (verbs/particles are filtered by
# the caller through lexical semantics; this is only the determiner-ish
# rest that can precede a head noun).
_TRANSPARENT = frozenset({
    "bitte", "mal", "doch", "noch", "auch", "nur", "so", "gleich", "sofort",
    "jetzt", "ganz", "etwas", "bisschen",
})
_SIDE_WORDS = {
    "linke": "links", "linken": "links", "linker": "links", "linkes": "links", "links": "links",
    "rechte": "rechts", "rechten": "rechts", "rechter": "rechts", "rechtes": "rechts", "rechts": "rechts",
    "obere": "oben", "oberen": "oben", "untere": "unten", "unteren": "unten",
    "vordere": "vorne", "vorderen": "vorne", "hintere": "hinten", "hinteren": "hinten",
    "grosse": "gross", "grossen": "gross", "kleine": "klein", "kleinen": "klein",
}


@dataclass(frozen=True)
class TargetDescription:
    """One described target, independent of the registry."""

    genera: tuple[str, ...] = ()
    head: str = ""
    explicit: tuple[EntitySnapshot, ...] = ()
    explicit_name: str = ""
    place: Place | None = None
    name_filters: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()
    quantity: Quantity = Quantity.ONE
    count: int | None = None
    universal: bool = False
    mass: bool = False
    token_start: int = 0
    token_end: int = 0

    @property
    def genus_key(self) -> str | None:
        return self.genera[0] if self.genera else None

    @property
    def is_plural(self) -> bool:
        return self.quantity in {Quantity.ALL, Quantity.BOTH, Quantity.COUNT}


@dataclass(frozen=True)
class TargetResolution:
    outcome: ResolutionOutcome
    description: TargetDescription
    entities: tuple[EntitySnapshot, ...] = ()
    message: str | None = None
    used_source_area: bool = False

    @property
    def resolved(self) -> bool:
        return self.outcome is ResolutionOutcome.RESOLVED


def _stem(word: str) -> str:
    for ending in _ADJECTIVE_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _feature(word: str) -> str | None:
    stem = _stem(word)
    for key, capability in _FEATURE_STEMS.items():
        if stem.startswith(key):
            return capability
    return None


@dataclass(frozen=True)
class _NameIndex:
    phrases: Mapping[str, tuple[EntitySnapshot, ...]]
    max_words: int


_NAME_INDEX_CACHE: dict[int, tuple[tuple[object, ...], "_NameIndex"]] = {}


def _name_index(entities: Sequence[EntitySnapshot]) -> _NameIndex:
    """Registry name/alias phrase index, cached per exact name content."""
    signature = tuple((entity.entity_id, entity.friendly_name, entity.aliases) for entity in entities)
    key = hash(signature)
    cached = _NAME_INDEX_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    index = _build_name_index(entities)
    if len(_NAME_INDEX_CACHE) > 8:
        _NAME_INDEX_CACHE.clear()
    _NAME_INDEX_CACHE[key] = (signature, index)
    return index


def _build_name_index(entities: Sequence[EntitySnapshot]) -> _NameIndex:
    phrases: dict[str, list[EntitySnapshot]] = {}
    for entity in entities:
        for name in (entity.friendly_name, *entity.aliases):
            key = " ".join(normalize_for_compare(name).replace("-", " ").split())
            if not key:
                continue
            bucket = phrases.setdefault(key, [])
            if entity not in bucket:
                bucket.append(entity)
    return _NameIndex(
        {key: tuple(value) for key, value in phrases.items()},
        max((len(key.split()) for key in phrases), default=1),
    )


def _words_of(tokens: Sequence[object]) -> tuple[list[str], list[int]]:
    words: list[str] = []
    positions: list[int] = []
    for index, token in enumerate(tokens):
        if getattr(token, "is_word", False) or getattr(token, "is_number", False):
            words.append(str(getattr(token, "canonical")))
            positions.append(index)
    return words, positions


# Function words that carry no target meaning of their own.  A command
# word outside this list, the lexicon, the ontology and the registry is
# *unexplained* and blocks execution ("Mach das Küchenlicht flauschig aus").
FUNCTION_WORDS = frozenset(normalize_for_compare(word) for word in (
    "der die das den dem des ein eine einen einem einer eines "
    "im in am an auf aus bei beim mit nach von vom zu zum zur fuer für ueber über unter vor hinter neben "
    "und oder sowie aber auch noch nur so dann danach jetzt sofort gleich mal doch bitte "
    "es ihn ihm sie ihr er wir uns mir mich dir dich du ich man "
    "kannst koenntest könntest wuerdest würdest wuerde würde moechte möchte will willst wollen "
    "hätte haette gern gerne sei sein soll sollst sollen muss musst darf "
    "mach mache machen schalte schalt schalten stell stelle stellen fahr fahre fahren "
    "dreh drehe drehen setz setze setzen lass lasse "
    "etwas bisschen wenig ganz komplett vollständig vollstaendig wieder einfach kurz schnell "
    "prozent grad stufe position mein meine meinen unser unsere unseren dein deine "
    "da dort hier drin mal eben halt ja okay ok nun also zwar gut na "
    "vielleicht eventuell irgendwann bald"
).split())


_VALUE_CUES_BEFORE = frozenset({"auf", "stufe", "um", "bis", "von", "in", "nach", "fuer", "ab"})
_VALUE_CUES_AFTER = frozenset({
    "prozent", "grad", "uhr", "minuten", "minute", "stunden", "stunde",
    "sekunden", "sekunde", "watt", "kelvin",
})


def _is_number(word: str) -> bool:
    return word.replace(",", "").replace(".", "").isdigit()


def _number_explained(words: Sequence[str], index: int) -> bool:
    """A number is a value only next to a value cue, never a name part."""
    before = words[index - 1] if index > 0 else ""
    after = words[index + 1] if index + 1 < len(words) else ""
    return before in _VALUE_CUES_BEFORE or after in _VALUE_CUES_AFTER


def describe_targets(
    tokens: Sequence[object],
    entities: Sequence[EntitySnapshot],
    *,
    lexicon: PlaceLexicon | None = None,
    ignore: frozenset[str] = frozenset(),
    names: _NameIndex | None = None,
) -> tuple[TargetDescription, ...]:
    """Read every target description of one token span.

    ``ignore`` holds normalized words the caller already explained (verbs,
    particles, values); they never become name filters.
    """
    return describe_with_residue(
        tokens, entities, lexicon=lexicon, ignore=ignore, names=names
    )[0]


def describe_with_residue(
    tokens: Sequence[object],
    entities: Sequence[EntitySnapshot],
    *,
    lexicon: PlaceLexicon | None = None,
    ignore: frozenset[str] = frozenset(),
    names: _NameIndex | None = None,
) -> tuple[tuple[TargetDescription, ...], tuple[str, ...]]:
    """Descriptions plus every word nothing explained (the residue)."""
    lexicon = lexicon or build_place_lexicon(entities)
    names = names or _name_index(entities)
    words, positions = _words_of(tokens)
    taken = [False] * len(words)

    # 1. Exact registry names and aliases, longest first.
    explicit: list[tuple[int, int, tuple[EntitySnapshot, ...], str]] = []
    for size in range(min(names.max_words, len(words)), 0, -1):
        for start in range(0, len(words) - size + 1):
            if any(taken[start:start + size]):
                continue
            phrase = " ".join(words[start:start + size])
            matches = names.phrases.get(phrase)
            if not matches:
                continue
            # A single genus word that is also somebody's name ("Rollladen")
            # is still a name; a single level word is not ("oben").
            if size == 1 and phrase in lexicon.phrases and lexicon.phrases[phrase].kind is not PlaceKind.AREA:
                continue
            explicit.append((start, start + size, matches, phrase))
            for index in range(start, start + size):
                taken[index] = True

    # 2. Places; "in Küche und Flur" coordinates two places into one.
    free_words = [word if not taken[index] else "\x00" for index, word in enumerate(words)]
    mentions = _coordinate(words, [
        mention for mention in lexicon.scan(free_words)
        if not any(taken[mention.token_start:mention.token_end])
    ])
    for mention in mentions:
        for index in range(mention.token_start, mention.token_end):
            taken[index] = True

    # 3. Genus heads (direct or compound).
    heads: list[tuple[int, object]] = []
    for index, word in enumerate(words):
        if taken[index] or word in ignore:
            continue
        analysis = analyse_word(word)
        if analysis is None:
            continue
        heads.append((index, analysis))
        taken[index] = True

    anchors = sorted(
        [(start, end, "explicit", (matches, phrase)) for start, end, matches, phrase in explicit]
        + [(index, index + 1, "genus", analysis) for index, analysis in heads]
    )
    descriptions: list[TargetDescription] = []
    previous_end = 0
    for position, (start, end, kind, payload) in enumerate(anchors):
        # Determiners/adjectives between the previous anchor and this head.
        modifier_indices = [
            index for index in range(previous_end, start)
            if not taken[index] and words[index] not in ignore
        ]
        modifiers = [words[index] for index in modifier_indices]
        previous_end = end
        quantity = Quantity.ONE
        count: int | None = None
        features: set[str] = set()
        filters: list[str] = []
        article_plural = False
        for index, word in zip(modifier_indices, modifiers):
            recognised = True
            if word in _ALL_WORDS:
                quantity = Quantity.ALL
            elif word in _BOTH_WORDS:
                quantity = Quantity.BOTH
            elif word in _ANY_WORDS:
                quantity = Quantity.ANY
            elif word in _PLURAL_ARTICLES:
                article_plural = True
            elif (
                (number := (int(word) if word.isdigit() else german_number(word))) is not None
                and 2 <= number <= 20
                and not (index + 1 < len(words) and words[index + 1] in _NUMBER_UNITS)
            ):
                # "8 Uhr", "20 Minuten", "3 Grad" measure something else.
                quantity, count = Quantity.COUNT, number
            elif (capability := _feature(word)) is not None:
                features.add(capability)
            elif word in _SIDE_WORDS:
                filters.append(_SIDE_WORDS[word])
            elif word in _ARTICLE_LIKE:
                pass
            else:
                recognised = False
            if recognised:
                taken[index] = True
        if kind == "explicit":
            matches, phrase = payload  # type: ignore[misc]
            as_genus = analyse_word(phrase) if " " not in phrase else None
            if as_genus is not None and (
                quantity in {Quantity.BOTH, Quantity.COUNT, Quantity.ALL} or article_plural
            ):
                # "beide Rollladen": a quantity makes the kind word a kind,
                # even when one device happens to be named "Rollladen".
                kind, payload = "genus", as_genus
            else:
                descriptions.append(TargetDescription(
                    explicit=tuple(matches),
                    explicit_name=phrase,
                    quantity=Quantity.ALL if quantity is Quantity.ALL else Quantity.ONE,
                    token_start=positions[start],
                    token_end=positions[end - 1] + 1,
                ))
                continue
        analysis = payload
        genera = tuple(getattr(analysis, "genera"))
        plural = bool(getattr(analysis, "plural"))
        universal = bool(getattr(analysis, "universal")) or genera == ("device",)
        place: Place | None = None
        modifier = getattr(analysis, "modifier")
        if modifier:
            place = lexicon.resolve_modifier(modifier)
            if place is None:
                filters.append(modifier)
        if getattr(analysis, "indefinite"):
            quantity = Quantity.ANY
        elif quantity is Quantity.ONE and (plural or universal or (article_plural and plural)):
            quantity = Quantity.ALL
        # Trailing side words ("Rollladen links") refine the same head.
        after = end
        while after < len(words) and not taken[after] and words[after] in _SIDE_WORDS:
            filters.append(_SIDE_WORDS[words[after]])
            taken[after] = True
            after += 1
        descriptions.append(TargetDescription(
            genera=genera,
            head=str(getattr(analysis, "word")),
            mass=bool(getattr(analysis, "mass")),
            place=place,
            name_filters=tuple(filters),
            features=frozenset(features),
            quantity=quantity,
            count=count,
            universal=universal,
            token_start=positions[start],
            token_end=positions[end - 1] + 1,
        ))
        del position

    # 4. Attach spoken places: each place belongs to the nearest head;
    # a single place applies to every description without its own place.
    if mentions:
        for mention in mentions:
            mention_token = positions[mention.token_start]
            open_ones = [
                index for index, item in enumerate(descriptions)
                if item.place is None
            ]
            if not open_ones:
                continue
            if len(mentions) == 1:
                for index in open_ones:
                    descriptions[index] = replace(descriptions[index], place=mention.place)
                continue
            nearest = min(
                open_ones,
                key=lambda index: min(
                    abs(descriptions[index].token_start - mention_token),
                    abs(descriptions[index].token_end - mention_token),
                ),
            )
            descriptions[nearest] = replace(descriptions[nearest], place=mention.place)
    residue = tuple(
        word for index, word in enumerate(words)
        if not taken[index]
        and (word not in ignore or (word in LEVEL_WORDS and word not in lexicon.phrases))
        and (word not in ignore or word in LEVEL_WORDS)
        and word not in FUNCTION_WORDS
        and word not in _ARTICLE_LIKE
        and word not in _TRANSPARENT
        and not (_is_number(word) and _number_explained(words, index))
        and not (german_number(word) is not None and _number_explained(words, index))
    )
    return tuple(descriptions), residue


_COORDINATORS = frozenset({"und", "sowie", "plus"})
_PLACE_GLUE = frozenset({"im", "in", "der", "dem", "den", "die", "das", "am", "an"})


def _coordinate(words: Sequence[str], mentions: list[PlaceMention]) -> list[PlaceMention]:
    """Merge place mentions joined only by "und"/"," into one union place."""
    merged: list[PlaceMention] = []
    for mention in mentions:
        if merged:
            previous = merged[-1]
            between = words[previous.token_end:mention.token_start]
            if (
                between
                and any(word in _COORDINATORS for word in between)
                and all(word in _COORDINATORS or word in _PLACE_GLUE for word in between)
                and previous.place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}
                and mention.place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}
            ):
                union = Place(
                    PlaceKind.AREA,
                    f"{previous.place.name} und {mention.place.name}",
                    previous.place.area_ids | mention.place.area_ids,
                    frozenset(),
                )
                merged[-1] = PlaceMention(
                    union, previous.token_start, mention.token_end,
                    previous.explicit_preposition,
                )
                continue
        merged.append(mention)
    return merged


def spoken_places(
    tokens: Sequence[object],
    entities: Sequence[EntitySnapshot],
    lexicon: PlaceLexicon | None = None,
) -> tuple[Place, ...]:
    """All places mentioned in the token span (without target heads)."""
    lexicon = lexicon or build_place_lexicon(entities)
    words, _ = _words_of(tokens)
    return tuple(mention.place for mention in lexicon.scan(words))


def _name_matches(entity: EntitySnapshot, filters: Sequence[str]) -> bool:
    haystack = " ".join(
        normalize_for_compare(name).replace("-", " ")
        for name in (entity.friendly_name, *entity.aliases)
    )
    words = haystack.split()
    for item in filters:
        stems = {item, _stem(item)}
        if item.endswith("n"):
            stems.add(item[:-1])
        if not any(
            word.startswith(stem) or stem in word
            for word in words
            for stem in stems
            if len(stem) >= 3
        ):
            return False
    return True


def genus_members(
    key: str, entities: Iterable[EntitySnapshot], *, universal: bool = False
) -> list[EntitySnapshot]:
    """Entities of one genus; the universal genus excludes critical kinds."""
    if universal or key == "device":
        everyday = {item.key for item in GENERA if item.in_everything}
        return [
            entity for entity in entities
            if entity_genera(entity) & everyday
        ]
    return [entity for entity in entities if key in entity_genera(entity)]


def _place_phrase(place: Place | None) -> str:
    if place is None:
        return "Im Haus"
    label = place.label
    return label[:1].upper() + label[1:]


def resolve_description(
    description: TargetDescription,
    entities: Sequence[EntitySnapshot],
    *,
    source_area: Place | None = None,
    required_capability: str | None = None,
    domains: frozenset[str] | None = None,
) -> TargetResolution:
    """Combine one description with the registry into an outcome."""
    if description.explicit and description.place is not None and not any(
        description.place.contains(entity) for entity in description.explicit
    ) and (as_genus := analyse_word(description.explicit_name)) is not None:
        # A kind word that is also somebody's name, spoken with a place the
        # named device is not at ("den Rollladen im Poleraum"): the kind
        # reading at that place is meant.
        description = replace(
            description, explicit=(), explicit_name="", genera=tuple(as_genus.genera),
            head=as_genus.word, mass=as_genus.mass,
        )
    if description.explicit:
        matches = tuple(description.explicit)
        if description.place is not None and len(matches) > 1:
            narrowed = tuple(entity for entity in matches if description.place.contains(entity))
            matches = narrowed or matches
        if domains is not None:
            matches = tuple(entity for entity in matches if entity.domain in domains) or matches
        if len(matches) == 1 or description.quantity is Quantity.ALL:
            return TargetResolution(ResolutionOutcome.RESOLVED, description, matches)
        return TargetResolution(ResolutionOutcome.AMBIGUOUS, description, matches)

    key = description.genus_key
    if key is None:
        return TargetResolution(ResolutionOutcome.NONE, description)
    candidates = genus_members(key, entities, universal=description.universal)
    for extra in description.genera[1:]:
        candidates.extend(
            entity for entity in genus_members(extra, entities)
            if entity not in candidates
        )
    unfit: list[EntitySnapshot] = []
    if domains is not None:
        unfit = [entity for entity in candidates if entity.domain not in domains]
        candidates = [entity for entity in candidates if entity.domain in domains]
    place = description.place
    used_source_area = False
    if place is not None and place.kind is PlaceKind.HERE:
        place = source_area
    if place is not None:
        candidates = [entity for entity in candidates if place.contains(entity)]
    if description.name_filters:
        candidates = [
            entity for entity in candidates
            if _name_matches(entity, description.name_filters)
        ]
    if description.features:
        candidates = [
            entity for entity in candidates
            if description.features <= entity.capabilities
        ]
    if required_capability is not None:
        capable = [entity for entity in candidates if required_capability in entity.capabilities]
        if capable:
            candidates = capable
    if (
        place is None
        and source_area is not None
        and description.quantity in {Quantity.ONE}
        and len(candidates) > 1
    ):
        local = [entity for entity in candidates if source_area.contains(entity)]
        if local:
            candidates = local
            place = source_area
            used_source_area = True
    candidates.sort(key=lambda entity: entity.entity_id)
    described = replace(description, place=place)
    if not candidates:
        present = [entity for entity in unfit if place is None or place.contains(entity)]
        if present:
            # "Mach die Tür zu" with only door contacts: the kind exists,
            # but nothing of it can carry the operation.
            names = sorted(entity.friendly_name for entity in present)
            listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
            sensors = all(entity.domain in {"binary_sensor", "sensor"} for entity in present)
            if sensors:
                sentence = f"{listed} kenne ich nur als Kontakt oder Sensor; schalten kann ich das nicht."
            elif len(present) == 1:
                # The actual reason from the device's capabilities, never a
                # blanket "unterstützt die Aktion nicht" (7.3.3).
                sentence = describe_abilities(present[0].friendly_name, present[0].capabilities)
            else:
                sentence = " ".join(
                    describe_abilities(entity.friendly_name, entity.capabilities)
                    for entity in sorted(present, key=lambda item: item.friendly_name)[:3]
                )
            return TargetResolution(
                ResolutionOutcome.NONE, described,
                message=f"{sentence} Ich habe nichts ausgeführt.",
            )
        return TargetResolution(
            ResolutionOutcome.NONE,
            described,
            message=_none_message(described),
        )
    quantity = description.quantity
    if (
        quantity is Quantity.ONE
        and description.mass
        and place is not None
        and not description.name_filters
        and not description.features
    ):
        # "das Licht im Wohnzimmer" / "das Wohnzimmerlicht": a mass noun at
        # a named place denotes all of it (documented rule F20).
        quantity = Quantity.ALL
    if quantity in {Quantity.ALL, Quantity.ANY}:
        return TargetResolution(
            ResolutionOutcome.RESOLVED, described, tuple(candidates),
            used_source_area=used_source_area,
        )
    if quantity is Quantity.BOTH or quantity is Quantity.COUNT:
        wanted = 2 if quantity is Quantity.BOTH else (description.count or 0)
        if len(candidates) == wanted:
            return TargetResolution(ResolutionOutcome.RESOLVED, described, tuple(candidates))
        if len(candidates) < wanted:
            return TargetResolution(
                ResolutionOutcome.NONE, described, tuple(candidates),
                message=_too_few_message(described, len(candidates)),
            )
        return TargetResolution(ResolutionOutcome.AMBIGUOUS, described, tuple(candidates))
    if len(candidates) == 1:
        return TargetResolution(
            ResolutionOutcome.RESOLVED, described, tuple(candidates),
            used_source_area=used_source_area,
        )
    return TargetResolution(
        ResolutionOutcome.AMBIGUOUS, described, tuple(candidates),
        used_source_area=used_source_area,
    )


_NUMBER_UNITS = frozenset({
    "uhr", "prozent", "grad", "minute", "minuten", "stunde", "stunden", "sekunde",
    "sekunden", "tag", "tage", "tagen", "watt", "stufe", "mal",
})
_COUNT_WORDS = {1: "ein", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs"}


def _none_message(description: TargetDescription) -> str:
    key = description.genus_key or "device"
    noun = negative_phrase(key, plural=description.is_plural or description.universal)
    if description.universal:
        noun = "keine passenden Geräte"
    qualifier = ""
    if description.name_filters:
        qualifier = f" ({', '.join(description.name_filters)})"
    return f"{_place_phrase(description.place)} gibt es {noun}{qualifier}."


def _too_few_message(description: TargetDescription, found: int) -> str:
    key = description.genus_key or "device"
    item = genus(key)
    noun = item.plural if found != 1 else item.singular
    number = _COUNT_WORDS.get(found, str(found))
    if found == 1:
        number = {"m": "einen", "f": "eine", "n": "ein"}[item.gender.value]
    return f"{_place_phrase(description.place)} gibt es nur {number} {noun}."


def resolve_text_target(
    tokens: Sequence[object],
    entities: Sequence[EntitySnapshot],
    *,
    source_area: Place | None = None,
    ignore: frozenset[str] = frozenset(),
) -> tuple[TargetResolution, ...]:
    """Describe and resolve every target of a token span."""
    lexicon = build_place_lexicon(entities)
    return tuple(
        resolve_description(item, entities, source_area=source_area)
        for item in describe_targets(tokens, entities, lexicon=lexicon, ignore=ignore)
    )


def closest_genus_word(word: str, *, max_distance: int = 2) -> tuple[str, ...]:
    """Genus-internal spelling repair: only forms of the *same* genus.

    Returns the genus keys whose own surface forms are within
    ``max_distance`` edits of ``word``.  A correction therefore never jumps
    between kinds of devices (never "Wassermelder" -> "Bewegungsmelder").
    """
    from .device_ontology import _form_index  # local: private data index

    normalized = normalize_for_compare(word)
    scored = sorted(
        (edit_distance(normalized, form), keys)
        for form, keys in _form_index().items()
        if abs(len(form) - len(normalized)) <= max_distance and len(form) >= 5
    )
    if not scored or scored[0][0] > max_distance:
        return ()
    best = scored[0][0]
    found: list[str] = []
    for distance, keys in scored:
        if distance != best:
            break
        for key in keys:
            if key not in found:
                found.append(key)
    return tuple(found)


@dataclass(frozen=True)
class DescribedTarget:
    """Convenience bundle for callers that also want the place lexicon."""

    resolutions: tuple[TargetResolution, ...]
    lexicon: PlaceLexicon = field(repr=False, default=None)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# The one target resolution for name phrases (7.4.0).
#
# Every "which device is meant by this phrase" question goes through
# ``resolve_phrase``: the scored name tier (exact names/aliases, contains,
# bounded fuzzy correction - moved here from the historic resolver) plus the
# rules of this module:
#   * only the entities passed in (the exposed ones) are candidates;
#   * a correction never crosses class boundaries: when the phrase names a
#     device kind ("die Leuchte im Bad"), contains/fuzzy candidates of other
#     domains are dropped; exact registry names always stay authoritative;
#   * ambiguity stays ambiguity (``AMBIGUOUS``) - a spoken place narrows it,
#     nothing else does; the caller asks a numbered question
#     (``numbered_question``);
#   * learned bindings (aliases, default choices) are applied here and only
#     here (``BINDING_HOOKS``, filled from 7.4.1 on).


# Words that name what the *command* creates or runs ("eine Automation für
# die Haustür"), not the kind of device meant - never a class boundary.
_META_DOMAINS = frozenset({"automation", "script", "scene"})


def _phrase_genera(words: Sequence[str]) -> frozenset[str]:
    """Device genus keys the phrase names (meta words excluded)."""
    keys: set[str] = set()
    for word in words:
        for key in lookup_genus_word(word):
            kinds = set(genus(key).domains)
            if kinds and not kinds <= _META_DOMAINS:
                keys.add(key)
    return frozenset(keys)


def _phrase_domains(words: Sequence[str]) -> frozenset[str] | None:
    """Device domains the phrase's genus words allow (``None``: no genus)."""
    domains: set[str] = set()
    for key in _phrase_genera(words):
        domains |= set(genus(key).domains)
    return frozenset(domains) if domains else None


def _within_kind(entity: EntitySnapshot, kinds: frozenset[str], keys: frozenset[str]) -> bool:
    """The entity is of the named kind by domain or by its own name/class
    (a switch named "Licht Sportraum" is a light)."""
    if entity.domain in kinds or keys & entity_genera(entity):
        return True
    for name in (entity.friendly_name, *entity.aliases):
        for word in normalize_for_compare(name).replace("-", " ").split():
            analysis = analyse_word(word)
            if analysis is not None and keys & set(analysis.genera):
                return True
    return False


def resolve_phrase(
    name: str,
    entities: Sequence[EntitySnapshot],
    *,
    area_id: str | None = None,
    domain: str | None = None,
    device_class: str | None = None,
    index: object | None = None,
) -> ResolutionResult:
    """Resolve a spoken name phrase to exposed entities (single entry point)."""
    from ..entities import (
        EntityMatchSource,
        ResolutionStatus,
        assemble_name_resolution,
        rank_name_candidates,
    )

    spoken = (name or "").strip()
    entity_list = list(entities)
    ranked = rank_name_candidates(
        spoken, entity_list, area_id=area_id, domain=domain,
        device_class=device_class, index=index,  # type: ignore[arg-type]
    )
    words = normalize_for_compare(spoken).replace("-", " ").split()
    keys = _phrase_genera(words)
    kinds = _phrase_domains(words)
    if kinds is not None and ranked:
        loose = {EntityMatchSource.CONTAINS, EntityMatchSource.FUZZY}
        ranked = [
            item for item in ranked
            if item.source not in loose or _within_kind(item.entity, kinds, keys)
        ]
    result = assemble_name_resolution(spoken, ranked)
    if result.status is ResolutionStatus.AMBIGUOUS and len(words) > 1:
        mentions = build_place_lexicon(entity_list).scan(words)
        places = [mention.place for mention in mentions if mention.place.kind is not PlaceKind.HERE]
        if places:
            narrowed = [item for item in ranked if places[0].contains(item.entity)]
            if narrowed and len(narrowed) < len(ranked):
                result = assemble_name_resolution(spoken, narrowed)
    return result


def numbered_question(candidates: Sequence[EntitySnapshot], *, noun: str = "Gerät") -> str:
    """"Welches Gerät meinst du: 1. …, 2. … oder 3. …?"."""
    items = [f"{index}. {entity.friendly_name}" for index, entity in enumerate(candidates, 1)]
    listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " oder " + items[-1]
    article = "Welche" if noun in {"Routine", "Automation", "Szene"} else "Welches"
    return f"{article} {noun} meinst du: {listed}?"


# ---------------------------------------------------------------------------
# Shadow comparison of the historic resolver and ``resolve_phrase`` (7.4.0).

RESOLUTION_DRIFTS = ("EQUIVALENT", "REFINEMENT", "OLD_BETTER", "SAFETY_DRIFT")


def _resolution_ids(result: ResolutionResult) -> frozenset[str]:
    ids = {entity.entity_id for entity in result.candidates}
    if result.entity is not None:
        ids.add(result.entity.entity_id)
    return frozenset(ids)


def compare_resolutions(
    old: ResolutionResult, new: ResolutionResult, name: str = ""
) -> tuple[str, str]:
    """Drift class and reason between the historic and the new resolution.

    * new ids outside the old ids, or a dropped confirmation for a fuzzy
      correction: ``SAFETY_DRIFT``;
    * the old one resolved, the new one does not: ``OLD_BETTER`` - unless the
      old entity lies outside the device kind the phrase names (the old
      resolver crossed a class boundary: ``REFINEMENT``/``class_boundary``);
    * the new one names a subset (fewer candidates, place narrowing,
      class boundary): ``REFINEMENT``.
    """
    from ..entities import ResolutionStatus

    old_ids, new_ids = _resolution_ids(old), _resolution_ids(new)
    if old.status is new.status and old_ids == new_ids:
        return "EQUIVALENT", ""
    if not new_ids <= old_ids:
        return "SAFETY_DRIFT", "new_targets"
    if old.status is ResolutionStatus.CONFIRMATION_REQUIRED and new.status is ResolutionStatus.RESOLVED:
        return "SAFETY_DRIFT", "confirmation_dropped"
    if old.status is ResolutionStatus.RESOLVED:
        words = normalize_for_compare(name).replace("-", " ").split()
        kinds = _phrase_domains(words)
        if (
            kinds is not None and old.entity is not None
            and not _within_kind(old.entity, kinds, _phrase_genera(words))
        ):
            return "REFINEMENT", "class_boundary"
        return "OLD_BETTER", f"resolved_to_{new.status.name.lower()}"
    return "REFINEMENT", f"{old.status.name.lower()}_to_{new.status.name.lower()}"


# ---------------------------------------------------------------------------
# Learned bindings (7.4.1) - applied here and only here.


def apply_alias_bindings(
    entities: Sequence[EntitySnapshot], bindings: Iterable[object]
) -> list[EntitySnapshot]:
    """Add confirmed household/personal aliases to the exposed snapshots.

    An alias is a lexicon entry of the one target resolution: once attached
    to the snapshot it works in every sentence form, question, time command,
    push sentence and multi-command. A binding whose target is not among
    ``entities`` (removed or no longer exposed) is simply inert.
    """
    from ..bindings import BindingKind

    extra: dict[str, list[str]] = {}
    for binding in bindings:
        if getattr(binding, "kind", None) is not BindingKind.ALIAS:
            continue
        spoken = str(getattr(binding, "data", {}).get("spoken") or getattr(binding, "key"))
        extra.setdefault(str(getattr(binding, "target")), []).append(spoken)
    if not extra:
        return list(entities)
    return [
        replace(entity, aliases=tuple(dict.fromkeys((*entity.aliases, *extra[entity.entity_id]))))
        if entity.entity_id in extra else entity
        for entity in entities
    ]


def default_choice_for(
    candidates: Sequence[EntitySnapshot],
    area_id: str | None,
    bindings: Iterable[object],
) -> EntitySnapshot | None:
    """The confirmed default choice for exactly this question, if any.

    The key is the offered candidate set × place, so only a candidate the
    clarification itself offered can be chosen: a default choice never adds
    a target, and the resulting command still runs through validator,
    EffectGraph and policy.
    """
    from ..bindings import BindingKind, normalize_key

    ids = sorted(entity.entity_id for entity in candidates)
    keys = {
        normalize_key(f"{'|'.join(ids)} @ {area_id or 'ueberall'}"),
        normalize_key(f"{'|'.join(ids)} @ ueberall"),
    }
    by_id = {entity.entity_id: entity for entity in candidates}
    for binding in bindings:
        if (
            getattr(binding, "kind", None) is BindingKind.DEFAULT_CHOICE
            and getattr(binding, "key") in keys
            and getattr(binding, "target") in by_id
        ):
            return by_id[str(getattr(binding, "target"))]
    return None


# ---------------------------------------------------------------------------
# Devices that exist in Home Assistant but are not exposed (7.6.1).


def _name_words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def hidden_name_mentions(
    text: str,
    exposed: Sequence[EntitySnapshot],
    hidden: Iterable[tuple[str, str]],
) -> tuple[str, ...]:
    """Names of non-exposed devices spoken in ``text``.

    ``hidden`` holds ``(domain, friendly name)`` of entities Home Assistant
    knows but HomeIntent may not use. A hidden name counts only when it is
    spoken as whole words, outside every exposed name ("Kaffeemaschine
    entkalken" is the exposed button, not the hidden machine), and when no
    exposed entity carries the same name. A single genus word ("Licht")
    only counts when no exposed device of that genus exists, so it never
    shadows an ordinary genus reference. Nothing here resolves a target:
    the result only explains why nothing is done - side entities of a
    hidden device never become its substitute.
    """
    words = _name_words(text)
    if not words:
        return ()
    exposed_keys: set[tuple[str, ...]] = set()
    for entity in exposed:
        for name in (entity.friendly_name, *entity.aliases):
            key = tuple(_name_words(name))
            if key:
                exposed_keys.add(key)
    covered = [False] * len(words)
    for key in exposed_keys:
        width = len(key)
        for start in range(len(words) - width + 1):
            if tuple(words[start:start + width]) == key:
                for index in range(start, start + width):
                    covered[index] = True
    present = set(words)
    found: list[str] = []
    for _domain, name in hidden:
        key = tuple(_name_words(name))
        if not key or key[0] not in present or key in exposed_keys or name in found:
            continue
        if len(key) == 1:
            analysis = analyse_word(key[0])
            if analysis is not None and any(
                set(analysis.genera) & set(entity_genera(entity)) for entity in exposed
            ):
                continue
        width = len(key)
        if any(
            tuple(words[start:start + width]) == key
            and not any(covered[start:start + width])
            for start in range(len(words) - width + 1)
        ):
            found.append(name)
    return tuple(found)


def hidden_device_text(names: Sequence[str], *, admin_hint: str | None = None) -> str:
    """"Saugroboter ist für HomeIntent nicht freigegeben." (+ where to change it)."""
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
    verb = "ist" if len(names) == 1 else "sind"
    text = f"{listed} {verb} für HomeIntent nicht freigegeben."
    return f"{text} {admin_hint}" if admin_hint else text
