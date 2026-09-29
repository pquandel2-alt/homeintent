"""Deterministic calendar queries and capability-safe mutation requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum, auto
from typing import Any, Mapping

from .calendar_event import select_calendar
from .nlu.phrases import Span, Word, find, has, words
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.language_frontend import LanguageDocument


class CalendarManagementKind(Enum):
    LIST = auto()
    FIND = auto()
    DELETE = auto()
    RESCHEDULE = auto()
    AVAILABILITY = auto()
    UPDATE = auto()


@dataclass(frozen=True)
class CalendarManagementRequest:
    kind: CalendarManagementKind
    start: datetime
    end: datetime
    title_filter: str | None = None
    calendar_entity_ids: tuple[str, ...] = ()
    new_start_time: time | None = None
    new_date: date | None = None
    new_title: str | None = None
    new_duration_minutes: int | None = None


@dataclass(frozen=True)
class CalendarEventSummary:
    calendar_entity_id: str
    summary: str
    start: str
    end: str
    description: str | None = None
    location: str | None = None
    uid: str | None = None
    recurrence_id: str | None = None


_CALENDAR_CUE_RE = re.compile(r"\b(?:kalender|\w*termin\w*)\b", re.I)
_DELETE_RE = re.compile(r"\b(?:lösch\w*|loesch\w*|entfern\w*|sag\w*\s+.*\bab)\b", re.I)
_RESCHEDULE_RE = re.compile(r"\b(?:verschieb\w*|verleg\w*)\b", re.I)
_WEEKEND_RE = re.compile(r"\b(?:am|dieses|kommendes|nächstes|naechstes)?\s*wochenende\b", re.I)
_WEEK_RE = re.compile(r"\b(?:diese|kommende|nächste|naechste)\s+woche\b", re.I)
_NEW_TIME_RE = re.compile(r"\bauf\s+(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", re.I)
_TARGET_DATE_RE = re.compile(
    r"\bauf\s+(?P<date>heute|morgen|übermorgen|uebermorgen|am\s+\d{1,2}\.\d{1,2}\.(?:\d{2,4})?)\b",
    re.I,
)
_ANY_NEW_TIME_RE = re.compile(r"\b(?:auf|um)\s+(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", re.I)


# Language island "Kalender" (7.5.2): cue phrases and name slots are lexicon
# data on word tokens (``nlu.phrases``), not sentence patterns.
_LIST_PHRASES = (
    "was steht|ist", "welche termine", "was habe ich", "zeig* mir meine termine",
    "zeig* mir termine", "zeig* meine termine", "zeig* termine", "wann ist",
)
_MY = frozenset({"mein", "meine", "meinen", "meiner", "meines"})


def _is_list_request(text: str) -> bool:
    return has(words(text), *_LIST_PHRASES)


def _asks_availability(text: str) -> bool:
    """"Habe ich … Zeit/frei", "Bin ich … frei"."""
    tokens = words(text)
    for index in range(len(tokens) - 1):
        first, second = tokens[index].key, tokens[index + 1].key
        later = [token.key for token in tokens[index + 2:]]
        if first in {"habe", "hab"} and second == "ich" and ({"zeit", "frei"} & set(later)):
            return True
        if first == "bin" and second == "ich" and "frei" in later:
            return True
    return False


def _clock_of(token: Word) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", token.key)
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def _time_window(text: str) -> tuple[int, int, int, int] | None:
    """"von 9 bis 11 Uhr", "zwischen 14 und 16 Uhr"."""
    tokens = words(text)
    for index, token in enumerate(tokens):
        if token.key not in {"von", "zwischen"} or index + 1 >= len(tokens):
            continue
        first = _clock_of(tokens[index + 1])
        cursor = index + 2
        if cursor < len(tokens) and tokens[cursor].key == "uhr":
            cursor += 1
        if first is None or cursor + 1 >= len(tokens) or tokens[cursor].key not in {"bis", "und"}:
            continue
        second = _clock_of(tokens[cursor + 1])
        if second is None:
            continue
        return first[0], first[1], second[0], second[1]
    return None


def _title_after_when(text: str) -> str | None:
    """"Wann ist mein Zahnarzttermin?" -> "Zahnarzt"."""
    stripped = text.strip()
    tokens = words(stripped)
    found = find(tokens, ("wann ist",))
    if found is None:
        return None
    index = found[0] + 2
    if index < len(tokens) and tokens[index].key in _MY and index + 1 < len(tokens):
        index += 1
    if index >= len(tokens):
        return None
    value = stripped[tokens[index].start:].rstrip(" ?!.,")
    if value.casefold().endswith("termin") and len(value) > len("termin"):
        value = value[: -len("termin")]
    value = re.sub(r"\btermin\b$", "", value, flags=re.I).strip(" .,!?:;")
    return value or None


def _rename(text: str) -> Span | None:
    """"Benenne den Termin X in Y um" -> old/new."""
    tokens = words(text)
    for index, token in enumerate(tokens):
        if not token.key.startswith("benenn"):
            continue
        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor].key == "den":
            cursor += 1
        if cursor >= len(tokens) or tokens[cursor].key != "termin":
            continue
        old_start = cursor + 1
        for middle in range(old_start + 1, len(tokens)):
            if tokens[middle].key not in {"in", "zu"}:
                continue
            for last in range(middle + 2, len(tokens)):
                if tokens[last].key == "um":
                    old = text[tokens[old_start].start:tokens[middle - 1].end]
                    new = text[tokens[middle + 1].start:tokens[last - 1].end]
                    return Span(token.start, tokens[last].end, named={"old": old, "new": new})
    return None


def _change_duration(text: str) -> Span | None:
    """"Ändere die Dauer vom Termin X auf 2 Stunden"."""
    tokens = words(text)
    for index, token in enumerate(tokens):
        if not token.key.startswith("aender"):
            continue
        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor].key == "die":
            cursor += 1
        if cursor >= len(tokens) or tokens[cursor].key != "dauer":
            continue
        cursor += 1
        if cursor < len(tokens) and tokens[cursor].key == "vom":
            cursor += 1
        elif cursor + 1 < len(tokens) and tokens[cursor].key == "von" and tokens[cursor + 1].key == "dem":
            cursor += 2
        else:
            continue
        if cursor >= len(tokens) or tokens[cursor].key != "termin":
            continue
        title_start = cursor + 1
        for auf in range(title_start + 1, len(tokens) - 2):
            if tokens[auf].key != "auf" or not tokens[auf + 1].key.isdigit():
                continue
            unit = tokens[auf + 2].key
            if unit in {"minute", "minuten", "stunde", "stunden"}:
                title = text[tokens[title_start].start:tokens[auf - 1].end]
                return Span(
                    token.start, tokens[auf + 2].end,
                    named={"title": title, "count": tokens[auf + 1].key, "unit": tokens[auf + 2].text},
                )
    return None


def _moved_title(text: str) -> str | None:
    """"Verschiebe den (Kalender)Termin X auf …" -> X."""
    tokens = words(text)
    for index, token in enumerate(tokens):
        if not (token.key.startswith("verschieb") or token.key.startswith("verleg")):
            continue
        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor].key == "den":
            cursor += 1
        if cursor >= len(tokens) or tokens[cursor].key not in {"termin", "kalendertermin"}:
            continue
        for auf in range(cursor + 2, len(tokens)):
            if tokens[auf].key == "auf":
                return text[tokens[cursor + 1].start:tokens[auf - 1].end]
    return None


def _day_range(value: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=now.tzinfo)
    return start, start + timedelta(days=1)


def _date_from_text(text: str, now: datetime) -> date | None:
    from .calendar_event import parse_calendar_date

    return parse_calendar_date(text, now)


def _range_from_text(text: str, now: datetime) -> tuple[datetime, datetime]:
    if _WEEKEND_RE.search(text):
        days_to_saturday = (5 - now.date().weekday()) % 7
        saturday = now.date() + timedelta(days=days_to_saturday)
        start = datetime.combine(saturday, time.min, tzinfo=now.tzinfo)
        return start, start + timedelta(days=2)
    if _WEEK_RE.search(text):
        start_date = now.date()
        end_date = start_date + timedelta(days=7 - start_date.weekday())
        return (
            datetime.combine(start_date, time.min, tzinfo=now.tzinfo),
            datetime.combine(end_date, time.min, tzinfo=now.tzinfo),
        )
    requested_date = _date_from_text(text, now)
    if requested_date is not None:
        return _day_range(requested_date, now)
    return now, now + timedelta(days=366)


def _title_filter(text: str) -> str | None:
    return _title_after_when(text)


def _mutation_title_filter(text: str) -> str | None:
    value = _DELETE_RE.sub(" ", text, count=1)
    value = _RESCHEDULE_RE.sub(" ", value, count=1)
    value = _NEW_TIME_RE.sub(" ", value)
    value = re.sub(
        r"\b(?:den|die|das|meinen?|meine|kalender|termin|heute|morgen|übermorgen|uebermorgen)\b",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:am|um|auf)\s+\d{1,2}(?::\d{2})?\s*(?:uhr)?\b", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" .,!?:;")
    return value or None


def parse_calendar_management(
    text: str,
    calendars: tuple[EntitySnapshot, ...],
    now: datetime,
    document: LanguageDocument | None = None,
) -> CalendarManagementRequest | None:
    """Parse bounded calendar management language without stealing device commands."""
    if document is not None:
        text = document.source_text
    if re.search(r"\b(?:auftrag|automation)\b|(?:bedingung|auslöser|ausloeser|trigger)", text, re.I):
        return None
    rename_match = _rename(text)
    duration_match = _change_duration(text)
    has_mutation = (
        _DELETE_RE.search(text) is not None
        or _RESCHEDULE_RE.search(text) is not None
        or rename_match is not None
        or duration_match is not None
    )
    availability = _asks_availability(text)
    if _CALENDAR_CUE_RE.search(text) is None and not has_mutation and not availability:
        return None
    kind: CalendarManagementKind | None = None
    if availability:
        kind = CalendarManagementKind.AVAILABILITY
    elif rename_match is not None or duration_match is not None:
        kind = CalendarManagementKind.UPDATE
    elif _DELETE_RE.search(text):
        kind = CalendarManagementKind.DELETE
    elif _RESCHEDULE_RE.search(text):
        kind = CalendarManagementKind.RESCHEDULE
    elif _is_list_request(text):
        kind = CalendarManagementKind.FIND if re.search(r"\bwann\s+ist\b", text, re.I) else CalendarManagementKind.LIST
    if kind is None:
        return None

    selected = select_calendar(text, calendars)
    calendar_ids = tuple(item.entity_id for item in selected) or tuple(
        item.entity_id for item in calendars
    )
    start, end = _range_from_text(text, now)
    target_date_match = _TARGET_DATE_RE.search(text) if kind is CalendarManagementKind.RESCHEDULE else None
    new_date = _date_from_text(target_date_match.group("date"), now) if target_date_match else None
    if new_date is not None:
        # The date after "auf" is the destination, not a lookup constraint.
        start, end = now, now + timedelta(days=366)
    if kind is CalendarManagementKind.AVAILABILITY:
        window = _time_window(text)
        if window is not None:
            start_hour, start_minute, end_hour, end_minute = window
            if 0 <= start_hour <= 23 and 0 <= end_hour <= 23 and start_minute < 60 and end_minute < 60:
                requested_date = _date_from_text(text, now) or now.date()
                start = datetime.combine(requested_date, time(start_hour, start_minute), tzinfo=now.tzinfo)
                end = datetime.combine(requested_date, time(end_hour, end_minute), tzinfo=now.tzinfo)
    new_start: time | None = None
    if kind is CalendarManagementKind.RESCHEDULE:
        match = _NEW_TIME_RE.search(text) or _ANY_NEW_TIME_RE.search(text)
        if match is not None:
            hour, minute = int(match.group(1)), int(match.group(2) or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                new_start = time(hour, minute)
    moved_title = _moved_title(text)
    if rename_match is not None:
        title_filter = rename_match.group("old").strip()
    elif duration_match is not None:
        title_filter = duration_match.group("title").strip()
    elif kind in {CalendarManagementKind.DELETE, CalendarManagementKind.RESCHEDULE}:
        title_filter = (
            moved_title.strip()
            if moved_title is not None
            else _mutation_title_filter(text)
        )
    else:
        title_filter = _title_filter(text)
    return CalendarManagementRequest(
        kind=kind,
        start=start,
        end=end,
        title_filter=title_filter,
        calendar_entity_ids=calendar_ids,
        new_start_time=new_start,
        new_date=new_date,
        new_title=rename_match.group("new").strip(" .!?;") if rename_match else None,
        new_duration_minutes=(
            int(duration_match.group("count"))
            * (60 if duration_match.group("unit").casefold().startswith("st") else 1)
            if duration_match else None
        ),
    )


def flatten_calendar_response(
    response: Mapping[str, Any] | None,
    title_filter: str | None = None,
) -> tuple[CalendarEventSummary, ...]:
    """Normalize HA's calendar-keyed service response and apply title search."""
    wanted = normalize_for_compare(title_filter) if title_filter else None
    result: list[CalendarEventSummary] = []
    for calendar_id, payload in (response or {}).items():
        if not isinstance(payload, Mapping):
            continue
        events = payload.get("events", ())
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, Mapping):
                continue
            summary = str(event.get("summary") or "Termin")
            if wanted and wanted not in normalize_for_compare(summary):
                continue
            start, end = event.get("start"), event.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                continue
            result.append(
                CalendarEventSummary(
                    calendar_entity_id=str(calendar_id),
                    summary=summary,
                    start=start,
                    end=end,
                    description=event.get("description"),
                    location=event.get("location"),
                    uid=event.get("uid"),
                    recurrence_id=event.get("recurrence_id"),
                )
            )
    return tuple(sorted(result, key=lambda item: item.start))


