"""Dependency-free, token-based temporal meaning extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum, auto
from typing import Sequence

from .german_structure import StructuralToken


class TemporalKind(Enum):
    NOW = auto()
    RELATIVE_DELAY = auto()
    DURATION = auto()
    ABSOLUTE_TIME = auto()
    DATE = auto()
    WEEKDAY = auto()
    BEFORE = auto()
    AFTER = auto()
    UNTIL = auto()
    WHILE = auto()
    SINCE = auto()
    SUN_EVENT = auto()


@dataclass(frozen=True)
class TemporalExpression:
    kind: TemporalKind
    token_start: int
    token_end: int
    value: str
    seconds: int | None = None


_NUMBERS = {
    "ein": 1, "eine": 1, "einer": 1, "zwei": 2, "drei": 3,
    "vier": 4, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "fuenfzehn": 15, "zwanzig": 20,
    "dreissig": 30, "sechzig": 60,
}
_UNIT_SECONDS = {
    "sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60,
    "stunde": 3600, "stunden": 3600, "tag": 86400, "tage": 86400,
}
_RELATIONS = {
    "vor": TemporalKind.BEFORE,
    "bevor": TemporalKind.BEFORE,
    "nach": TemporalKind.AFTER,
    "nachdem": TemporalKind.AFTER,
    "bis": TemporalKind.UNTIL,
    "waehrend": TemporalKind.WHILE,
    "solange": TemporalKind.WHILE,
    "seit": TemporalKind.SINCE,
}
_WEEKDAYS = frozenset({
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag",
    "samstag", "sonntag",
})
_DATES = frozenset({"heute", "morgen", "uebermorgen", "gestern", "vorgestern"})
_SUN_EVENTS = frozenset({"sonnenaufgang", "sonnenuntergang"})
_NON_TEMPORAL_NACH_COMPLEMENTS = frozenset(
    {"oben", "unten", "links", "rechts", "vorne", "hinten", "hause"}
)


def _number(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return _NUMBERS.get(word)


def analyse_temporal_semantics(
    tokens: Sequence[StructuralToken],
) -> tuple[TemporalExpression, ...]:
    """Extract composable time relations without interpreting execution support."""
    words = tuple(token.canonical for token in tokens)
    found: list[TemporalExpression] = []
    for index, word in enumerate(words):
        if word in {"jetzt", "sofort"}:
            found.append(TemporalExpression(TemporalKind.NOW, index, index + 1, word))
        if word in _DATES:
            found.append(TemporalExpression(TemporalKind.DATE, index, index + 1, word))
        if word in _WEEKDAYS:
            found.append(TemporalExpression(TemporalKind.WEEKDAY, index, index + 1, word))
        if word in _SUN_EVENTS:
            found.append(TemporalExpression(TemporalKind.SUN_EVENT, index, index + 1, word))
        relation = _RELATIONS.get(word)
        if (
            word == "nach"
            and index + 1 < len(words)
            and words[index + 1] in _NON_TEMPORAL_NACH_COMPLEMENTS
        ):
            # Device directions ("nach oben fahren") and the presence
            # phrase "nach Hause" are not temporal AFTER scopes.
            relation = None
        if word == "vor" and all(
            not getattr(token, "is_word", True) for token in tokens[index + 1:]
        ):
            # Sentence-final "vor" is the particle of a separable verb
            # ("Bereite den Filmabend vor"), never "before" (7.6.1).
            relation = None
        if (
            word == "bis"
            and index + 1 < len(words)
            and words[index + 1] == "auf"
            and not (index + 2 < len(words) and _number(words[index + 2]) is not None)
        ):
            # "bis auf die Stehlampe" is an exception, not an UNTIL scope.
            relation = None
        if relation is not None:
            found.append(TemporalExpression(relation, index, index + 1, word))
        if index + 2 < len(words):
            amount = _number(words[index + 1])
            multiplier = _UNIT_SECONDS.get(words[index + 2])
            if amount is not None and multiplier is not None:
                kind = {
                    "in": TemporalKind.RELATIVE_DELAY,
                    "fuer": TemporalKind.DURATION,
                    "seit": TemporalKind.SINCE,
                }.get(word)
                if kind is not None:
                    found.append(TemporalExpression(
                        kind, index, index + 3,
                        " ".join(words[index:index + 3]),
                        amount * multiplier,
                    ))
        if word == "um" and index + 3 < len(words) and words[index + 2] == ":":
            # "um 6:30 (Uhr)" - the clock form of "um halb sieben" (7.7.1 A3).
            hour, minute = _number(words[index + 1]), _number(words[index + 3])
            if hour is not None and minute is not None and 0 <= hour <= 23 and 0 <= minute <= 59:
                end = index + 5 if index + 4 < len(words) and words[index + 4] == "uhr" else index + 4
                found.append(TemporalExpression(
                    TemporalKind.ABSOLUTE_TIME, index, end, f"{hour:02d}:{minute:02d}"
                ))
        if word == "um" and index + 2 < len(words):
            hour = _number(words[index + 1])
            if hour is not None and words[index + 2] == "uhr" and 0 <= hour <= 23:
                found.append(TemporalExpression(
                    TemporalKind.ABSOLUTE_TIME, index, index + 3, f"{hour:02d}:00"
                ))
    # Prefer the richer duration/since span over its one-token relation.
    return tuple(
        item for item in found
        if not any(
            other is not item
            and other.token_start == item.token_start
            and other.token_end > item.token_end
            for other in found
        )
    )


@dataclass(frozen=True)
class TemporalWindow:
    """One half-open local-time interval used by historical queries."""

    start: datetime
    end: datetime
    label: str


def resolve_history_window(
    tokens: Sequence[StructuralToken], now: datetime
) -> TemporalWindow | None:
    """Resolve supported German history phrases in Home Assistant local time.

    ``now`` must be the timezone-aware value supplied by Home Assistant.  The
    returned bounds retain that timezone and are half-open, so a local day is
    always ``[local midnight, next local midnight)`` even across UTC offsets.
    This lives beside the temporal scanner to keep one temporal authority.
    """
    if now.tzinfo is None:
        raise ValueError("Historical time resolution requires local timezone data")
    words = tuple(token.canonical for token in tokens)
    text = " ".join(words)
    midnight = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)

    if "letzte nacht" in text:
        return TemporalWindow(
            midnight - timedelta(hours=4), midnight + timedelta(hours=6), "letzte Nacht"
        )

    offset: int | None = None
    label = ""
    if "vorgestern" in words:
        offset, label = -2, "vorgestern"
    elif "gestern" in words:
        offset, label = -1, "gestern"
    elif "heute" in words:
        offset, label = 0, "heute"
    if offset is None:
        return None

    start = midnight + timedelta(days=offset)
    end = start + timedelta(days=1)
    # Phrase-level precedence prevents the DATE token ``morgen`` in
    # ``heute Morgen`` from being mistaken for tomorrow.
    if "morgen" in words and ("heute" in words or "gestern" in words):
        return TemporalWindow(
            start + timedelta(hours=5), start + timedelta(hours=12), f"{label} Morgen"
        )
    if "abend" in words:
        return TemporalWindow(
            start + timedelta(hours=18), end, f"{label} Abend"
        )
    return TemporalWindow(start, end, label)


def resolve_scheduled_datetime(
    expressions: Sequence[TemporalExpression], now: datetime
) -> datetime | None:
    """Resolve a supported date plus clock time against HA's local clock."""
    if now.tzinfo is None:
        raise ValueError("Scheduling requires local timezone data")
    clock = next(
        (item.value for item in expressions if item.kind is TemporalKind.ABSOLUTE_TIME),
        None,
    )
    if clock is None:
        return None
    hour_text, minute_text = clock.split(":", 1)
    date_value = next(
        (item.value for item in expressions if item.kind is TemporalKind.DATE),
        None,
    )
    day_offset = (
        {"heute": 0, "morgen": 1, "uebermorgen": 2}.get(date_value)
        if date_value is not None
        else None
    )
    if day_offset is None:
        target = now.replace(
            hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0
        )
        return target if target > now else target + timedelta(days=1)
    target_date = now.date() + timedelta(days=day_offset)
    return datetime.combine(
        target_date,
        time(int(hour_text), int(minute_text)),
        tzinfo=now.tzinfo,
    )
