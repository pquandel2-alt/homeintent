"""Word-level cues: does an utterance contain a word of a given form?

Replaces raw-text regular expressions of the shape ``\\b(?:stell\\w*|quelle)\\b``
with a token check over compare-normalized words.  Forms are written
compare-normalized (``ä`` -> ``ae``, ``ß`` -> ``ss``); a trailing ``*``
matches any continuation (``stell*`` -> stelle, stellen), a leading ``*`` any
compound head (``*heizung`` -> Fußbodenheizung).
"""

from __future__ import annotations

from functools import lru_cache

from ..entities import normalize_for_compare

__all__ = ("has_phrase", "has_word", "words_of")

_STRIP = ".,!?;:\"'„“”()[]"


@lru_cache(maxsize=2048)
def _word_tuple(text: str) -> tuple[str, ...]:
    return tuple(word.strip(_STRIP) for word in normalize_for_compare(text).split())


def words_of(text: str) -> list[str]:
    return list(_word_tuple(text))


def has_word(text: str, *forms: str) -> bool:
    for word in _word_tuple(text):
        for form in forms:
            if form.startswith("*") and word.endswith(form[1:]):
                return True
            if form.endswith("*") and word.startswith(form[:-1]):
                return True
            if word == form:
                return True
    return False


def has_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {' '.join(words_of(text))} "
