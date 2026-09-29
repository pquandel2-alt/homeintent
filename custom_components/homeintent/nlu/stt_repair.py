"""Speech-to-text compound repair (7.7 B8).

Home Assistant's speech recognition often splits German compounds:
"küchen licht", "außen beleuchtung", "kinder zimmer licht", "roll laden".
Adjacent words are joined only when the joined word is exactly a word of an
exposed registry name (device, alias, area, floor) or of the closed device
and spelling vocabulary. Nothing is guessed: no edit distance, no joining
of function words, and a spoken multi-word registry name stays as it is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from ..entities import EntitySnapshot, normalize_for_compare
from .domain_operations import DOMAIN_WORDS
from .semantic_catalog import CANONICAL_SPELLING_FORMS

_WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")
_MAX_PARTS = 3
_MIN_JOINED = 6
# Articles, prepositions, particles and verb parts are never part of a compound.
_FUNCTION_WORDS = frozenset({
    "das", "die", "der", "den", "dem", "des", "ein", "eine", "einen", "im", "am",
    "an", "aus", "auf", "zu", "und", "in", "ab", "um", "bei", "beim", "mit", "von",
    "vom", "zum", "zur", "mal", "ma", "bitte", "noch", "doch", "es", "ist", "sind",
    "mach", "schalte", "schalt", "fahr", "fahre", "stell", "stelle", "wie", "was",
    "oben", "unten", "links", "rechts", "alle", "aeh", "aehm", "hoch", "runter",
})


def _vocabulary(entities: Iterable[EntitySnapshot]) -> tuple[frozenset[str], frozenset[str]]:
    """(single registry/vocabulary words, spoken multi-word registry names)."""
    names = frozenset(
        name
        for entity in entities
        for name in (
            entity.friendly_name, *entity.aliases, entity.area_name or "",
            *entity.area_aliases, entity.floor_name or "",
        )
        if name
    )
    return _vocabulary_of(names)


@lru_cache(maxsize=4)
def _vocabulary_of(names: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Built once per set of registry names (the registry rarely changes)."""
    words: set[str] = {normalize_for_compare(word) for words in DOMAIN_WORDS.values() for word in words}
    words.update(normalize_for_compare(word) for word in CANONICAL_SPELLING_FORMS)
    phrases: set[str] = set()
    for name in names:
        parts = normalize_for_compare(name).replace("-", " ").split()
        words.update(part for part in parts if len(part) >= _MIN_JOINED)
        if len(parts) > 1:
            phrases.add(" ".join(parts))
    return frozenset(words), frozenset(phrases)


def join_split_compounds(text: str, entities: Iterable[EntitySnapshot]) -> str:
    """``text`` with split registry compounds joined (longest join first)."""
    tokens = list(_WORD.finditer(text))
    if len(tokens) < 2:
        return text
    vocabulary, phrases = _vocabulary(entities)
    keys = [normalize_for_compare(token.group()) for token in tokens]
    pieces: list[str] = []
    last = 0
    index = 0
    while index < len(tokens):
        joined_width = 0
        for width in range(min(_MAX_PARTS, len(tokens) - index), 1, -1):
            parts = keys[index:index + width]
            gaps = [
                text[tokens[position].end():tokens[position + 1].start()]
                for position in range(index, index + width - 1)
            ]
            joined = "".join(parts)
            if (
                all(gap == " " for gap in gaps)
                and not any(part in _FUNCTION_WORDS or len(part) < 2 for part in parts)
                and len(joined) >= _MIN_JOINED
                and joined in vocabulary
                and " ".join(parts) not in phrases
            ):
                joined_width = width
                break
        if joined_width:
            first, final = tokens[index], tokens[index + joined_width - 1]
            pieces.append(text[last:first.start()])
            pieces.append("".join(token.group() for token in tokens[index:index + joined_width]).lower())
            last = final.end()
            index += joined_width
        else:
            index += 1
    pieces.append(text[last:])
    return "".join(pieces)
