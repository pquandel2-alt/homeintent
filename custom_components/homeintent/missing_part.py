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
    # 7.9.2 A2: "Wie lange soll die Bewässerung laufen?" - an automation
    # that opens an irrigation valve needs its end.
    DURATION = "duration"
    # 7.9.2 A6: "Um wie viel und in welchem Zeitraum?" ("schnell fällt").
    RATE = "rate"
    # 7.9.2 A6: "Ab welchem Wert?" ("wenn die Sonne scheint").
    THRESHOLD = "threshold"


_SPOKEN = {
    MissingPart.WINDOW: "Zeitraum",
    MissingPart.UNTIL: "Uhrzeit",
    MissingPart.PERIOD: "Zählbeginn",
    MissingPart.DEVICE: "Gerät",
    MissingPart.RECIPIENT: "Empfänger",
    MissingPart.DURATION: "Dauer",
    MissingPart.RATE: "Betrag und Zeitraum",
    MissingPart.THRESHOLD: "Schwellwert",
}


@dataclass(frozen=True)
class PartRequest:
    """The open question: which part, about which words, and how it was asked."""

    part: MissingPart
    question: str
    # DEVICE/RECIPIENT: the words the answer replaces ("Keller", "Lena").
    replaces: tuple[str, ...] = ()
    original_text: str = ""
    # DEVICE: the offered devices; an answer naming exactly one of them
    # ("den im Vorgarten", "Vorgarten") stands for its full name (7.9.2).
    choices: tuple[str, ...] = ()

    @property
    def spoken_part(self) -> str:
        return _SPOKEN[self.part]


_UNIT_SECONDS = {
    "sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60, "stunde": 3600, "stunden": 3600,
    "tag": 86400, "tage": 86400, "tagen": 86400, "woche": 604800, "wochen": 604800,
}
# Closed word classes of a period answer: prepositions before the amount.
_PERIOD_LEAD = frozenset({"innerhalb", "binnen", "von", "in", "im", "zeitraum", "über", "während"})
_ONE = frozenset({"ein", "eine", "einer", "einen", "einem"})
_HALF = frozenset({"halbe", "halben"})
_ONE_AND_HALF = frozenset({"anderthalb", "eineinhalb"})
# Closed word classes of a clock answer.
_CLOCK_LEAD = frozenset({"bis", "um", "gegen", "spätestens"})
_DAYS = frozenset({"heute", "morgen"})
_EVENING = frozenset({"abends", "nachmittags", "abend"})
_MORNING = frozenset({"morgens", "früh"})
_PERIODS = {
    "heute": "heute", "täglich": "heute", "seit mitternacht": "heute", "ab heute": "heute",
    "diese woche": "diese Woche", "wöchentlich": "diese Woche", "die woche": "diese Woche",
    "diesen monat": "diesen Monat", "monatlich": "diesen Monat", "den monat": "diesen Monat",
}
_FILLER_WORDS = frozenset({"ja", "also", "ähm", "äh", "ok", "okay", "naja"})
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des"})


def _clean(answer: str) -> str:
    words = answer.strip().rstrip(".!?").replace(",", " ").split()
    while words and words[0].casefold() in _FILLER_WORDS:
        words = words[1:]
    return " ".join(words)


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
        seconds = _period_seconds(key.split())
        if seconds is None or seconds < 60:
            return None
        return f"innerhalb von {_spoken_duration(seconds)}"
    if part is MissingPart.UNTIL:
        clock = _clock(key.split())
        if clock is None:
            return None
        day, hour, minute = clock
        lead = f"{day} um " if day else ""
        return f"bis {lead}{hour} Uhr" if minute == 0 else f"bis {lead}{hour}:{minute:02d} Uhr"
    if part is MissingPart.PERIOD:
        return _PERIODS.get(key)
    if part is MissingPart.DURATION:
        seconds = _period_seconds([word for word in key.split() if word not in {"für", "fuer", "lang", "etwa", "ungefähr"}])
        if seconds is None or seconds < 60 or seconds > 86400:
            return None
        return f"für {_spoken_duration(seconds)}"
    # DEVICE / RECIPIENT: a short naming answer - never a sentence.
    words = text.split()
    if not words or len(words) > 5 or any(normalize_for_compare(word) in {"wenn", "dann", "und"} for word in words):
        return None
    if part is MissingPart.RECIPIENT:
        words = [word for word in words if word.casefold() not in {"an", "für", "fuer"}]
    return " ".join(words) or None


