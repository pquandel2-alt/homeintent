"""Deterministic utterance and clause semantics for natural German.

This module deliberately models reusable meaning dimensions instead of full
sentence templates.  It is HA-free and cheap enough to run before every
parser.  Consumers still validate all targets, capabilities and values; the
analysis here only identifies the kind and safety shape of an utterance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from .normalize import normalize
from .semantic_catalog import (
    ACTION_EXPRESSIONS,
    COMMAND_MARKER_EXPRESSIONS,
    COMPARATOR_ENTRIES,
    DOMAIN_EXPRESSIONS,
    INTENT_BY_DOMAIN_ACTION,
    PROPERTY_ENTRIES,
    regex_union,
)


class SpeechAct(Enum):
    COMMAND = auto()
    QUERY = auto()
    AUTOMATION = auto()
    CONFIRMATION = auto()
    CORRECTION = auto()
    STATEMENT = auto()


class Modality(Enum):
    DIRECT = auto()
    POLITE = auto()
    WISH = auto()
    HYPOTHETICAL = auto()
    UNCERTAIN = auto()
    # "Lass das Licht an": keep the current state; never an operation.
    MAINTAIN = auto()


class Polarity(Enum):
    POSITIVE = auto()
    NEGATIVE = auto()


class PragmaticDisposition(Enum):
    EXECUTABLE_REQUEST = auto()
    ASK_BEFORE_ACTION = auto()
    READ_ONLY = auto()
    NON_ACTION = auto()


class ClauseRole(Enum):
    MAIN = auto()
    TRIGGER = auto()
    CONDITION = auto()
    ACTION = auto()


@dataclass(frozen=True)
class MeaningClause:
    text: str
    role: ClauseRole
    connector: str | None = None


@dataclass(frozen=True)
class SemanticUtterance:
    source_text: str
    normalized_text: str
    speech_act: SpeechAct
    modality: Modality
    polarity: Polarity
    clauses: tuple[MeaningClause, ...]

    @property
    def safe_to_execute_directly(self) -> bool:
        return (
            self.speech_act is SpeechAct.COMMAND
            and self.modality not in {
                Modality.HYPOTHETICAL,
                Modality.UNCERTAIN,
                Modality.MAINTAIN,
            }
            and self.polarity is Polarity.POSITIVE
        )

    @property
    def pragmatic_disposition(self) -> PragmaticDisposition:
        if self.safe_to_execute_directly:
            return PragmaticDisposition.EXECUTABLE_REQUEST
        if self.speech_act is SpeechAct.QUERY:
            return PragmaticDisposition.READ_ONLY
        if (
            self.speech_act is SpeechAct.STATEMENT
            and _IMPLICIT_COMPLAINT_RE.search(self.normalized_text)
        ):
            return PragmaticDisposition.ASK_BEFORE_ACTION
        return PragmaticDisposition.NON_ACTION


_QUESTION_RE = re.compile(
    r"^\s*(?:wer|was|wie|wo|wann|warum|wieso|welch\w*|ist|sind|war|waren|hat|haben|"
    r"gibt|kann|kannst|könnte|koennte)\b",
    re.I,
)
_DELIBERATIVE_QUESTION_RE = re.compile(
    r"^\s*(?:(?:soll|muss|darf)\s+ich\b|wollen\s+wir\b|"
    r"ich\s+frage\s+mich\s*,?\s*ob\b)",
    re.I,
)
_QUERY_REQUEST_RE = re.compile(
    r"^\s*(?:sag|sage|zeig|zeige|nenn|nenne)\s+(?:mir\s+)?(?:bitte\s+)?",
    re.I,
)
_INDIRECT_QUERY_RE = re.compile(r"^\s*ob\b", re.I)
_AUTOMATION_RE = re.compile(
    r"\b(?:wenn|sobald|falls|sofern|jedes\s+mal\s+wenn|immer\s+wenn)\b",
    re.I,
)
_META_AUTOMATION_QUERY_RE = re.compile(
    r"^\s*(?:was\s+(?:(?:würde|wuerde)\s+)?(?:passieren|geschehen|passiert|geschieht)|"
    r"wie\s+wäre\s+es|was\s+wäre)\b.*\b(?:wenn|falls)\b",
    re.I,
)
_INFORMATIONAL_CAN_RE = re.compile(r"^\s*kann\s+man\b", re.I)
_CONFIRMATION_RE = re.compile(
    r"^\s*(?:ja|nein|okay|ok|bestätigen|abbrechen|mach das)\s*[.!?]*$",
    re.I,
)
_CORRECTION_RE = re.compile(
    r"^\s*(?:nein[,.]?\s*)?(?:ich\s+meinte|nicht\s+.+?\s+sondern|nur\s+)",
    re.I,
)
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:wäre|waere|würde|wuerde|hätte|haette)\b.*\b(?:wenn|falls)\b"
    r"|\bangenommen\b|\bwas\s+wäre\s+wenn\b|\bwas\s+(?:passiert|geschieht)\b.*\bwenn\b",
    re.I,
)
_UNCERTAIN_RE = re.compile(
    r"\b(?:vielleicht|eventuell|möglicherweise|moeglicherweise|normalerweise)\b",
    re.I,
)
_WISH_RE = re.compile(
    r"\b(?:ich\s+(?:hätte|haette|möchte|moechte|will)\s+(?:gern|gerne)?|"
    r"ich\s+wünsche\s+mir)\b",
    re.I,
)
_POLITE_REQUEST_SHELL_RE = re.compile(
    r"^\s*(?:bitte\s+)?(?:kannst|könntest|koenntest|würdest|wuerdest|magst)\s+du\b",
    re.I,
)
_POLITE_RE = re.compile(
    r"\b(?:bitte|kannst\s+du|könntest\s+du|koenntest\s+du|"
    r"wäre\s+es\s+möglich|waere\s+es\s+moeglich)\b",
    re.I,
)
_NEGATION_RE = re.compile(
    r"\b(?:nicht(?!\s+(?:höher|hoeher|niedriger|mehr|weniger)\s+als\b)|"
    r"kein\w*|niemals|keinesfalls|auf\s+keinen\s+fall)\b",
    re.I,
)
_IMPLICIT_COMPLAINT_RE = re.compile(
    r"\b(?:mir\s+zu|fuer\s+mich\s+zu|für\s+mich\s+zu)\s+"
    r"(?:hell|dunkel|warm|kalt|laut|leise)\b",
    re.I,
)
_COMMAND_RE = re.compile(
    r"\b(?:" + regex_union(list(COMMAND_MARKER_EXPRESSIONS))
    + r"|regel\w*|regle\w*|zeig\w*|sorge\w*|schick\w*|"
    r"find\w*|such\w*|ort\w*|lokalisier\w*|piep\w*|heb\w*|unmute|"
    r"wähl\w*|waehl\w*|auswähl\w*|auswaehl\w*|send\w*|"
    r"drück\w*|drueck\w*|"
    r"möchte|moechte)\b",
    re.I,
)
def _elliptical_command_expression() -> str:
    """Build only executable domain/action pairs, once at import time.

    A generic device noun next to a state word is not automatically a
    command (``Ist das Radio an?``).  Pairing each domain with the actions
    actually registered for it prevents unrelated vocabulary such as
    ``Wert``/``Kamera`` from becoming executable discourse evidence.
    """
    pairs: list[str] = []
    for domain, action in INTENT_BY_DOMAIN_ACTION:
        domain_expression = regex_union(list(DOMAIN_EXPRESSIONS[domain]))
        action_expression = regex_union(list(ACTION_EXPRESSIONS[action]))
        pairs.append(
            rf"(?:\b{domain_expression}\b.*\b{action_expression}\b|"
            rf"\b{action_expression}\b.*\b{domain_expression}\b)"
        )
    return regex_union(pairs)


_ELLIPTICAL_COMMAND_RE = re.compile(_elliptical_command_expression(), re.I)
_PROPERTY_CUE_RE = re.compile(
    r"\b" + regex_union([
        expression
        for entry in PROPERTY_ENTRIES
        for expression in entry.expressions
    ]) + r"\b",
    re.I,
)
_COMPARATOR_CUE_RE = re.compile(
    r"\b" + regex_union([
        expression
        for entry in COMPARATOR_ENTRIES
        for expression in entry.expressions
    ]) + r"\b",
    re.I,
)
_COPULAR_STATE_QUESTION_RE = re.compile(
    r"^\s*(?:ist|sind|war|waren|welch\w*|was\s+(?:ist|macht|zeigt)|wie\s+ist)\b",
    re.I,
)
_DEVICE_NOUN_EXPRESSION = regex_union([
    expression
    for expressions in DOMAIN_EXPRESSIONS.values()
    for expression in expressions
])
_DOMAIN_CUE_RE = re.compile(r"\b" + _DEVICE_NOUN_EXPRESSION + r"\b", re.I)
_NUMERIC_ASSIGNMENT_RE = re.compile(
    r"\bauf\s+-?\d{1,3}\s*(?:prozent|%|grad|°\s*c|°c)\b",
    re.I,
)
_POSITION_ASSIGNMENT_RE = re.compile(
    r"\bauf\s+(?:halb\w*|halbe\w*|fünfzig\s+prozent|\d+\s*(?:prozent|%))\b",
    re.I,
)
_PROPERTY_NOUN_QUERY_RE = re.compile(
    r"^\s*(?:temperatur|luftfeuchtigkeit|feuchtigkeit|batteriestand|batterie|"
    r"leistung|strom|energie|helligkeit)\b.*\b(?:im|in\s+der|in\s+dem)\b",
    re.I,
)
_VERB_FIRST_DEVICE_RE = re.compile(
    rf"^\s*(?P<verb>[a-zäöüß]+(?:t|en))\b(?P<body>.*\b{_DEVICE_NOUN_EXPRESSION}\b.*)[?.!]*$",
    re.I,
)
_VERB_FIRST_MODAL_RE = re.compile(
    r"^(?:kannst|koenntest|könntest|wuerdest|würdest|moechtest|möchtest|sollst)$",
    re.I,
)
_SUBJECT_START_RE = re.compile(
    r"^\s*(?:der|die|das|ein|eine|mein\w*|dein\w*|unser\w*|dies\w*|alle)\b",
    re.I,
)
_TRIGGER_CUE_RE = re.compile(r"\b(?P<cue>wenn|sobald|falls)\b", re.I)
_CONDITION_CUE_RE = re.compile(r"\b(?P<cue>nur\s+wenn|sofern)\b", re.I)
_CONTEXTUAL_FOLLOWUP_RE = re.compile(
    r"^\s*(?:und|dann|danach|nein[,.]?|stattdessen)\b|"
    r"\b(?:der|die|das)\s+andere\w*\b|"
    r"^\s*(?:er|sie|es|das|dort|davon)\b|"
    r"\b(?:dort|davon)\b",
    re.I,
)


def is_contextual_followup(text: str) -> bool:
    """Whether a turn explicitly asks to inherit prior dialog meaning."""
    return _CONTEXTUAL_FOLLOWUP_RE.search(normalize(text)) is not None


def _modality(text: str) -> Modality:
    if _HYPOTHETICAL_RE.search(text):
        return Modality.HYPOTHETICAL
    if _UNCERTAIN_RE.search(text):
        # Inside a polite request shell ("Könntest du vielleicht mal ...")
        # the hedge is politeness, not uncertainty about the wish.
        if _POLITE_REQUEST_SHELL_RE.match(text):
            return Modality.POLITE
        return Modality.UNCERTAIN
    if _WISH_RE.search(text):
        return Modality.WISH
    if _POLITE_RE.search(text):
        return Modality.POLITE
    return Modality.DIRECT


def _is_verb_first_device_question(text: str) -> bool:
    """Recognize German finite-verb-first questions structurally.

    Third-person questions (``Spielt das Radio?``) share operation lexemes
    with commands, but not the usual imperative morphology (``Spiele ...``)
    or modal command shell (``Kannst du ...?``).  A question mark is strong
    evidence; without punctuation, a nominative subject directly after the
    finite verb is accepted conservatively.  Ambiguity always resolves to
    QUERY because failing to act is safer than executing a question.
    """
    match = _VERB_FIRST_DEVICE_RE.match(text)
    if match is None or _VERB_FIRST_MODAL_RE.fullmatch(match.group("verb")):
        return False
    return text.rstrip().endswith("?") or _SUBJECT_START_RE.match(match.group("body")) is not None


def _clauses(text: str, speech_act: SpeechAct) -> tuple[MeaningClause, ...]:
    if speech_act is not SpeechAct.AUTOMATION:
        return (MeaningClause(text, ClauseRole.MAIN),)
    condition = _CONDITION_CUE_RE.search(text)
    trigger = _TRIGGER_CUE_RE.search(text)
    cue = condition or trigger
    if cue is None:
        return (MeaningClause(text, ClauseRole.MAIN),)
    before = text[: cue.start()].strip(" ,")
    after = text[cue.end() :].strip(" ,")
    dependent_role = ClauseRole.CONDITION if condition is not None else ClauseRole.TRIGGER
    # This is intentionally only a role-preserving first split. Existing
    # automation parsers still determine the exact trigger/action boundary.
    clauses: list[MeaningClause] = []
    if before:
        clauses.append(MeaningClause(before, ClauseRole.ACTION))
    if after:
        clauses.append(MeaningClause(after, dependent_role, cue.group("cue")))
    return tuple(clauses) or (MeaningClause(text, ClauseRole.MAIN),)


def analyse_utterance(text: str) -> SemanticUtterance:
    """Return the deterministic discourse/safety shape of ``text``."""
    normalized = normalize(text)
    if _CONFIRMATION_RE.fullmatch(normalized):
        speech_act = SpeechAct.CONFIRMATION
    elif _CORRECTION_RE.search(normalized):
        speech_act = SpeechAct.CORRECTION
    elif _META_AUTOMATION_QUERY_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    elif _AUTOMATION_RE.search(normalized):
        speech_act = SpeechAct.AUTOMATION
    elif _is_verb_first_device_question(normalized):
        speech_act = SpeechAct.QUERY
    elif _DELIBERATIVE_QUESTION_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    elif _INDIRECT_QUERY_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    elif _QUERY_REQUEST_RE.search(text) and re.search(r"^\s*\w+\s+mir\b", text, re.I):
        # ``Zeige mir den Zustand ...`` asks for information.  The otherwise
        # identical transitive form ``Zeige die Kamera auf dem TV`` is a
        # device-to-device command and continues into the command branch.
        speech_act = SpeechAct.QUERY
    elif re.search(
        r"^\s*(?:wer|was|wie|welch\w*|wo|wann|warum|wieso|gibt\s+es|haben\s+wir)\b",
        normalized,
        re.I,
    ):
        # Explicit interrogatives own their clause even when a state word
        # such as "eingeschaltet" is also an operation lexeme.
        speech_act = SpeechAct.QUERY
    # A polite question that contains an executable operation is still a
    # command ("Kannst du das Licht einschalten?").  The generic question
    # shape is evaluated afterwards.  "Kann man ...?" remains an
    # informational question and must never execute anything.
    elif (
        not _COPULAR_STATE_QUESTION_RE.search(normalized)
        and (
            _COMMAND_RE.search(normalized)
            or _ELLIPTICAL_COMMAND_RE.search(normalized)
            or (
                _POLITE_RE.search(normalized)
                and _DOMAIN_CUE_RE.search(normalized)
                and (
                    _NUMERIC_ASSIGNMENT_RE.search(normalized)
                    or _POSITION_ASSIGNMENT_RE.search(normalized)
                )
            )
        )
        and not _INFORMATIONAL_CAN_RE.search(normalized)
    ):
        speech_act = SpeechAct.COMMAND
    # Chat-style measurement questions commonly place the interrogative at
    # the end (``Im Erdgeschoss Temperatur wie viel?``).  A recognised
    # property plus question punctuation is structural query evidence; the
    # command branch above still wins whenever an executable operation is
    # present.
    elif _PROPERTY_CUE_RE.search(normalized) and (
        normalized.rstrip().endswith("?") or _COMPARATOR_CUE_RE.search(normalized)
    ):
        speech_act = SpeechAct.QUERY
    elif _PROPERTY_NOUN_QUERY_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    elif _QUERY_REQUEST_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    elif normalized.rstrip().endswith("?") or _QUESTION_RE.search(normalized):
        speech_act = SpeechAct.QUERY
    else:
        speech_act = SpeechAct.STATEMENT
    return SemanticUtterance(
        source_text=text,
        normalized_text=normalized,
        speech_act=speech_act,
        # Natural-shell normalization intentionally removes constructions
        # such as "ich hätte gerne".  Modality is discourse information, so
        # classify it from both original and normalized text.
        modality=_modality(f"{text} {normalized}"),
        polarity=(Polarity.NEGATIVE if _NEGATION_RE.search(normalized) else Polarity.POSITIVE),
        clauses=_clauses(normalized, speech_act),
    )
