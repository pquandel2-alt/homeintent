"""Locative prepositions ("im", "in der", "in dem", "am", "beim") (7.7 B6).

One word-level reading instead of four copies of the same sentence
pattern: a cue is one of the contracted prepositions or "in" directly
followed by "der"/"dem".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

_WORD = re.compile(r"\w+")
_CONTRACTED = frozenset({"im", "am", "beim"})
_ARTICLES = frozenset({"der", "dem"})


def locative_spans(text: str) -> Iterator[tuple[int, int]]:
    """Character spans of every locative preposition in ``text``."""
    words = list(_WORD.finditer(text))
    for index, word in enumerate(words):
        lowered = word.group().casefold()
        if lowered in _CONTRACTED:
            yield word.start(), word.end()
        elif lowered == "in" and index + 1 < len(words):
            following = words[index + 1]
            gap = text[word.end():following.start()]
            if gap and gap.isspace() and following.group().casefold() in _ARTICLES:
                yield word.start(), following.end()


def has_locative_cue(text: str, *, followed: bool = False) -> bool:
    """Whether ``text`` contains a locative preposition; with ``followed``
    only one that has a word after it."""
    return any(
        not followed or text[end:end + 1].isspace()
        for _start, end in locative_spans(text)
    )


def strip_locative_prepositions(name: str) -> str:
    """``name`` without its locative prepositions, whitespace collapsed."""
    parts, last = [], 0
    for start, end in locative_spans(name):
        parts.append(name[last:start])
        parts.append(" ")
        last = end
    parts.append(name[last:])
    return " ".join("".join(parts).split())


def drop_locative_prepositions(text: str, *, spoken: frozenset[str] | None = None) -> str:
    """``text`` with each locative preposition that is followed by a word
    removed together with the whitespace after it; ``spoken`` limits the
    removal to those forms (e.g. ``{"im", "in der", "in dem"}``)."""
    parts, last = [], 0
    for start, end in locative_spans(text):
        form = " ".join(text[start:end].casefold().split())
        if not text[end:end + 1].isspace() or (spoken is not None and form not in spoken):
            continue
        parts.append(text[last:start])
        last = end + len(text[end:]) - len(text[end:].lstrip())
    parts.append(text[last:])
    return "".join(parts)


def names_place_after_locative(text: str, names: Iterable[str]) -> bool:
    """Whether a locative preposition is directly followed by one of
    ``names`` (compared case-insensitively, as whole words)."""
    folded = text.casefold()
    for _start, end in locative_spans(text):
        if not text[end:end + 1].isspace():
            continue
        rest = folded[end:].lstrip()
        for name in names:
            name = name.casefold()
            if name and rest.startswith(name) and not rest[len(name):len(name) + 1].isalnum():
                return True
    return False
