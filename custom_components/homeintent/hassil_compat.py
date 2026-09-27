"""Version-stable hassil recognition for grammars with wildcard slots.

hassil 3.12 (shipped with Home Assistant 2026.9) narrows text-slot candidates
differently than 3.11. For a sentence like ``"in {amount} {unit}
{command_text}"`` with the units ``Minuten``/``Minute`` it may now match the
shorter ``Minute`` first and let the open wildcard start in the middle of the
word: ``"in 30 Minuten erinnere mich"`` yields ``command_text = "n erinnere
mich"``. Every parser that places a wildcard directly after a text list is
affected (relative-time commands, delayed pushes, reminders).

``recognize_aligned`` keeps hassil's own first result whenever every wildcard
value consists of whole words of the spoken text (the common case, so no
extra cost; compared word by word, so punctuation hassil strips does not
matter) and otherwise walks ``recognize_all`` for the first result
that does. A result that splits a word is never returned; if hassil offers no
aligned alternative the sentence is treated as not recognized instead of
being executed with a mangled command (never a partial guess).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from hassil import Intents, RecognizeResult, SlotList, recognize, recognize_all


def _wildcard_values(result: RecognizeResult) -> list[str]:
    values: list[str] = []
    for entity in result.entities.values():
        if getattr(entity, "is_wildcard", False):
            values.append(str(entity.value))
    return values


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _is_word_aligned(text_words: list[str], value: str) -> bool:
    value_words = _words(value)
    if not value_words:
        return True
    width = len(value_words)
    return any(
        text_words[index : index + width] == value_words
        for index in range(len(text_words) - width + 1)
    )


def is_word_aligned(text: str, result: RecognizeResult) -> bool:
    """Whether every wildcard of ``result`` covers whole words of ``text``."""
    text_words = _words(text)
    return all(_is_word_aligned(text_words, value) for value in _wildcard_values(result))


def recognize_aligned(
    text: str,
    intents: Intents,
    slot_lists: Mapping[str, SlotList] | None = None,
    language: str = "de",
    **kwargs: Any,
) -> RecognizeResult | None:
    """``hassil.recognize`` that never returns a word-splitting wildcard."""
    lists = dict(slot_lists) if slot_lists is not None else None
    result = recognize(text, intents, slot_lists=lists, language=language, **kwargs)
    if result is None or is_word_aligned(text, result):
        return result
    for candidate in recognize_all(text, intents, slot_lists=lists, language=language, **kwargs):
        if is_word_aligned(text, candidate):
            return candidate
    return None
