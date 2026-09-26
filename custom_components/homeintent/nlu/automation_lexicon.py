"""One lexical normalization layer for natural automation language.

Everything spelling-related that the 7.2.0 automation reader needs lives
here, so no parser carries its own alias list (spec §50, §80):

* device and property nouns with their grounded class
  (``Rolllade``/``Rollade``/``Rollladen``/``Rolladen``/``Rollo``/``Jalousie``
  all mean a cover; ``Temperatur`` a temperature sensor, ...);
* a few low-risk speech-to-text rejoins ("roll lade", "fünf zig");
* same-turn self repair ("60, äh 50 Prozent", "das Küchenfenster, nein das
  Bürofenster") resolved to the corrected constituent only.

Nothing here grounds an entity or decides a meaning.  Pure and typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from .german_morphology import GrammaticalGender


@dataclass(frozen=True)
class NounClass:
    """What a device/property noun can refer to."""

    domain: str
    device_class: str | None = None  # binary_sensor / sensor device class
    gender: GrammaticalGender = GrammaticalGender.MASCULINE
    plural: bool = False
    # A property noun names a *reading* ("Temperatur") rather than a device.
    property_noun: bool = False


_M, _F, _N = GrammaticalGender.MASCULINE, GrammaticalGender.FEMININE, GrammaticalGender.NEUTER

_NOUNS: Mapping[str, NounClass] = {
    # covers - every spelling users and STT produce
    "rollladen": NounClass("cover", None, _M),
    "rolladen": NounClass("cover", None, _M),
    "rolllade": NounClass("cover", None, _F),
    "rollade": NounClass("cover", None, _F),
    "rollo": NounClass("cover", None, _N),
    "rollos": NounClass("cover", None, _N, plural=True),
    "rollläden": NounClass("cover", None, _M, plural=True),
    "rolläden": NounClass("cover", None, _M, plural=True),
    "jalousie": NounClass("cover", None, _F),
    "jalousien": NounClass("cover", None, _F, plural=True),
    # openings
    "fenster": NounClass("binary_sensor", "window", _N),
    "tür": NounClass("binary_sensor", "door", _F),
    "türe": NounClass("binary_sensor", "door", _F),
    "türen": NounClass("binary_sensor", "door", _F, plural=True),
    "garagentor": NounClass("binary_sensor", "garage_door", _N),
    "bewegungsmelder": NounClass("binary_sensor", "motion", _M),
    "bewegungssensor": NounClass("binary_sensor", "motion", _M),
    # actuators
    "licht": NounClass("light", None, _N),
    "lichter": NounClass("light", None, _N, plural=True),
    "lampe": NounClass("light", None, _F),
    "lampen": NounClass("light", None, _F, plural=True),
    "leuchte": NounClass("light", None, _F),
    "ventilator": NounClass("fan", None, _M),
    "lüfter": NounClass("fan", None, _M),
    "heizung": NounClass("climate", None, _F),
    "thermostat": NounClass("climate", None, _N),
    "lautsprecher": NounClass("media_player", None, _M),
    "fernseher": NounClass("media_player", None, _M),
    "steckdose": NounClass("switch", None, _F),
    "kaffeemaschine": NounClass("switch", None, _F),
    # readings
    "temperatur": NounClass("sensor", "temperature", _F, property_noun=True),
    "luftfeuchtigkeit": NounClass("sensor", "humidity", _F, property_noun=True),
    "feuchtigkeit": NounClass("sensor", "humidity", _F, property_noun=True),
    "luftfeuchte": NounClass("sensor", "humidity", _F, property_noun=True),
    "akku": NounClass("sensor", "battery", _M, property_noun=True),
    "akkustand": NounClass("sensor", "battery", _M, property_noun=True),
    "batterie": NounClass("sensor", "battery", _F, property_noun=True),
    "ladestand": NounClass("sensor", "battery", _M, property_noun=True),
}
# Longest first, so "bewegungsmelder" wins over a shorter suffix.
_SUFFIXES = tuple(sorted(_NOUNS, key=len, reverse=True))


def noun_class(word: str) -> NounClass | None:
    """Exact lexical lookup of one (case-insensitive) noun."""
    return _NOUNS.get(word.casefold().strip(".,!?"))


def split_compound(word: str) -> tuple[str, NounClass] | None:
    """"Bürorollladen" -> ("büro", cover); "Haustür" -> ("haus", door).

    The prefix is returned unresolved: grounding decides whether it is an
    area or a name modifier.  ``None`` when the word is no compound of a
    known head (or is the bare head itself).
    """
    lowered = word.casefold().strip(".,!?")
    if "-" in lowered:
        head, _, tail = lowered.rpartition("-")
        entry = _NOUNS.get(tail)
        if entry is not None and head:
            return head, entry
    for suffix in _SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 3:
            prefix = lowered[: -len(suffix)]
            return prefix, _NOUNS[suffix]
    return None


# --- speech-to-text rejoins ---------------------------------------------------

_STT_REJOINS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\broll[\s-]+lade\b", re.IGNORECASE), "Rolllade"),
    (re.compile(r"\broll[\s-]+laden\b", re.IGNORECASE), "Rollladen"),
    (
        re.compile(
            r"\b(zwan|drei(?:ß|ss)|vier|fünf|fuenf|sech|sieb|acht|neun)\s+zig\b",
            re.IGNORECASE,
        ),
        r"\1zig",
    ),
    (re.compile(r"\bpush\s+nachricht\b", re.IGNORECASE), "Push-Nachricht"),
    (re.compile(r"\bpushnachricht\b", re.IGNORECASE), "Push-Nachricht"),
    (re.compile(r"\bhaus\s+tür\b", re.IGNORECASE), "Haustür"),
    (re.compile(r"\bwenn's\b", re.IGNORECASE), "wenn es"),
    # harmless spelling variants of "at home"
    (re.compile(r"\bzu\s+hause\b", re.IGNORECASE), "zuhause"),
    (re.compile(r"\bdaheim\b", re.IGNORECASE), "zuhause"),
    (re.compile(r"\bkeiner\s+(?=(?:mehr\s+)?zuhause\b)", re.IGNORECASE), "niemand "),
)


def rejoin_stt(text: str) -> str:
    for pattern, replacement in _STT_REJOINS:
        text = pattern.sub(replacement, text)
    return text


# --- same-turn self repair ------------------------------------------------------

_REPAIR_MARKER = (
    r"(?:\s*(?:\.{2,}|…)\s*|,\s*|\s+)(?:(?:äh+m?|ähm|öhm|ehm)\s*,?\s*)?"
    r"(?:nein|quatsch|sorry|pardon|korrektur|ich\s+meine|äh+m?|ähm)"
    r"(?:\s*,?\s*(?:ich\s+meine|nein))?\s*,?\s+"
)
_NUMBER = r"(?:-?\d+(?:[.,]\d+)?|[a-zäöüß]+zig|zehn|zwanzig|dreißig|hundert)"
_COMPARATOR = r"(?:über|unter|mindestens|höchstens|mehr\s+als|weniger\s+als)"
_UNIT = r"(?:\s*(?:%|prozent|grad))?"

# "60, äh 50 Prozent" / "über 26, nein über 24 Grad"
_NUMBER_REPAIR_RE = re.compile(
    rf"(?:{_COMPARATOR}\s+)?{_NUMBER}{_UNIT}{_REPAIR_MARKER}(?=(?:{_COMPARATOR}\s+)?{_NUMBER}\b)",
    re.IGNORECASE,
)
_ARTICLE = r"(?:der|die|das|den|dem|des|mein(?:en|em|e)?)"
# "das Küchenfenster, nein das Bürofenster"
_NOUN_PHRASE_REPAIR_RE = re.compile(
    rf"\b{_ARTICLE}\s+[\wäöüß-]+{_REPAIR_MARKER}(?={_ARTICLE}\s+[\wäöüß-]+)",
    re.IGNORECASE,
)
# "in der Küche, nein im Wohnzimmer"
_LOCATION = r"(?:im|in\s+der|in\s+dem|in|am|auf\s+der)"
_LOCATION_REPAIR_RE = re.compile(
    rf"\b{_LOCATION}\s+[\wäöüß-]+{_REPAIR_MARKER}(?={_LOCATION}\s+[\wäöüß-]+)",
    re.IGNORECASE,
)
# "schick Julia, nein mir" - the replaced recipient
_RECIPIENT_REPAIR_RE = re.compile(
    rf"\b(?P<verb>schick\w*|send\w*|sag\w*|gib|benachrichtig\w*|informier\w*)\s+"
    rf"[a-zäöüß][\wäöüß-]*{_REPAIR_MARKER}(?=(?:mir|mich|uns|[A-ZÄÖÜ][\wäöüß-]*)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RepairResult:
    text: str
    repaired: bool


def resolve_repairs(text: str) -> RepairResult:
    """Keep only the corrected constituent of a same-kind self repair.

    Only a constituent immediately followed by a repair marker *and* a new
    constituent of the same kind is dropped; anything else is untouched.
    """
    repaired = text
    repaired = _NUMBER_REPAIR_RE.sub("", repaired)
    repaired = _NOUN_PHRASE_REPAIR_RE.sub("", repaired)
    repaired = _LOCATION_REPAIR_RE.sub("", repaired)
    repaired = _RECIPIENT_REPAIR_RE.sub(lambda match: f"{match.group('verb')} ", repaired)
    repaired = re.sub(r"\s+", " ", repaired).strip()
    return RepairResult(repaired, repaired != re.sub(r"\s+", " ", text).strip())


__all__ = (
    "NounClass",
    "RepairResult",
    "noun_class",
    "rejoin_stt",
    "resolve_repairs",
    "split_compound",
)
