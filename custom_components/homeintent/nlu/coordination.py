"""Coordination in the meaning layer (7.7.1 A4).

German shares material across coordinated parts; every part must still be
read, never silently dropped:

* hyphen ellipsis ("Garten- und Terrassenlicht") -> "Gartenlicht und
  Terrassenlicht",
* a shared head ("Küche und Esszimmer Rollladen") -> "den Rollladen in der
  Küche und den Rollladen im Esszimmer",
* a place named in the first part holds for later parts without their own
  place ("Im Wohnzimmer das Licht aus und die Rollläden runter"),
* a part that names only places ("…, dann im Keller und in der Waschküche")
  repeats the object and the operation of the first part.

The rules are over word classes (place lexicon, device ontology, registry
names, particles); no sentence template. The result is one explicit surface
that the ordinary pipeline reads.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..entities import EntitySnapshot, normalize_for_compare
from .device_ontology import GENERA, Gender, analyse_word
from .language_frontend import LanguageToken, tokenize_language

__all__ = ("expand_coordination",)

_CONJUNCTIONS = frozenset({"und", "sowie", "oder"})
_CLAUSE_WORDS = frozenset({"und", "dann", "danach", "anschliessend", "sowie"})
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "alle", "beide"})
_PREPOSITIONS = frozenset({"im", "in", "am", "an", "beim", "bei", "auf", "vom", "von", "zum", "zur"})
_PARTICLES = frozenset({"an", "aus", "ein", "auf", "zu", "hoch", "runter", "rauf", "raus", "rein"})
_GLUE = frozenset({"und", "dann", "danach", "auch", "noch", "bitte", "anschliessend", "sowie", "ebenfalls"})
_ACCUSATIVE = {Gender.MASCULINE: "den", Gender.FEMININE: "die", Gender.NEUTER: "das"}
_GENDER = {item.key: item.gender for item in GENERA}
_TRIGGERS = frozenset({"wenn", "sobald", "falls", "sofern", "bei", "jeden", "jedes", "immer"})


def _head_surface(word: str) -> str | None:
    analysis = analyse_word(word)
    if analysis is None or not analysis.head or analysis.universal:
        return None
    head = analysis.head
    normalized = normalize_for_compare(word).replace("-", "")
    if not normalized.endswith(head) or len(normalized) == len(head):
        return None
    for size in range(len(head) - 2, len(word) + 1):
        if normalize_for_compare(word[-size:]) == head:
            return word[-size:]
    return head


def _expand_hyphen(text: str) -> str:
    """"Garten- und Terrassenlicht" -> "Gartenlicht und Terrassenlicht"."""
    tokens = tokenize_language(text)
    for index in range(len(tokens) - 4, -1, -1):
        first, dash, conjunction, second = tokens[index:index + 4]
        if not (
            first.is_word and dash.text in {"-", "–"} and dash.start == first.end
            and conjunction.canonical in _CONJUNCTIONS and second.is_word
        ):
            continue
        head = _head_surface(second.text)
        if head is None:
            continue
        text = text[:first.start] + first.text + head.lower() + text[dash.end:]
    return text


def _place_mentions(words: Sequence[str], entities: Sequence[EntitySnapshot]):
    from .place_model import PlaceKind, build_place_lexicon

    return [
        mention for mention in build_place_lexicon(entities).scan(words)
        if mention.place.kind in {PlaceKind.AREA, PlaceKind.FLOOR}
    ]


def _expand_shared_head(text: str, entities: Sequence[EntitySnapshot]) -> str:
    """"Küche und Esszimmer Rollladen" -> both places with their own object."""
    tokens = [token for token in tokenize_language(text) if token.is_word]
    words = [token.canonical for token in tokens]
    mentions = _place_mentions(words, entities)
    covered = _registry_covered(words, entities)
    for left, right in zip(mentions, mentions[1:]):
        if any(index in covered for index in range(left.token_start, left.token_end)):
            # "Flurlicht oben und Gäste-WC Licht": registry names, not places.
            continue
        if left.explicit_preposition or right.explicit_preposition:
            continue
        if right.token_start != left.token_end + 1 or words[left.token_end] not in _CONJUNCTIONS:
            continue
        noun_index = right.token_end
        if noun_index >= len(words):
            continue
        analysis = analyse_word(words[noun_index])
        if analysis is None or analysis.universal or not analysis.genera or analysis.modifier:
            continue
        start = left.token_start
        if start > 0 and words[start - 1] in _ARTICLES:
            start -= 1
        noun = tokens[noun_index].text
        article = "die" if analysis.plural else _ACCUSATIVE.get(_GENDER.get(analysis.genera[0], Gender.NEUTER), "das")
        rewritten = (
            f"{article} {noun} {left.place.label} {words[left.token_end]} "
            f"{article} {noun} {right.place.label}"
        )
        return text[:tokens[start].start] + rewritten + text[tokens[noun_index].end:]
    return text


def _registry_covered(words: Sequence[str], entities: Sequence[EntitySnapshot]) -> set[int]:
    """Word positions inside an exactly spoken registry name or alias."""
    from .self_correction import registry_name_table

    table = registry_name_table(entities)
    covered: set[int] = set()
    for size in range(min(table.max_words, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            if tuple(words[start:start + size]) in table.by_words:
                covered.update(range(start, start + size))
    return covered


def _segments(tokens: Sequence[LanguageToken]) -> list[tuple[int, int]]:
    """Clause ranges: at commas and at a joiner that opens a new clause."""
    from .self_correction import IMPERATIVE_VERBS as _VERBS

    ranges: list[tuple[int, int]] = []
    start = 0
    for index, token in enumerate(tokens):
        boundary = token.canonical in {",", ";"}
        if token.is_word and token.canonical in _CLAUSE_WORDS and index > start:
            following = next((item for item in tokens[index + 1:] if item.is_word), None)
            boundary = following is not None and (
                following.canonical in _VERBS | _ARTICLES | {"dann", "danach"}
                or token.canonical in {"dann", "danach"}
            )
        if boundary:
            if index > start:
                ranges.append((start, index))
            start = index + (1 if token.canonical in {",", ";"} else 0)
    if start < len(tokens):
        ranges.append((start, len(tokens)))
    return ranges


def _carry_place(text: str, entities: Sequence[EntitySnapshot]) -> str:
    """The place of the first part holds for later parts (and places-only
    parts repeat the first part's object and operation)."""
    from .self_correction import utterance_fields

    tokens = tokenize_language(text)
    ranges = _segments(tokens)
    if len(ranges) < 2:
        return text
    pieces = [text[tokens[start].start:tokens[end - 1].end] for start, end in ranges]
    first_fields, _rest = utterance_fields(pieces[0], entities)
    # Only a place with its preposition ("im Wohnzimmer") frames the whole
    # sentence; "Rollladen Schlafzimmer" is part of one noun phrase.
    places = [
        item for item in first_fields
        if item.kind == "place" and item.place is not None
        and normalize_for_compare(item.text.split()[0]) in _PREPOSITIONS
    ]
    targets = [item for item in first_fields if item.kind == "target"]
    particle = next((item.text for item in first_fields if item.kind == "particle"), None)
    edits: list[tuple[int, int, str]] = []
    for (start, end), piece in zip(ranges[1:], pieces[1:]):
        fields, rest = utterance_fields(piece, entities)
        offset = tokens[start].start
        kinds = {item.kind for item in fields}
        own_places = [item for item in fields if item.kind == "place"]
        content_words = [
            token.canonical for token in tokens[start:end]
            if token.is_word and token.canonical not in _GLUE | _PREPOSITIONS | _ARTICLES
        ]
        if (
            own_places and not rest and kinds <= {"place", "reference"} and len(targets) == 1
            and targets[0].registry is None and particle is not None and content_words
        ):
            # "…, dann im Keller und in der Waschküche": object and
            # operation of the first part at the new places.
            first_place = min(item.start for item in own_places)
            last_place = max(item.end for item in own_places)
            edits.append((
                offset + first_place, offset + last_place,
                f"{targets[0].text} {piece[first_place:last_place]} {particle}",
            ))
            continue
        if len(places) != 1 or own_places:
            continue
        genus_targets = [item for item in fields if item.kind == "target" and not item.registry_ids and item.genera]
        if len(genus_targets) != 1 or any(item.registry_ids for item in fields if item.kind == "target"):
            continue
        target = genus_targets[0]
        edits.append((offset + target.end, offset + target.end, f" {places[0].place_label}"))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def expand_coordination(text: str, entities: Iterable[EntitySnapshot]) -> str:
    """One explicit surface for coordinated parts; unchanged otherwise."""
    lowered = text.casefold()
    if "-" not in text and " und " not in lowered and "," not in text and " dann " not in lowered:
        return text
    entity_list = entities if isinstance(entities, list) else list(entities)
    if "-" in text:
        text = _expand_hyphen(text)
    if " und " in lowered or " oder " in lowered:
        text = _expand_shared_head(text, entity_list)
    if any(token.canonical in _TRIGGERS for token in tokenize_language(text)):
        # Trigger and action are separate parts of an automation; the
        # automation reader owns them.
        return text
    return _carry_place(text, entity_list)
