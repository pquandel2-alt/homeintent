"""Explicit exclusions ("alle Lichter aus außer der Stehlampe und dem Nachtlicht").

Shared by the semantic command compiler and the semantic projection so both
split an exclusion list the same way: every name after "außer"/"mit Ausnahme
von" is its own exclusion, separated by "und" or commas, and a trailing
separable particle ("... außer X aus") belongs to the positive command.
"""

from __future__ import annotations

import re

_EXCLUSION_CUE_RE = re.compile(
    r"\b(?:außer|ausser|mit\s+ausnahme\s+von)\b", re.I
)
# German separable particles can follow the exclusion: ``alle Lichter außer
# Küchenlicht aus``.  The first alternative deliberately claims a recognised
# command tail before the general end-of-sentence alternative can absorb it
# into the registry name.
_EXCLUSION_RE = re.compile(
    r"\b(?:außer|ausser|mit\s+ausnahme\s+von)\s+(?P<targets>.+?)"
    r"(?:\s+(?P<tail>an|ein|aus|auf|zu|hoch|runter|herunter|hinauf|hinunter|"
    r"anmachen|ausmachen|einschalten|ausschalten|anschalten|abschalten|"
    r"öffnen|schließen)\s*[?.!]*$|\s*[?.!]*$)",
    re.I,
)
_EXCLUSION_SPLIT_RE = re.compile(r"\s*(?:,|\bund\b|\bsowie\b)\s*", re.I)
_EXCLUSION_ARTICLE_RE = re.compile(
    r"^(?:(?:dem|der|den|die|das|des|vom|alle[nrms]?|im|in\s+der|in\s+dem)\s+)+",
    re.I,
)


def split_exclusion(text: str) -> tuple[str, tuple[str, ...]]:
    """Return the positive command and independently named exclusions."""
    match = _EXCLUSION_RE.search(text)
    if match is None:
        return text, ()
    raw_targets = tuple(
        cleaned
        for part in _EXCLUSION_SPLIT_RE.split(match.group("targets"))
        if (cleaned := _EXCLUSION_ARTICLE_RE.sub("", part).strip(" ,.;:!?"))
    )
    positive = text[:match.start()].rstrip(" ,;:")
    if tail := match.group("tail"):
        positive = f"{positive} {tail}"
    return positive, raw_targets


def has_exclusion_clause(text: str) -> bool:
    """Return whether *text* explicitly introduces one or more exceptions."""
    return _EXCLUSION_CUE_RE.search(text) is not None
