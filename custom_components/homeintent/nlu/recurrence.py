"""Once or recurring? Never guessed (7.3.3, finding Q5).

* A clock time or event without a repetition marker is a one-time command.
* With a marker ("jeden", "immer", "täglich", "werktags" …) it is a
  recurring automation (preview first, as before).
* Events that typically recur (sunset/sunrise, darkness) without any marker
  are asked about: "Nur heute oder jeden Tag?"

Only meaning is decided here; the automation model and its confirmation stay
where they are.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Iterable

from ..entities import normalize_for_compare


class Recurrence(Enum):
    ONCE = auto()
    RECURRING = auto()
    UNSPECIFIED = auto()


_RECURRING_WORDS = frozenset({
    "jeden", "jede", "jedes", "jeder", "immer", "taeglich", "werktags",
    "wochentags", "wochenends", "wochenende", "werktagen", "wochentagen",
    "wochenenden", "feiertagen", "montags", "dienstags", "mittwochs",
    "donnerstags", "freitags", "samstags", "sonntags", "morgens", "abends",
    "naechtlich", "stuendlich", "regelmaessig", "jedesmal", "staendig",
    "dauerhaft", "kuenftig", "zukuenftig",
})
_ONCE_WORDS = frozenset({
    "heute", "morgen", "uebermorgen", "einmal", "einmalig", "nur", "diesmal",
    "gleich", "naechsten", "naechste", "naechstes", "diesen", "dieses", "diese",
})


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def recurrence_of(text: str) -> Recurrence:
    words = _words(text)
    word_set = set(words)
    # "morgen früh" is a date, "morgens" a repetition; "alle zwei Tage" repeats.
    if word_set & _RECURRING_WORDS or any(
        word == "alle" and index + 2 < len(words) and words[index + 2].startswith(("tag", "woche", "stunde"))
        for index, word in enumerate(words)
    ):
        return Recurrence.RECURRING
    if word_set & _ONCE_WORDS:
        return Recurrence.ONCE
    return Recurrence.UNSPECIFIED


def is_conditional(text: str) -> bool:
    words = _words(text)
    return bool(words) and (
        words[0] in {"wenn", "sobald", "falls", "sowie"} or "wenn" in words[1:4]
    )


def answer_recurrence(text: str) -> Recurrence:
    """Answer to "Nur heute oder jeden Tag?"."""
    words = set(_words(text))
    if words & {"jeden", "immer", "taeglich", "dauerhaft", "regelmaessig", "jedesmal", "jede", "jedes"}:
        return Recurrence.RECURRING
    if words & {"heute", "einmal", "einmalig", "nur", "diesmal", "jetzt", "sofort"}:
        return Recurrence.ONCE
    return Recurrence.UNSPECIFIED


def trigger_kinds(triggers: Iterable[object]) -> set[str]:
    return {getattr(getattr(item, "type", None), "name", "") for item in triggers}
