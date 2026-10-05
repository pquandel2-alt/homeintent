"""Language island "Überwachungen verwalten" (7.9 W8): frames, not sentences.

"Welche Überwachungen laufen?", "Was überwachst du gerade?", "Stopp die
Fensterüberwachung.", "Pausiere die Garagen-Meldung bis morgen um 7 Uhr.",
"Lösche die Meldung für das Garagentor."

A request is one of four kinds, recognized from closed word classes:

* the object is a *monitoring noun* - a noun whose head is
  Überwachung/Meldung/Warnung/Benachrichtigung/Erinnerung (also as compound
  head: "Fensterüberwachung", "Garagen-Meldung") - or the verb
  "überwachen/beobachten" itself;
* the operation comes from the verb: listing (interrogative), stopping
  (stoppen, beenden, ausschalten, deaktivieren), deleting (löschen,
  entfernen), pausing (pausieren, aussetzen) with a "bis" time.

The compound's modifier or a "für/vom X" phrase names what is watched;
resolving it to monitors stays with the caller.  Home-Assistant-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from .entities import normalize_for_compare


class MonitoringOperation(Enum):
    LIST = auto()
    STOP = auto()
    DELETE = auto()
    PAUSE = auto()


@dataclass(frozen=True)
class MonitoringRequest:
    operation: MonitoringOperation
    subject: str | None = None  # "fenster", "garage", "garagentor" (normalized)
    spoken_subject: str | None = None  # as said: "Garagentor", "Haustür" (7.9.1 A5)
    day_offset: int | None = None  # PAUSE: 0 today, 1 tomorrow
    hour: int | None = None
    minute: int = 0


_HEADS = ("ueberwachung", "meldung", "warnung", "benachrichtigung", "erinnerung", "beobachtung")
_WATCH_VERBS = frozenset({"ueberwachst", "beobachtest", "ueberwacht", "beobachtet", "ueberwachen", "beobachten"})
_LIST_WORDS = frozenset({"welche", "was", "zeig", "zeige", "liste", "nenne", "sag", "gibt"})
_STOP_WORDS = frozenset({
    "stopp", "stoppe", "stop", "beende", "beenden", "deaktiviere", "deaktivieren", "hoer", "hoere",
    "schalte", "schalt", "stell", "stelle",
})
_DELETE_WORDS = frozenset({
    "loesche", "loeschen", "loesch", "entferne", "entfernen", "entfern", "vergiss",
})
_PAUSE_WORDS = frozenset({
    "pausiere", "pausieren", "pausier", "setze", "setz", "unterbrich", "unterbreche",
})
# Infinitives and short imperatives of the stop verbs; a modal frame
# ("Kannst du die Garagen-Meldung ausschalten?") puts them at the end.
_STOP_INFINITIVES = frozenset({
    "stoppen", "beenden", "deaktivieren", "deaktivier", "ausschalten", "abschalten", "abstellen",
    "beend",
})
# Lead-ins before the verb: politeness, modal frames.
_LEAD_INS = frozenset({
    "bitte", "kannst", "koenntest", "wuerdest", "magst", "du", "mal", "jetzt", "dann", "und", "ok", "okay",
})
_FILLERS = frozenset({
    "die", "der", "das", "den", "dem", "meine", "meinen", "deine", "bitte", "mal", "fuer", "vom",
    "von", "zum", "zur", "mit", "auf", "aus", "ab", "aufs", "fuers", "alle", "gerade", "jetzt", "im", "in", "am",
    "laufen", "laeuft", "aktiv", "aktiven", "sind", "gibt", "es", "du", "hast", "eingerichtet",
    "des", "zu", "an", "bei", "beim", "ueber", "betreffend", "wegen",
    "ueberwachungen", "meldungen", "warnungen", "benachrichtigungen", "erinnerungen",
})
_CLOCK_RE = re.compile(r"\bbis\s+(?:(?P<day>heute|morgen)\s*)?(?:(?:um|gegen)\s+)?(?P<hour>\d{1,2})?(?::(?P<minute>\d{2}))?\s*(?:uhr)?", re.IGNORECASE)


def _monitoring_noun(key: str) -> tuple[bool, str | None]:
    """(is a monitoring noun, its modifier) - "fensterueberwachung" -> fenster."""
    for head in _HEADS:
        if key == head or key == f"{head}en" or key == f"{head}s":
            return True, None
        if key.endswith(head) and len(key) > len(head) + 2:
            return True, key[: -len(head)].rstrip("-s")
    return False, None


def parse_monitoring_management(text: str) -> MonitoringRequest | None:
    raw = re.sub(r"[?.!,]", " ", text)
    keys = [normalize_for_compare(word).replace("-", "") for word in raw.split()]
    if not keys:
        return None
    nouns = [(index, _monitoring_noun(key)) for index, key in enumerate(keys)]
    found = [(index, modifier) for index, (hit, modifier) in nouns if hit]
    watches = any(key in _WATCH_VERBS for key in keys)
    if not found and not watches:
        return None
    lead = 0
    while lead < len(keys) - 1 and keys[lead] in _LEAD_INS:
        lead += 1
    first = keys[lead]
    if first not in _LIST_WORDS and found:
        # The verb may close a modal frame: "Kannst du die Überwachung vom
        # Garagentor löschen?" - same object forms for every verb (7.9.1 A5).
        trailing = next(
            (
                key for key in reversed(keys)
                if key in _DELETE_WORDS | _PAUSE_WORDS | _STOP_WORDS | _STOP_INFINITIVES
            ),
            None,
        )
        if first not in _DELETE_WORDS | _PAUSE_WORDS | _STOP_WORDS and trailing is not None:
            first = trailing
    modifier = found[0][1] if found else None
    subject = modifier or _subject_phrase(keys, found[0][0] if found else None)
    spoken = _spoken_subject(raw.split(), keys, found[0][0] if found else None, modifier)
    if first in _LIST_WORDS or (watches and first in {"was", "welche", "wen", "wo"}):
        if any(key in _STOP_WORDS | _DELETE_WORDS | _PAUSE_WORDS for key in keys[1:]):
            return None
        return MonitoringRequest(MonitoringOperation.LIST, subject, spoken)
    if not found:
        return None
    if first in _DELETE_WORDS:
        return MonitoringRequest(MonitoringOperation.DELETE, subject, spoken)
    if first in _PAUSE_WORDS or (first in {"setze", "setz"} and "aus" in keys):
        clock = _CLOCK_RE.search(text)
        if clock is None:
            return MonitoringRequest(MonitoringOperation.PAUSE, subject, spoken)
        day = (clock.group("day") or "").casefold()
        hour = int(clock.group("hour")) if clock.group("hour") else None
        return MonitoringRequest(
            MonitoringOperation.PAUSE, subject, spoken,
            day_offset=1 if day == "morgen" else 0 if day == "heute" or hour is not None else None,
            hour=hour, minute=int(clock.group("minute") or 0),
        )
    if first in _STOP_WORDS | _STOP_INFINITIVES:
        if first in {"schalte", "schalt", "stell", "stelle"} and "aus" not in keys and "ab" not in keys:
            return None
        return MonitoringRequest(MonitoringOperation.STOP, subject, spoken)
    return None


def _spoken_subject(
    words: list[str], keys: list[str], noun_index: int | None, modifier: str | None
) -> str | None:
    """The watched thing as the speaker said it, for the answer."""
    if noun_index is None:
        return None
    if modifier:
        word = words[noun_index]
        head_length = len(keys[noun_index]) - len(modifier)
        return word[: max(1, len(word) - head_length)].rstrip("-") if head_length > 0 else word
    rest = [
        word for word, key in zip(words[noun_index + 1:], keys[noun_index + 1:])
        if key not in _FILLERS and not key.isdigit()
        and key not in {"bis", "morgen", "heute", "uhr", "um"}
        and key not in _DELETE_WORDS | _PAUSE_WORDS | _STOP_WORDS | _STOP_INFINITIVES
    ]
    return " ".join(rest) or None


def _subject_phrase(keys: list[str], noun_index: int | None) -> str | None:
    """"die Meldung für das Garagentor" -> "garagentor"."""
    if noun_index is None:
        return None
    rest = [key for key in keys[noun_index + 1:] if key not in _FILLERS and not key.isdigit()]
    rest = [key for key in rest if key not in {"bis", "morgen", "heute", "uhr", "um"}]
    rest = [key for key in rest if key not in _DELETE_WORDS | _PAUSE_WORDS | _STOP_WORDS | _STOP_INFINITIVES]
    return " ".join(rest) or None


# Objects of a management verb that are never a calendar entry, a list item
# or a reminder (7.9.1 A5): "Lösch die Überwachung vom Garagentor".
_MANAGED_OBJECT_HEADS = ("ueberwachung", "meldung", "warnung", "benachrichtigung", "automation")
_MANAGEMENT_VERBS = (
    _DELETE_WORDS | _PAUSE_WORDS | _STOP_WORDS | _STOP_INFINITIVES
    | frozenset({"aendere", "aendern", "aender", "bearbeite", "bearbeiten", "aktiviere", "aktivieren",
                 "aktivier", "verschiebe", "verschieben", "schalte", "schalt"})
)


def names_managed_object(text: str) -> bool:
    """A management verb with a monitoring/automation noun as object."""
    raw = re.sub(r"[?.!,]", " ", text)
    keys = [normalize_for_compare(word).replace("-", "") for word in raw.split()]
    if not any(key in _MANAGEMENT_VERBS for key in keys):
        return False
    return any(
        key.endswith(head) or key.endswith(head + "en") or key.endswith(head + "s")
        for key in keys for head in _MANAGED_OBJECT_HEADS
    )


__all__ = (
    "MonitoringOperation",
    "MonitoringRequest",
    "names_managed_object",
    "parse_monitoring_management",
)