def _period_seconds(words: list[str]) -> float | None:
    """"innerhalb von 10 Minuten", "eine halbe Stunde", "zwei Tage"."""
    while words and words[0] in _PERIOD_LEAD:
        words = words[1:]
    if not words or words[-1] not in _UNIT_SECONDS:
        return None
    unit = _UNIT_SECONDS[words[-1]]
    amount_words = words[:-1]
    if not amount_words:
        return float(unit)
    if amount_words[-1] in _HALF:
        return 0.5 * unit if amount_words[:-1] in ([], ["eine"], ["einer"]) else None
    if len(amount_words) != 1:
        return None
    word = amount_words[0]
    if word in _ONE_AND_HALF:
        return 1.5 * unit
    amount = _amount(word)
    return amount * unit if amount is not None and amount > 0 else None


def _spoken_duration(seconds: float) -> str:
    for size, singular, plural in (
        (604800, "Woche", "Wochen"), (86400, "Tag", "Tage"), (3600, "Stunde", "Stunden"),
        (60, "Minute", "Minuten"),
    ):
        if seconds % size == 0:
            count = int(seconds // size)
            return f"{count} {singular if count == 1 else plural}"
    return f"{int(seconds // 60)} Minuten"


def _clock(words: list[str]) -> tuple[str | None, int, int] | None:
    """"bis 20 Uhr", "um 9 abends", "morgen 7 Uhr", "19:30" -> (day, hour, minute)."""
    day: str | None = None
    evening = False
    rest: list[str] = []
    for word in words:
        if word in _CLOCK_LEAD or word in _MORNING:
            continue
        if word in _DAYS and day is None:
            day = word
        elif word in _EVENING:
            evening = True
        elif word != "uhr":
            rest.append(word)
    if len(rest) != 1:
        return None
    hour_text, _, minute_text = rest[0].replace(".", ":").partition(":")
    if not hour_text.isdigit() or (minute_text and not minute_text.isdigit()):
        return None
    hour, minute = int(hour_text), int(minute_text or 0)
    if evening and hour < 12:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return day, hour, minute


_CONJUNCTION_RE = re.compile(r"\b(wenn|sobald|falls|sofern)\b\s+", re.IGNORECASE)


def complete_request(request: PartRequest, phrase: str) -> str:
    """The original request with the answered part, as one sentence."""
    original = request.original_text.strip()
    if request.part is MissingPart.DURATION:
        # The duration belongs to the action: before a trailing condition
        # ("…, wenn …"), else at the end of the sentence.
        match = re.search(r",?\s+\b(wenn|sobald|falls|sofern)\b", original, re.IGNORECASE)
        if match is not None and match.start() > 0:
            return f"{original[:match.start()]} {phrase}{original[match.start():]}"
        return f"{original.rstrip('.!?')} {phrase}."
    if request.part in {MissingPart.WINDOW, MissingPart.UNTIL, MissingPart.PERIOD}:
        match = _CONJUNCTION_RE.search(original)
        if match is None:
            return f"{original.rstrip('.!?')} {phrase}."
        return f"{original[:match.end()]}{phrase} {original[match.end():]}"
    if request.choices:
        phrase = _choice(request.choices, phrase) or phrase
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


def _choice(choices: tuple[str, ...], phrase: str) -> str | None:
    """The one offered name every content word of the answer occurs in."""
    words = [
        normalize_for_compare(word) for word in phrase.split()
        if normalize_for_compare(word) not in _ARTICLES | {"im", "in", "am", "beim", "vom", "von"}
    ]
    if not words:
        return None
    hits = [
        name for name in choices
        if all(any(word in part for part in normalize_for_compare(name).split()) for word in words)
    ]
    return hits[0] if len(hits) == 1 else None
