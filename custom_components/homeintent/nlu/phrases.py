"""Multi-word expressions as lexicon data (7.5.x language islands).

A phrase is a sequence of word specs separated by spaces; each spec lists
alternatives with ``|`` and may end in ``*`` for a stem ("zeig|zeige",
"erledig*", "was steht|ist|fehlt"). Phrases are data: they are matched on
word tokens (with their character offsets in the original text), never
compiled into sentence patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

from ..entities import normalize_for_compare

_TOKEN = re.compile(r"\d{1,2}:\d{2}|[^\s.,;:!?„“\"'()]+")


@dataclass(frozen=True)
class Word:
    text: str
    key: str
    start: int
    end: int


def words(text: str) -> list[Word]:
    """Word tokens with normalized keys and character offsets."""
    return [
        Word(match.group(), normalize_for_compare(match.group()), match.start(), match.end())
        for match in _TOKEN.finditer(text)
    ]


@lru_cache(maxsize=4096)
def _compile(phrase: str) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    specs = []
    for part in phrase.split():
        exact: list[str] = []
        stems: list[str] = []
        for alternative in part.split("|"):
            key = normalize_for_compare(alternative.rstrip("*"))
            (stems if alternative.endswith("*") else exact).append(key)
        specs.append((tuple(exact), tuple(stems)))
    return tuple(specs)


def word_is(word: Word, spec: str) -> bool:
    ((exact, stems),) = _compile(spec)
    return word.key in exact or any(word.key.startswith(stem) for stem in stems)


def match_at(tokens: Sequence[Word], index: int, phrase: str) -> int | None:
    """Number of tokens the phrase covers at ``index``, or ``None``."""
    specs = _compile(phrase)
    if index + len(specs) > len(tokens):
        return None
    for offset, (exact, stems) in enumerate(specs):
        key = tokens[index + offset].key
        if key not in exact and not any(key.startswith(stem) for stem in stems):
            return None
    return len(specs)


def find(tokens: Sequence[Word], phrases: Iterable[str], start: int = 0) -> tuple[int, int] | None:
    """(index, length) of the leftmost phrase occurrence from ``start``."""
    candidates = tuple(phrases)
    for index in range(start, len(tokens)):
        for phrase in candidates:
            length = match_at(tokens, index, phrase)
            if length is not None:
                return index, length
    return None


def has(tokens: Sequence[Word], *phrases: str) -> bool:
    return find(tokens, phrases) is not None


def keys(tokens: Sequence[Word]) -> list[str]:
    return [token.key for token in tokens]


class Span:
    """The part of ``re.Match`` callers use: ``start``/``end``/``span``/``group``."""

    def __init__(self, start: int, end: int, *groups: str, named: dict[str, str] | None = None) -> None:
        self._start, self._end, self._groups, self._named = start, end, groups, named or {}

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def span(self) -> tuple[int, int]:
        return self._start, self._end

    def group(self, index: int | str = 0) -> str:
        if isinstance(index, str):
            return self._named.get(index, "")
        return (self._groups[index - 1] or "") if index else ""

    def groupdict(self) -> dict[str, str]:
        return dict(self._named)


__all__ = ("Span", "Word", "find", "has", "keys", "match_at", "word_is", "words")
