"""One lexical normalization layer for natural automation language.

Everything spelling-related that the automation reader needs lives here,
so no parser carries its own alias list (spec §50, §80):

* device and property nouns with their grounded class - since 7.3.0 read
  from the one genus ontology (``device_ontology``), so every lemma,
  plural, colloquial word and compound known there works in automations
  and notifications as well;
* a few low-risk speech-to-text rejoins ("roll lade", "fünf zig");
* same-turn self repair ("60, äh 50 Prozent", "das Küchenfenster, nein das
  Bürofenster") resolved to the corrected constituent only.

Nothing here grounds an entity or decides a meaning.  Pure and typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


from .device_ontology import Gender, analyse_word, genus
from .german_morphology import GrammaticalGender, entity_name_gender


@dataclass(frozen=True)
class NounClass:
    """What a device/property noun can refer to."""

    domain: str
    device_class: str | None = None  # binary_sensor / sensor device class
    gender: GrammaticalGender = GrammaticalGender.MASCULINE
    plural: bool = False
    # A property noun names a *reading* ("Temperatur") rather than a device.
    property_noun: bool = False
    # The ontology genus this noun denotes (candidate selection uses it).
    genus: str | None = None


_GENDER = {
    Gender.MASCULINE: GrammaticalGender.MASCULINE,
    Gender.FEMININE: GrammaticalGender.FEMININE,
    Gender.NEUTER: GrammaticalGender.NEUTER,
}
# The typed class a genus contributes to automation grounding.  Candidate
# selection itself uses genus membership (``device_ontology``); this view
# only keeps the historical NounClass contract (domain, class, gender).
_PRIMARY_DOMAIN_ORDER = (
    "binary_sensor", "sensor", "cover", "light", "fan", "climate", "media_player",
    "switch", "lock", "vacuum", "lawn_mower", "valve", "humidifier",
)


def _noun_for_genus(key: str, plural: bool, word: str = "") -> NounClass:
    item = genus(key)
    lexical_gender = entity_name_gender(word) if word else None
    domains = sorted(item.domains, key=lambda domain: (
        _PRIMARY_DOMAIN_ORDER.index(domain) if domain in _PRIMARY_DOMAIN_ORDER else 99, domain
    ))
    domain = domains[0]
    device_class = None
    if item.device_classes is not None and len(item.device_classes) == 1:
        device_class = next(iter(item.device_classes))
    elif item.device_classes is not None and domain == "binary_sensor":
        device_class = sorted(item.device_classes)[0]
    if key == "garage_door":
        device_class = "garage_door"
    return NounClass(
        domain,
        device_class,
        lexical_gender or _GENDER[item.gender],
        plural=plural,
        property_noun=item.sensor and item.domains == frozenset({"sensor"}),
        genus=key,
    )


def noun_class(word: str) -> NounClass | None:
    """Exact lexical lookup of one noun through the genus ontology."""
    analysis = analyse_word(word.casefold().strip(".,!?"))
    if analysis is None or analysis.modifier is not None or analysis.universal:
        return None
    return _noun_for_genus(analysis.genera[0], analysis.plural, analysis.word)


def split_compound(word: str) -> tuple[str, NounClass] | None:
    """"Bürorollladen" -> ("büro", cover); "Haustür" -> ("haus", door).

    The prefix is returned unresolved: grounding decides whether it is an
    area or a name modifier.  ``None`` when the word is no compound of a
    known head (or is the bare head itself).
    """
    lowered = word.casefold().strip(".,!?")
    if "-" in lowered:
        head, _, tail = lowered.rpartition("-")
        entry = noun_class(tail)
        if entry is not None and head:
            return head, entry
    analysis = analyse_word(lowered)
    if analysis is None or analysis.modifier is None:
        return None
    return analysis.modifier, _noun_for_genus(
        analysis.genera[0], analysis.plural, analysis.head or ""
    )


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
