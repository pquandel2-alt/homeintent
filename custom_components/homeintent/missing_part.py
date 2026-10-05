"""One missing part of a monitoring request, asked and answered (7.9.1 A6).

"Melde dich, wenn die Temperatur im Büro um 2 Grad fällt." -> "In welchem
Zeitraum?" -> "Innerhalb von 10 Minuten." Every question that asks for
exactly one part of an understood request carries that part as a type
(``MissingPart``). The next turn of the same user in the same conversation
is read *only* as that part: a short answer ("in 10 Minuten", "eine
Stunde", "bis 9", "heute", "Anna", "im Flur") completes the original
request, which then runs the normal path again - grounding, validator,
spoken preview, explicit "Ja". "Abbrechen" ends the dialog (universal
cancel); a complete new request ends it without side effect.

The completion is deterministic text composition: the part is inserted
right after the clause conjunction ("wenn innerhalb von 10 Minuten die
Temperatur …"), or - for a device or a recipient - replaces exactly the
words the question was about. Home-Assistant-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .entities import normalize_for_compare
from .nlu.normalize import german_number

__all__ = ("MissingPart", "PartRequest", "complete_request", "read_part_answer")


class MissingPart(Enum):
    WINDOW = "window"  # "In welchem Zeitraum?"
    UNTIL = "until"  # "Bis wann soll ich prüfen …?"
    PERIOD = "period"  # "Ab wann soll ich den Verbrauch zählen …?"
    DEVICE = "device"  # "Welches Gerät meinst du?", "Welchen Melder …?"
    RECIPIENT = "recipient"  # "Wen soll ich benachrichtigen?"


_SPOKEN = {
    MissingPart.WINDOW: "Zeitraum",
    MissingPart.UNTIL: "Uhrzeit",
    MissingPart.PERIOD: "Zählbeginn",
    MissingPart.DEVICE: "Gerät",
    MissingPart.RECIPIENT: "Empfänger",
}


@dataclass(frozen=True)
class PartRequest:
    """The open question: which part, about which words, and how it was asked."""

    part: MissingPart
    question: str
    # DEVICE/RECIPIENT: the words the answer replaces ("Keller", "Lena").
    replaces: tuple[str, ...] = ()
    original_text: str = ""

    @property
    def spoken_part(self) -> str:
        return _SPOKEN[self.part]


_UNIT_SECONDS = {
    "sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60, "stunde": 3600, "stunden": 3600,
    "tag": 86400, "tage": 86400, "tagen": 86400, "woche": 604800, "wochen": 604800,
}
_DURATION_RE = re.compile(
    r"^(?:(?:innerhalb|binnen)\s+(?:von\s+)?|in\s+|im\s+zeitraum\s+von\s+|über\s+|während\s+)?"
    r"(?P<amount>\d+(?:[.,]\d+)?|[a-zäöüß]+)?\s*(?P<unit>sekunden?|minuten?|stunden?|tagen?|tage|tag|wochen?)$"
)
_HALF_RE = re.compile(
    r"^(?:(?:innerhalb|binnen)\s+(?:von\s+)?|in\s+)?(?:(?P<whole>anderthalb|eineinhalb)|(?:einer?\s+)?halben?)"
    r"\s+stunden?$"
)
_CLOCK_RE = re.compile(
    r"^(?:bis\s+)?(?:(?P<day>heute|morgen)\s+)?(?:(?:früh|abend|abends|morgens)\s+)?"
    r"(?:(?:um|gegen|spätestens|spätestens\s+um)\s+)?(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?"
    r"(?:\s*uhr)?(?:\s+(?P<part>abends|morgens|früh|nachmittags))?$"
)
_PERIODS = {
    "heute": "heute", "täglich": "heute", "seit mitternacht": "heute", "ab heute": "heute",
    "diese woche": "diese Woche", "wöchentlich": "diese Woche", "die woche": "diese Woche",
    "diesen monat": "diesen Monat", "monatlich": "diesen Monat", "den monat": "diesen Monat",
}
_LEADING = re.compile(r"^(?:(?:ja|also|ähm|äh|ok|okay|naja)\s*,?\s+)+", re.IGNORECASE)
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des"})


def _clean(answer: str) -> str:
    text = " ".join(answer.strip().rstrip(".!?").split())
    return _LEADING.sub("", text).strip()


def _amount(word: str | None) -> float | None:
    if word is None:
        return 1.0
    key = word.replace(",", ".")
    try:
        return float(key)
    except ValueError:
        pass
    if key in {"ein", "eine", "einer", "einen", "einem"}:
        return 1.0
    if key in {"halbe", "halben"}:
        return 0.5
    value = german_number(key)
    return float(value) if value is not None else None


def read_part_answer(part: MissingPart, answer: str) -> str | None:
    """The answer as a phrase for exactly ``part``, or ``None``."""
    text = _clean(answer)
    key = text.casefold()
    if not key:
        return None
    if part is MissingPart.WINDOW:
        half = _HALF_RE.match(key)
        if half is not None:
            # "eine halbe Stunde", "anderthalb Stunden": said in minutes.
            minutes = 30 if half.group("whole") is None else 90
            return f"innerhalb von {minutes} Minuten"
        match = _DURATION_RE.match(key)
        if match is None:
            return None
        amount = _amount(match.group("amount"))
        if amount is None or amount <= 0:
            return None
        unit = match.group("unit")
        seconds = amount * _UNIT_SECONDS[unit]
        if seconds < 60:
            return None
        number = f"{amount:g}".replace(".", ",")
        return f"innerhalb von {number} {unit}"
    if part is MissingPart.UNTIL:
        match = _CLOCK_RE.match(key)
        if match is None:
            return None
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        if match.group("part") in {"abends", "nachmittags"} and hour < 12:
            hour += 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        day = f"{match.group('day')} um " if match.group("day") else ""
        return f"bis {day}{hour} Uhr" if minute == 0 else f"bis {day}{hour}:{minute:02d} Uhr"
    if part is MissingPart.PERIOD:
        return _PERIODS.get(key)
    # DEVICE / RECIPIENT: a short naming answer - never a sentence.
    words = text.split()
    if not words or len(words) > 5 or any(normalize_for_compare(word) in {"wenn", "dann", "und"} for word in words):
        return None
    if part is MissingPart.RECIPIENT:
        words = [word for word in words if word.casefold() not in {"an", "für", "fuer"}]
    return " ".join(words) or None


_CONJUNCTION_RE = re.compile(r"\b(wenn|sobald|falls|sofern)\b\s+", re.IGNORECASE)


def complete_request(request: PartRequest, phrase: str) -> str:
    """The original request with the answered part, as one sentence."""
    original = request.original_text.strip()
    if request.part in {MissingPart.WINDOW, MissingPart.UNTIL, MissingPart.PERIOD}:
        match = _CONJUNCTION_RE.search(original)
        if match is None:
            return f"{original.rstrip('.!?')} {phrase}."
        return f"{original[:match.end()]}{phrase} {original[match.end():]}"
    replaced = _replace_words(original, request.replaces, request.part, phrase)
    return replaced if replaced is not None else original


def _replace_words(original: str, words: tuple[str, ...], part: MissingPart, phrase: str) -> str | None:
    if not words:
        return None
    tokens = original.split()
    keys = [normalize_for_compare(token.strip(",.;:!?„“\"")) for token in tokens]
    wanted = [normalize_for_compare(word) for word in words if normalize_for_compare(word) not in _ARTICLES]
    if not wanted:
        return None
    for start in range(len(keys)):
        if keys[start] != wanted[0]:
            continue
        end = start
        matched = 1
        while matched < len(wanted) and end + 1 < len(keys):
            end += 1
            if keys[end] == wanted[matched]:
                matched += 1
            elif keys[end] not in _ARTICLES:
                break
        if matched < len(wanted):
            continue
        trailing = tokens[end][len(tokens[end].rstrip(",.;:!?")):]
        new = phrase
        if part is MissingPart.DEVICE and start > 0 and keys[start - 1] in _ARTICLES:
            # "das Kellerlicht" -> "die Stehlampe": the answer brings its own article.
            if normalize_for_compare(phrase.split()[0]) in _ARTICLES:
                start -= 1
        return " ".join([*tokens[:start], new + trailing, *tokens[end + 1:]])
    return None