def _spoken_start(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return value
        return f"am {parsed_date.day}.{parsed_date.month}."
    return f"am {parsed.day}.{parsed.month}. um {parsed.strftime('%H:%M')} Uhr"


def render_calendar_events(
    events: tuple[CalendarEventSummary, ...],
    request: CalendarManagementRequest,
) -> str:
    if request.kind is CalendarManagementKind.AVAILABILITY:
        if not events:
            return (
                f"Ja, zwischen {request.start.strftime('%H:%M')} und "
                f"{request.end.strftime('%H:%M')} Uhr ist kein Termin eingetragen."
            )
        names = ", ".join(f"„{event.summary}“" for event in events[:3])
        return f"Nein, in diesem Zeitraum liegt {names}."
    if not events:
        if request.title_filter:
            return f"Ich finde keinen Termin mit „{request.title_filter}“ im abgefragten Zeitraum."
        return "Im abgefragten Zeitraum stehen keine Termine im Kalender."
    if request.kind is CalendarManagementKind.FIND and len(events) == 1:
        event = events[0]
        return f"„{event.summary}“ ist {_spoken_start(event.start)}."
    rendered = [f"„{event.summary}“ {_spoken_start(event.start)}" for event in events[:8]]
    suffix = "" if len(events) <= 8 else f" Außerdem gibt es {len(events) - 8} weitere Termine."
    return f"Ich habe {len(events)} Termin{'e' if len(events) != 1 else ''} gefunden: " + "; ".join(rendered) + "." + suffix
