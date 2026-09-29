"""Shared deterministic German lexicon with source spans.

The semantic compiler must reason about *meaning components*, not complete
sentences.  This module is the single dependency-free vocabulary used for
that purpose.  It returns every recognised component together with its
position in the utterance, so consumers can detect overlaps, conflicts and
unexplained meaningful words without depending on word order.

The lexicon deliberately contains no Home Assistant runtime objects and no
statistical model.  Adding a synonym extends every permutation in which that
meaning can occur; it does not add another sentence template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from functools import lru_cache
from typing import Iterable

from ..entities import normalize_for_compare
from .semantic_catalog import (
    ACTION_EXPRESSIONS,
    AUTOMATION_CUE_ENTRIES,
    COMMAND_MARKER_EXPRESSIONS,
    COMPARATOR_ENTRIES,
    DEVICE_CLASS_ENTRIES,
    DOMAIN_EXPRESSIONS,
    MEASUREMENT_PROPERTY_SPECS,
    PROPERTY_ENTRIES,
    QUANTIFIER_ENTRIES,
    QUERY_SCOPE_ENTRIES,
    RELATION_ENTRIES,
    STATE_ENTRIES,
)

__all__ = [
    "LEXEMES",
    "Lexeme",
    "MEASUREMENT_PROPERTY_SPECS",
    "SemanticAnalysis",
    "SemanticKind",
    "SemanticSpan",
    "analyse_semantics",
]


class SemanticKind(Enum):
    """Kinds of independently observable meaning components."""

    COMMAND_MARKER = auto()
    ACTION = auto()
    DOMAIN = auto()
    DEVICE_CLASS = auto()
    STATE = auto()
    QUANTIFIER = auto()
    QUERY_SCOPE = auto()
    AUTOMATION_CUE = auto()
    PROPERTY = auto()
    COMPARATOR = auto()
    RELATION = auto()


@dataclass(frozen=True)
class Lexeme:
    kind: SemanticKind
    value: object
    expressions: tuple[str, ...]


@dataclass(frozen=True)
class SemanticSpan:
    kind: SemanticKind
    value: object
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class SemanticAnalysis:
    text: str
    spans: tuple[SemanticSpan, ...]
    unexplained_tokens: tuple[str, ...]

    def values(self, kind: SemanticKind) -> frozenset[object]:
        return frozenset(span.value for span in self.spans if span.kind is kind)

    def matching(self, kind: SemanticKind) -> tuple[SemanticSpan, ...]:
        return tuple(span for span in self.spans if span.kind is kind)

    def conflicts(self, kind: SemanticKind) -> bool:
        return len(self.values(kind)) > 1


# Expressions are regular-expression fragments.  They describe individual
# meanings, never whole sentences.  Inflection is therefore centralised once.
LEXEMES: tuple[Lexeme, ...] = (
    Lexeme(SemanticKind.COMMAND_MARKER, True, COMMAND_MARKER_EXPRESSIONS),
    *(Lexeme(SemanticKind.ACTION, action, expressions)
      for action, expressions in ACTION_EXPRESSIONS.items()),
    *(Lexeme(SemanticKind.DOMAIN, domain, expressions)
      for domain, expressions in DOMAIN_EXPRESSIONS.items()),
    *(Lexeme(SemanticKind.DEVICE_CLASS, item.value, item.expressions)
      for item in DEVICE_CLASS_ENTRIES),
    *(Lexeme(SemanticKind.STATE, item.value, item.expressions)
      for item in STATE_ENTRIES),
    *(Lexeme(SemanticKind.QUANTIFIER, item.value, item.expressions)
      for item in QUANTIFIER_ENTRIES),
    *(Lexeme(SemanticKind.QUERY_SCOPE, item.value, item.expressions)
      for item in QUERY_SCOPE_ENTRIES),
    *(Lexeme(SemanticKind.AUTOMATION_CUE, item.value, item.expressions)
      for item in AUTOMATION_CUE_ENTRIES),
    *(Lexeme(SemanticKind.PROPERTY, item.value, item.expressions)
      for item in PROPERTY_ENTRIES),
    *(Lexeme(SemanticKind.COMPARATOR, item.value, item.expressions)
      for item in COMPARATOR_ENTRIES),
    *(Lexeme(SemanticKind.RELATION, item.value, item.expressions)
      for item in RELATION_ENTRIES),
)


_WORD_RE = re.compile(r"[\wäöüß]+", re.I)
_FILLERS = frozenset(
    normalize_for_compare(word)
    for word in (
        "der die das den dem des ein eine einen einem einer bitte kannst du mir mal doch "
        "jetzt vorhanden vorhandenen im in am auf beim nach von zur zum und oder soll sollen dort "
        "möchte will ich dass ob davon denn noch es sie sein sind ist stehen steht hat haben werde werden wird welche welcher welches was wie "
        "kannste könntest koenntest würdest wuerdest würd wuerd gern wär waer nett "
        "äh aeh also eben irgendwie "
        # Hedges are modality (semantic_utterance decides uncertain vs.
        # polite); as words they carry no target or operation meaning.
        "vielleicht eventuell irgendwann bald "
        "nicht "
        "wiedergabe "
        "komplett ganz ganze ganzen ganzer ganzes vollständig halb halbe halber halben hälfte höhe prozent einmal zwar"
    ).split()
)


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[Lexeme, re.Pattern[str]], ...]:
    compiled: list[tuple[Lexeme, re.Pattern[str]]] = []
    for lexeme in LEXEMES:
        # Longest textual alternative first makes the chosen spans stable.
        expressions = sorted(lexeme.expressions, key=len, reverse=True)
        compiled.append((lexeme, re.compile(r"\b(?:" + "|".join(expressions) + r")\b", re.I)))
    return tuple(compiled)


def _remove_shadowed(spans: Iterable[SemanticSpan]) -> tuple[SemanticSpan, ...]:
    """Remove shorter same-kind spans contained in a longer semantic unit.

    The values need not agree: ``nicht höher als`` is one LTE comparator and
    must shadow the embedded GT phrase ``höher als``. Keeping both would turn
    a single, well-scoped comparison into an artificial meaning conflict.
    """
    ordered = sorted(spans, key=lambda span: (span.start, -(span.end - span.start), span.kind.value))
    selected: list[SemanticSpan] = []
    for span in ordered:
        if any(
            other.kind is span.kind
            and other.start <= span.start
            and other.end >= span.end
            for other in selected
        ):
            continue
        selected.append(span)
    return tuple(sorted(selected, key=lambda span: (span.start, span.end, span.kind.value)))


def _level_role(text: str, start: int) -> str:
    from .place_model import level_word_role

    before = [normalize_for_compare(word) for word in _WORD_RE.findall(text[:start])]
    words = before + [normalize_for_compare(word) for word in _WORD_RE.findall(text[start:])]
    return level_word_role(words, len(before))


def analyse_semantics(text: str) -> SemanticAnalysis:
    """Scan ``text`` once and return order-independent semantic evidence."""
    found: list[SemanticSpan] = []
    for lexeme, pattern in _compiled():
        for match in pattern.finditer(text):
            if (
                lexeme.kind in {SemanticKind.STATE, SemanticKind.ACTION}
                and normalize_for_compare(match.group(0)) in {"oben", "unten"}
                and _level_role(text, match.start()) == "floor"
            ):
                # "Wie viele Rollläden gibt es oben?", "Fahr oben alle
                # Rollläden runter": the floor, neither position nor
                # direction - one rule with the place model (7.6.1).
                continue
            found.append(SemanticSpan(
                kind=lexeme.kind,
                value=lexeme.value,
                start=match.start(),
                end=match.end(),
                text=match.group(0),
            ))
    spans = _remove_shadowed(found)
    covered = [False] * len(text)
    for span in spans:
        for position in range(span.start, span.end):
            covered[position] = True
    unexplained = tuple(
        match.group(0)
        for match in _WORD_RE.finditer(text)
        if not all(covered[position] for position in range(match.start(), match.end()))
        and normalize_for_compare(match.group(0)) not in _FILLERS
        and not match.group(0).isdigit()
    )
    return SemanticAnalysis(text=text, spans=spans, unexplained_tokens=unexplained)
