"""Shared, HA-free meaning representation for one German conversation turn.

This module composes the existing discourse and semantic scanners once.  It
does not resolve Home Assistant entities and can therefore be consumed by
commands, queries, automations and diagnostics without creating a new router.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from .semantic_lexicon import SemanticAnalysis, analyse_semantics
from .semantic_utterance import (
    MeaningClause,
    Modality,
    Polarity,
    SemanticUtterance,
    SpeechAct,
    analyse_utterance,
)
from .word_cues import has_word


class TemporalPerspective(Enum):
    UNSPECIFIED = auto()
    CURRENT = auto()
    PAST = auto()
    FUTURE = auto()


class CoordinationKind(Enum):
    NONE = auto()
    ADDITIVE = auto()
    ALTERNATIVE = auto()
    CONTRASTIVE = auto()


_PAST_RE = re.compile(
    r"\b(?:gestern|vorgestern|früher|damals|letzte[snm]?|"
    r"war|waren|hatte|hatten|lief|liefen|spielte|spielten|wurde|wurden)\b",
    re.I,
)
_FUTURE_RE = re.compile(
    r"\b(?:morgen|übermorgen|später|demnächst|künftig|"
    r"in\s+\d+\s+(?:sekunde|minute|stunde|tag)\w*|wird|werden)\b",
    re.I,
)
_CURRENT_RE = re.compile(r"\b(?:jetzt|gerade|aktuell|momentan|noch)\b", re.I)
@dataclass(frozen=True)
class SemanticTurn:
    """One compositional view shared before routing and entity resolution."""

    utterance: SemanticUtterance
    semantic_analysis: SemanticAnalysis
    temporal_perspective: TemporalPerspective
    coordination: CoordinationKind

    @property
    def source_text(self) -> str:
        return self.utterance.source_text

    @property
    def normalized_text(self) -> str:
        return self.utterance.normalized_text

    @property
    def speech_act(self) -> SpeechAct:
        return self.utterance.speech_act

    @property
    def modality(self) -> Modality:
        return self.utterance.modality

    @property
    def polarity(self) -> Polarity:
        return self.utterance.polarity

    @property
    def clauses(self) -> tuple[MeaningClause, ...]:
        return self.utterance.clauses

    @property
    def safe_to_execute_directly(self) -> bool:
        return self.utterance.safe_to_execute_directly


def _temporal_perspective(text: str) -> TemporalPerspective:
    if _PAST_RE.search(text):
        return TemporalPerspective.PAST
    if _FUTURE_RE.search(text):
        return TemporalPerspective.FUTURE
    if _CURRENT_RE.search(text):
        return TemporalPerspective.CURRENT
    return TemporalPerspective.UNSPECIFIED


def _coordination(text: str) -> CoordinationKind:
    if re.search(
        r"\b(?:aber|jedoch|außer|ausser|mit\s+ausnahme\s+von)\b", text, re.I
    ):
        return CoordinationKind.CONTRASTIVE
    if has_word(text, "oder", "entweder"):
        return CoordinationKind.ALTERNATIVE
    if has_word(text, "und", "sowie", "ausserdem"):
        return CoordinationKind.ADDITIVE
    return CoordinationKind.NONE


def analyse_turn(text: str) -> SemanticTurn:
    """Build the shared meaning view exactly once for one source string."""
    utterance = analyse_utterance(text)
    normalized = utterance.normalized_text
    return SemanticTurn(
        utterance=utterance,
        semantic_analysis=analyse_semantics(normalized),
        temporal_perspective=_temporal_perspective(normalized),
        coordination=_coordination(normalized),
    )
