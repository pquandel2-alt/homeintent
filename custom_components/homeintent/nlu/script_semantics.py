"""Compositional, name-bound semantics for colloquial script requests."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import cast

from ..entities import EntitySnapshot, normalize_for_compare
from .frame import SemanticFrame, TargetReference
from .parser import ClarificationRequest, ParseResult
from .primitives import SemanticAction


_WORD_RE = re.compile(r"[\wäöüß]+", re.I)
_FILLERS = frozenset({"bitte", "mal", "doch", "gerade", "noch", "jetzt"})
_ARTICLES_ONE = frozenset({"ein", "eine", "einen", "einem", "einer", "eines", "eins"})
_DEFAULT_PREFIXES = (
    ("starte",),
    ("start",),
    ("aktiviere",),
    ("aktivier",),
    ("zapf",),
    ("zapfe",),
    ("mach",),
    ("mache",),
    ("gib",),
    ("ich", "moechte"),
    ("ich", "will"),
    ("ich", "haette", "gern"),
    ("ich", "haette", "gerne"),
)
_DEFAULT_SUFFIXES = (("starten",), ("ausfuehren",), ("zapfen",))
_PROTECTED_CUSTOM_WORDS = frozenset(
    {
        "nicht",
        "nein",
        "ja",
        "bestaetigen",
        "abbrechen",
        "stopp",
        "stop",
        "oeffnen",
        "schliessen",
        "entriegeln",
        "verriegeln",
        "entschaerfen",
        "scharf",
        "alarm",
        "aus",
        "an",
        "ein",
        "auf",
        "zu",
        "loeschen",
        "starten",
        "aktivieren",
        "ausfuehren",
    }
)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        normalize_for_compare(word)
        for word in _WORD_RE.findall(text)
    )


def validate_script_verbs(value: object) -> tuple[tuple[str, ...], ...]:
    """Validate bounded literal verb phrases; never accept regex syntax."""

    if value in (None, "", (), []):
        return ()
    if isinstance(value, str):
        raw_items: Iterable[object] = value.split(",")
    elif isinstance(value, Sequence):
        raw_items = cast(Sequence[object], value)
    else:
        raise ValueError("script_verbs muss eine Liste kurzer Verbphrasen sein")
    phrases: list[tuple[str, ...]] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            raise ValueError("script_verbs darf nur Text enthalten")
        words = _tokens(raw.strip())
        if not 1 <= len(words) <= 3 or any(len(word) > 24 for word in words):
            raise ValueError("Skriptverben dürfen aus ein bis drei kurzen Wörtern bestehen")
        if " ".join(words) != normalize_for_compare(raw.strip()):
            raise ValueError("Skriptverben dürfen keine Regex- oder Wildcard-Zeichen enthalten")
        if set(words) & _PROTECTED_CUSTOM_WORDS:
            raise ValueError("Skriptverb kollidiert mit Sicherheits- oder Aktionssprache")
        if words not in phrases:
            phrases.append(words)
        if len(phrases) > 12:
            raise ValueError("Höchstens zwölf eigene Skriptverben sind erlaubt")
    return tuple(phrases)


def _strip_fillers(words: tuple[str, ...]) -> tuple[str, ...]:
    start = 0
    end = len(words)
    while start < end and words[start] in _FILLERS:
        start += 1
    while end > start and words[end - 1] in _FILLERS:
        end -= 1
    return words[start:end]


def _target_words(
    text: str,
    custom_verbs: tuple[tuple[str, ...], ...],
) -> tuple[str, ...] | None:
    words = _strip_fillers(_tokens(text))
    if not words:
        return None

    # "Führe <Name> aus" has a bound separable particle. Neither half by
    # itself is accepted, so a target word "aus" cannot be silently lost.
    if words[0] in {"fuehre", "fuehr"} and words[-1:] == ("aus",):
        return _strip_fillers(words[1:-1]) or None

    for prefix in (*_DEFAULT_PREFIXES, *custom_verbs):
        if words[: len(prefix)] != prefix:
            continue
        remainder = _strip_fillers(words[len(prefix) :])
        if remainder[:1] == ("mir",):
            remainder = _strip_fillers(remainder[1:])
        return remainder or None

    for suffix in _DEFAULT_SUFFIXES:
        if words[-len(suffix) :] == suffix:
            return _strip_fillers(words[: -len(suffix)]) or None
    return None


def _quantity_key(words: tuple[str, ...]) -> tuple[str, ...]:
    if not words:
        return words
    first = words[0]
    if first in _ARTICLES_ONE:
        first = "1"
    elif first == "zwei":
        first = "2"
    return (first, *words[1:])


def compile_script_request(
    text: str,
    entities: Sequence[EntitySnapshot],
    *,
    custom_verbs: tuple[tuple[str, ...], ...] = (),
) -> ParseResult | ClarificationRequest | None:
    """Compile only a wrapper plus one exact exposed script name/alias."""

    target_words = _target_words(text, custom_verbs)
    if target_words is None:
        return None
    target_key = _quantity_key(target_words)
    matches: list[EntitySnapshot] = []
    for entity in entities:
        if entity.domain != "script":
            continue
        if any(
            _quantity_key(_tokens(name)) == target_key
            for name in (entity.friendly_name, *entity.aliases)
        ):
            matches.append(entity)
    if not matches:
        return None
    if len(matches) > 1:
        return ClarificationRequest("HassRunScript", "script", tuple(matches))
    entity = matches[0]
    return ParseResult(
        frame=SemanticFrame(
            intent="HassRunScript",
            target=TargetReference(
                entity.friendly_name, entity.entity_id, entity.domain
            ),
            area=None,
            source_text=text,
            action=SemanticAction.START,
        ),
        resolved_entities=[entity],
    )
