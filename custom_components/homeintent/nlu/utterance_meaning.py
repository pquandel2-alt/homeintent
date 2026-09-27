"""Typed utterance meaning shared by every HomeIntent function.

The language frontend analyses a sentence exactly once into a
``LanguageDocument`` (tokens, structure, lexical semantics, temporal
expressions).  This module reads that document and derives the reusable
meaning dimensions that are not visible as single lexemes: *maintenance*
("lass X an" = keep a state, never an operation) and other modal frames.

Every rule here is stated over word classes and lexicon data from
``semantic_catalog``; there is no sentence template.  Consumers (direct
commands, automations, notifications, queries) read the frames instead of
re-scanning the raw text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .semantic_catalog import (
    MAINTAIN_BLOCKING_OBJECTS,
    MAINTAIN_VERB_FORMS,
    STATE_COMPLEMENT_WORDS,
)
from .semantic_lexicon import SemanticKind, analyse_semantics
from .semantic_state import SemanticState

__all__ = ("MaintainFrame", "maintain_frames", "segment_clauses")


class _Token(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def canonical(self) -> str: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...

    @property
    def is_word(self) -> bool: ...


_LEADING_DISCOURSE = frozenset({
    "bitte", "ok", "okay", "also", "gut", "nein", "ja", "achso", "ach", "und",
    "dann", "aber",
})
_TRAILING_DISCOURSE = frozenset({"bitte", "einfach", "ruhig", "so", "mal"})
# Words that join two predicates.  Coordinated maintenance clauses are
# evaluated separately so "Lass das Licht an und mach die Heizung aus"
# keeps the first object and still executes the second clause.
_CLAUSE_JOINERS = frozenset({"und", "aber", "sondern"})
_COMPOUND_MAINTAIN_SUFFIX = "lassen"


@dataclass(frozen=True)
class MaintainFrame:
    """One clause asking to keep an object in its current state."""

    object_text: str
    state: SemanticState
    char_start: int
    char_end: int


def segment_clauses(tokens: Sequence[_Token]) -> tuple[tuple[int, int], ...]:
    """Split a token sequence at commas and predicate coordinators.

    The result lists half-open token ranges.  A coordinator is a boundary
    only if a word follows it, so trailing particles never create empty
    clauses.
    """
    ranges: list[tuple[int, int]] = []
    start = 0
    for index, token in enumerate(tokens):
        if token.canonical in {",", ";"} or (
            token.is_word and token.canonical in _CLAUSE_JOINERS and index > start
        ):
            if index > start:
                ranges.append((start, index))
            start = index + 1
    if start < len(tokens):
        ranges.append((start, len(tokens)))
    return tuple(ranges)


def _words(tokens: Sequence[_Token], start: int, end: int) -> list[int]:
    return [index for index in range(start, end) if tokens[index].is_word]


def _maintain_in_clause(
    source: str, tokens: Sequence[_Token], start: int, end: int
) -> MaintainFrame | None:
    words = _words(tokens, start, end)
    while words and tokens[words[0]].canonical in _LEADING_DISCOURSE:
        words.pop(0)
    while words and tokens[words[-1]].canonical in _TRAILING_DISCOURSE - {"so"}:
        words.pop()
    if len(words) < 3:
        return None
    first = tokens[words[0]].canonical
    last = tokens[words[-1]].canonical
    object_words: list[int]
    state: SemanticState | None
    if first in MAINTAIN_VERB_FORMS:
        # Imperative first: "Lass(t) das Licht an", "Lassen Sie ... an".
        body = words[1:]
        if first == "lassen":
            if not body or tokens[body[0]].canonical != "sie":
                return None
            body = body[1:]
        if not body or tokens[body[0]].canonical in MAINTAIN_BLOCKING_OBJECTS:
            return None
        state = STATE_COMPLEMENT_WORDS.get(last)
        if state is None and len(body) >= 2 and last in {"brennen", "laufen"}:
            state = STATE_COMPLEMENT_WORDS[last]
        object_words = body[:-1]
    else:
        # Verb-final (modal shell): "Kannst du das Licht an lassen/anlassen".
        if last == "lassen" and len(words) >= 3:
            state = STATE_COMPLEMENT_WORDS.get(tokens[words[-2]].canonical)
            object_words = words[:-2]
        elif last.endswith(_COMPOUND_MAINTAIN_SUFFIX) and len(last) > len(
            _COMPOUND_MAINTAIN_SUFFIX
        ):
            state = STATE_COMPLEMENT_WORDS.get(last[: -len(_COMPOUND_MAINTAIN_SUFFIX)])
            object_words = words[:-1]
        else:
            return None
        # Drop the modal shell ("kannst du", "könntest du bitte").
        while object_words and tokens[object_words[0]].canonical in {
            "kannst", "koenntest", "wuerdest", "du", "bitte", "sollst", "musst",
        }:
            object_words.pop(0)
    if state is None or not object_words:
        return None
    object_text = source[tokens[object_words[0]].start:tokens[object_words[-1]].end]
    object_semantics = analyse_semantics(object_text)
    # Another operation inside the object span means the clause is not a
    # pure "leave it" instruction ("Lass das Licht anmachen", "lass die
    # Rollläden runterfahren").  Such clauses are left to the command path.
    if object_semantics.values(SemanticKind.ACTION) or any(
        span.text.casefold() not in MAINTAIN_VERB_FORMS
        for span in object_semantics.matching(SemanticKind.COMMAND_MARKER)
    ):
        return None
    return MaintainFrame(
        object_text=object_text,
        state=state,
        char_start=tokens[words[0]].start,
        char_end=tokens[words[-1]].end,
    )


def maintain_frames(
    source: str, tokens: Sequence[_Token]
) -> tuple[tuple[MaintainFrame | None, ...], tuple[tuple[int, int], ...]]:
    """Return one optional maintenance frame per clause and the clause ranges."""
    ranges = segment_clauses(tokens)
    return (
        tuple(_maintain_in_clause(source, tokens, start, end) for start, end in ranges),
        ranges,
    )


_STATE_PREDICATES = {
    SemanticState.ON: "bleibt an",
    SemanticState.ACTIVE: "läuft weiter",
    SemanticState.OFF: "bleibt aus",
    SemanticState.OPEN: "bleibt offen",
    SemanticState.CLOSED: "bleibt zu",
    SemanticState.UNKNOWN: "bleibt, wie es ist",
}


def render_maintain(frames: Sequence[MaintainFrame]) -> str:
    """German confirmation that nothing is changed for kept objects."""
    parts = [
        f"{frame.object_text[:1].upper()}{frame.object_text[1:]} "
        f"{_STATE_PREDICATES.get(frame.state, 'bleibt, wie es ist')}."
        for frame in frames
    ]
    return "In Ordnung. " + " ".join(parts) + " Ich ändere nichts."
