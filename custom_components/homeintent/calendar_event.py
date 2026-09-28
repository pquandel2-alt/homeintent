"""Deterministic German calendar-event drafting and validation.

This is intentionally separate from scheduled HomeIntent automations: a
calendar event is written through Home Assistant's ``calendar.create_event``
entity service and never becomes an automation in ``automations.yaml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from .nlu.phrases import Span, find, words
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.entity_clarification import (
    CandidateReplyKind,
    render_candidate_question,
    resolve_candidate_reply,
)


_WEEKDAYS = {
    "montag": 0,
    "dienstag": 1,
    "mittwoch": 2,
    "donnerstag": 3,
    "freitag": 4,
    "samstag": 5,
    "sonnabend": 5,
    "sonntag": 6,
}
_WEEKDAY_LABELS = (
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"
)
_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}
_MONTH_LABELS = (
    "", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
    "August", "September", "Oktober", "November", "Dezember",
)
_NUMBER_WORDS: dict[str, float] = {
    "null": 0,
    "ein": 1,
    "eins": 1,
    "eine": 1,
    "einen": 1,
    "einer": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "fuenf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
    "zwoelf": 12,
    "dreizehn": 13,
    "vierzehn": 14,
    "fünfzehn": 15,
    "fuenfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
    "zwanzig": 20,
    "einundzwanzig": 21,
    "zweiundzwanzig": 22,
    "dreiundzwanzig": 23,
    "vierundzwanzig": 24,
    "dreißig": 30,
    "dreissig": 30,
    "eineinhalb": 1.5,
    "anderthalb": 1.5,
}
_NUMBER_TOKEN = r"(?:\d+(?:[,.]\d+)?|[a-zäöüß]+)"
_CLOCK_WORDS = {
    key: int(value)
    for key, value in _NUMBER_WORDS.items()
    if value == int(value) and 1 <= value <= 24
}
_CLOCK_WORD_PATTERN = "|".join(sorted(_CLOCK_WORDS, key=len, reverse=True))
_WEEKDAY_PATTERN = "|".join(_WEEKDAYS)
_MONTH_PATTERN = "|".join(_MONTHS)

_CREATE_VERB_RE = re.compile(
    r"\b(?:trag(?:e)?|trage|eintragen|einzutragen|erstelle|erstellen|"
    r"anlegen|anzulegen|lege|füge|fuege|hinzufügen|hinzufuegen|"
    r"schreib(?:e|en)?|plane|planen|vormerken|mach(?:e|en)?)\b",
    re.IGNORECASE,
)
_EVENT_NOUN_RE = re.compile(
    r"\b(?:termin|kalendertermin|kalendereintrag|kalender)\b", re.IGNORECASE
)
_RELATIVE_DATE_RE = re.compile(r"\b(heute|morgen|übermorgen|uebermorgen)\b", re.IGNORECASE)
_IN_DAYS_RE = re.compile(rf"\bin\s+({_NUMBER_TOKEN})\s+tagen?\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    rf"\b(?:(am)|(nächsten?|naechsten?|kommenden?))?\s*({_WEEKDAY_PATTERN})\b",
    re.IGNORECASE,
)
_NAMED_DATE_RE = re.compile(
    rf"\b(?:am\s+)?(\d{{1,2}})\.?(?:\s+)({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    r"\b(?:am\s+)?(\d{1,2})\.(\d{1,2})\.(?:(\d{2}|\d{4}))?\b",
    re.IGNORECASE,
)
_DAY_PERIOD_RE = re.compile(
    r"\b(früh|frueh|morgens|vormittags|mittags|nachmittags|abends|nachts)\b",
    re.IGNORECASE,
)
_DAY_PERIOD_TIMES = {
    "früh": time(8, 0),
    "frueh": time(8, 0),
    "morgens": time(8, 0),
    "vormittags": time(10, 0),
    "mittags": time(12, 0),
    "nachmittags": time(15, 0),
    "abends": time(20, 0),
    "nachts": time(23, 0),
}
_CLOCK_EXPR = (
    rf"(?:\d{{1,2}}(?::\d{{1,2}})?|{_CLOCK_WORD_PATTERN}|halb\s+[a-zäöüß]+|"
    r"viertel\s+(?:nach|vor)\s+[a-zäöüß]+)"
)
_START_TIME_RE = re.compile(
    rf"\b(?:um|gegen|ab|von)\s+({_CLOCK_EXPR})\s*(?:uhr)?\b", re.IGNORECASE
)
_CLOCK_WITH_UHR_RE = re.compile(rf"\b({_CLOCK_EXPR})\s+uhr\b", re.IGNORECASE)
_SPOKEN_CLOCK_RE = re.compile(
    r"\b(halb\s+[a-zäöüß]+|viertel\s+(?:nach|vor)\s+[a-zäöüß]+)\b",
    re.IGNORECASE,
)
_END_TIME_RE = re.compile(rf"\bbis\s+({_CLOCK_EXPR})\s*(?:uhr)?\b", re.IGNORECASE)
_DURATION_RE = re.compile(
    rf"\b(?:für|fuer|dauert?|dauer)\s+({_NUMBER_TOKEN})\s*"
    rf"(stunden?|std\.?|minuten?|min\.?)"
    rf"(?:\s+(?:und\s+)?({_NUMBER_TOKEN})\s*(minuten?|min\.?))?\b",
    re.IGNORECASE,
)
_BARE_DURATION_RE = re.compile(
    rf"^\s*({_NUMBER_TOKEN})\s*(stunden?|std\.?|minuten?|min\.?)"
    rf"(?:\s+(?:und\s+)?({_NUMBER_TOKEN})\s*(minuten?|min\.?))?\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# Language island "Kalender" (7.5.2): phrases and title slots as lexicon data.
_ALL_DAY_PHRASES = ("fuer den ganzen tag", "den ganzen tag", "ganztaegig")


def _all_day(text: str) -> Span | None:
    tokens = words(text)
    found = find(tokens, _ALL_DAY_PHRASES)
    if found is None:
        return None
    index, length = found
    return Span(tokens[index].start, tokens[index + length - 1].end)


def _fraction_duration(text: str) -> Span | None:
    """"(für) (eine) halbe Stunde" / "Viertelstunde" -> span and fraction word."""
    tokens = words(text)
    found = find(tokens, ("halbe stunde", "viertelstunde"))
    if found is None:
        return None
    index, length = found
    start = index
    if start > 0 and tokens[start - 1].key in {"eine", "einer"}:
        start -= 1
    if start > 0 and tokens[start - 1].key in {"fuer", "dauer", "dauert"} and tokens[start - 1].text.casefold() != "fur":
        start -= 1
    fraction = text[tokens[index].start:tokens[index + length - 1].end]
    return Span(tokens[start].start, tokens[index + length - 1].end, fraction)


def _explicit_title(text: str) -> str | None:
    """"Der Titel ist X", "Der Name soll X sein/heißen", "Nenne ihn X"."""
    tokens = words(text)
    for index, token in enumerate(tokens):
        if token.key in {"titel", "name"} and index + 1 < len(tokens):
            verb = tokens[index + 1].key
            if verb in {"ist", "lautet"} and index + 2 < len(tokens):
                return text[tokens[index + 2].start:]
            last = tokens[-1]
            if (
                verb == "soll" and len(tokens) - index >= 4
                and (last.key == "sein" or last.text.casefold() == "heißen")
                and last.end == len(text.rstrip())
            ):
                return text[tokens[index + 2].start:tokens[-2].end]
        if token.key in {"nenn", "nenne"} and index + 1 < len(tokens):
            if tokens[index + 1].key == "ihn" and index + 2 < len(tokens):
                return text[tokens[index + 2].start:]
            if (
                tokens[index + 1].key == "den" and index + 3 < len(tokens)
                and tokens[index + 2].key == "termin"
            ):
                return text[tokens[index + 3].start:]
    return None


@dataclass(frozen=True)
class CalendarEventDraft:
    """Conversation-local, never persisted event fields."""

    title: str | None = None
    event_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    duration_minutes: int | None = None
    all_day: bool = False
    calendar_entity_id: str | None = None


@dataclass(frozen=True)
class CalendarDraftUpdate:
    draft: CalendarEventDraft
    changed: bool
    error_text: str | None = None


@dataclass(frozen=True)
class CalendarEventServiceCall:
    entity_id: str
    data: dict[str, str]


@dataclass(frozen=True)
class _Pieces:
    event_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    duration_minutes: int | None = None
    all_day: bool = False
    spans: tuple[tuple[int, int], ...] = ()


def writable_calendars(entities: list[EntitySnapshot]) -> tuple[EntitySnapshot, ...]:
    """Return only calendars which advertise HA's CREATE_EVENT feature."""
    return tuple(
        entity
        for entity in entities
        if entity.domain == "calendar" and "CREATE_EVENT" in entity.capabilities
    )


def _number(token: str) -> float | None:
    normalized = token.casefold().replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return _NUMBER_WORDS.get(normalized)


def _clock(raw: str) -> time | None:
    value = raw.casefold().strip()
    numeric = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", value)
    if numeric is not None:
        hour, minute = int(numeric.group(1)), int(numeric.group(2) or 0)
    elif value in _CLOCK_WORDS:
        hour, minute = _CLOCK_WORDS[value] % 24, 0
    elif value.startswith("halb "):
        target = _CLOCK_WORDS.get(value.removeprefix("halb ").strip())
        if target is None:
            return None
        hour, minute = (target - 1) % 24, 30
    elif value.startswith("viertel nach "):
        target = _CLOCK_WORDS.get(value.removeprefix("viertel nach ").strip())
        if target is None:
            return None
        hour, minute = target % 24, 15
    elif value.startswith("viertel vor "):
        target = _CLOCK_WORDS.get(value.removeprefix("viertel vor ").strip())
        if target is None:
            return None
        hour, minute = (target - 1) % 24, 45
    else:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return time(hour, minute)


def _future_named_date(day: int, month: int, year: int | None, now: datetime) -> date | None:
    chosen_year = year or now.year
    try:
        result = date(chosen_year, month, day)
        if year is None and result < now.date():
            result = date(chosen_year + 1, month, day)
        return result
    except ValueError:
        return None


def _parse_date(text: str, now: datetime) -> tuple[date | None, tuple[int, int] | None]:
    match = _IN_DAYS_RE.search(text)
    if match is not None:
        amount = _number(match.group(1))
        if amount is not None and float(amount).is_integer() and 0 <= amount <= 366:
            return now.date() + timedelta(days=int(amount)), match.span()

    match = _RELATIVE_DATE_RE.search(text)
    if match is not None:
        offset = {"heute": 0, "morgen": 1, "übermorgen": 2, "uebermorgen": 2}[
            match.group(1).casefold()
        ]
        return now.date() + timedelta(days=offset), match.span()

    match = _WEEKDAY_RE.search(text)
    if match is not None:
        weekday = _WEEKDAYS[match.group(3).casefold()]
        days = (weekday - now.date().weekday()) % 7
        if match.group(2) is not None and days == 0:
            days = 7
        return now.date() + timedelta(days=days), match.span()

    match = _NAMED_DATE_RE.search(text)
    if match is not None:
        result = _future_named_date(
            int(match.group(1)), _MONTHS[match.group(2).casefold()],
            int(match.group(3)) if match.group(3) else None, now,
        )
        return result, match.span()

    match = _NUMERIC_DATE_RE.search(text)
    if match is not None:
        year = int(match.group(3)) if match.group(3) else None
        if year is not None and year < 100:
            year += 2000
        result = _future_named_date(int(match.group(1)), int(match.group(2)), year, now)
        return result, match.span()
    return None, None


def parse_calendar_date(text: str, now: datetime) -> date | None:
    """Public date-only parser shared by event drafting and calendar queries."""
    return _parse_date(text, now)[0]


def _parse_duration(text: str, *, allow_bare: bool) -> tuple[int | None, tuple[int, int] | None]:
    fraction = _fraction_duration(text)
    if fraction is not None:
        minutes = 30 if fraction.group(1).casefold().startswith("halbe") else 15
        return minutes, fraction.span()
    match = _DURATION_RE.search(text)
    if match is None and allow_bare:
        match = _BARE_DURATION_RE.fullmatch(text)
    if match is None:
        return None, None
    first = _number(match.group(1))
    second = _number(match.group(3)) if match.group(3) else 0
    if first is None or second is None:
        return None, match.span()
    unit = match.group(2).casefold()
    minutes = round(first * 60) if unit.startswith(("st", "std")) else round(first)
    minutes += round(second)
    if not 1 <= minutes <= 7 * 24 * 60:
        return None, match.span()
    return minutes, match.span()


def _parse_pieces(text: str, now: datetime, *, allow_bare: bool = False) -> _Pieces:
    spans: list[tuple[int, int]] = []
    event_date, date_span = _parse_date(text, now)
    if date_span is not None:
        spans.append(date_span)

    all_day_match = _all_day(text)
    all_day = all_day_match is not None
    if all_day_match is not None:
        spans.append(all_day_match.span())

    end_match = _END_TIME_RE.search(text)
    end_time = _clock(end_match.group(1)) if end_match is not None else None
    if end_match is not None:
        spans.append(end_match.span())

    start_match = _START_TIME_RE.search(text)
    start_time = _clock(start_match.group(1)) if start_match is not None else None
    if start_match is not None and start_time is None:
        start_match = None
    if start_match is None:
        for candidate in _CLOCK_WITH_UHR_RE.finditer(text):
            if end_match is not None and candidate.start() >= end_match.start() and candidate.end() <= end_match.end():
                continue
            candidate_time = _clock(candidate.group(1))
            if candidate_time is not None:
                start_match = candidate
                start_time = candidate_time
                break
    if start_match is None:
        candidate = _SPOKEN_CLOCK_RE.search(text)
        if candidate is not None:
            candidate_time = _clock(candidate.group(1))
            if candidate_time is not None:
                start_match = candidate
                start_time = candidate_time
    if start_match is None and allow_bare:
        bare = re.fullmatch(rf"\s*(?:um\s+)?({_CLOCK_EXPR})\s*(?:uhr)?\s*[.!?]?\s*", text, re.IGNORECASE)
        if bare is not None:
            candidate_time = _clock(bare.group(1))
            if candidate_time is not None:
                start_match = bare
                start_time = candidate_time
    if start_match is not None:
        spans.append(start_match.span())

    period_match = _DAY_PERIOD_RE.search(text)
    if start_time is None and period_match is not None:
        start_time = _DAY_PERIOD_TIMES[period_match.group(1).casefold()]
        spans.append(period_match.span())

    duration, duration_span = _parse_duration(text, allow_bare=allow_bare)
    if duration_span is not None:
        spans.append(duration_span)

    return _Pieces(
        event_date=event_date,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration,
        all_day=all_day,
        spans=tuple(spans),
    )


def _calendar_names(calendar: EntitySnapshot) -> tuple[str, ...]:
    values = [calendar.friendly_name, *calendar.aliases]
    object_id = calendar.entity_id.split(".", 1)[-1].replace("_", " ")
    values.append(object_id)
    return tuple(value for value in values if value.strip())


def select_calendar(
    text: str, calendars: tuple[EntitySnapshot, ...]
) -> tuple[EntitySnapshot, ...]:
    """Resolve calendar names without guessing between equally matching entities."""
    spoken = normalize_for_compare(text)
    scored: list[tuple[int, EntitySnapshot]] = []
    for calendar in calendars:
        best = 0
        for name in _calendar_names(calendar):
            candidate = normalize_for_compare(name)
            generic_free = re.sub(r"\bkalender\b", "", candidate).strip()
            if candidate and re.search(
                rf"(?<!\w){re.escape(candidate)}(?!\w)", spoken
            ):
                best = max(best, len(candidate))
            if generic_free and re.search(
                rf"(?<!\w){re.escape(generic_free)}(?:s|n|en)?kalender(?!\w)",
                spoken,
            ):
                best = max(best, len(generic_free))
        if best:
            scored.append((best, calendar))
    if not scored:
        return ()
    highest = max(score for score, _ in scored)
    return tuple(calendar for score, calendar in scored if score == highest)


def _clean_title(
    text: str,
    spans: tuple[tuple[int, int], ...],
    calendars: tuple[EntitySnapshot, ...],
) -> str | None:
    explicit = _explicit_title(text)
    if explicit is not None:
        return explicit.strip(" .,!?:;\"'") or None

    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    value = "".join(chars)
    for calendar in calendars:
        for name in sorted(_calendar_names(calendar), key=len, reverse=True):
            value = re.sub(
                rf"(?:\b(?:in|auf)\s+(?:den|dem|meinen?|unseren?)\s+)?{re.escape(name)}",
                " ",
                value,
                flags=re.IGNORECASE,
            )
    cleanup_patterns = (
        r"\b(?:kannst du|könntest du|koenntest du|ich möchte|ich moechte|ich will|bitte)\b",
        r"\b(?:trag(?:e)?|trage|eintragen|einzutragen|erstelle|erstellen|anlegen|anzulegen|lege|füge|fuege|hinzufügen|hinzufuegen|schreib(?:e|en)?|plane|planen|vormerken|mach(?:e|en)?)\b",
        r"\b(?:einen?|den|der|die|das)?\s*(?:termin|kalendertermin|kalendereintrag)\b",
        r"\b(?:(?:in|auf)\s+(?:den|dem|meinen?|unseren?)?|im)\s*kalender\b",
        r"\bkalender\b",
        r"\b(?:mit\s+dem\s+)?(?:titel|namen)\b",
    )
    for pattern in cleanup_patterns:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bein\s*[.!?]*$", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*(?:für|fuer|als|namens)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" .,!?:;\"'")
    value = re.sub(r"^(?:ein|eine|einen|der|die|das)\s+", "", value, flags=re.IGNORECASE)
    return value if value and len(value) <= 160 else None


def is_calendar_event_request(text: str) -> bool:
    request_cue = _CREATE_VERB_RE.search(text) is not None or (
        re.search(
            r"\bich\s+(?:möchte|moechte|will|brauche)\b",
            text,
            re.IGNORECASE,
        ) is not None
        and _EVENT_NOUN_RE.search(text) is not None
    )
    if not request_cue:
        return False
    if _EVENT_NOUN_RE.search(text) is not None:
        return True
    # Natural shorthand such as "Trag morgen um 10 Uhr Zahnarzt ein" must
    # still carry a real date; this avoids stealing ordinary device commands.
    shorthand_verb = re.search(
        r"\b(?:trag(?:e)?|trage|eintragen|einzutragen|füge|fuege|hinzufügen|hinzufuegen|schreib(?:e|en)?)\b",
        text,
        re.IGNORECASE,
    )
    return shorthand_verb is not None and (
        _RELATIVE_DATE_RE.search(text) is not None
        or _IN_DAYS_RE.search(text) is not None
        or _WEEKDAY_RE.search(text) is not None
        or _NAMED_DATE_RE.search(text) is not None
        or _NUMERIC_DATE_RE.search(text) is not None
    )


def start_calendar_event_draft(
    text: str,
    calendars: tuple[EntitySnapshot, ...],
    now: datetime,
) -> CalendarEventDraft | None:
    if not is_calendar_event_request(text):
        return None
    pieces = _parse_pieces(text, now)
    matches = select_calendar(text, calendars)
    calendar_id = matches[0].entity_id if len(matches) == 1 else None
    if calendar_id is None and len(calendars) == 1:
        calendar_id = calendars[0].entity_id
    title = _clean_title(text, pieces.spans, matches)
    return CalendarEventDraft(
        title=title,
        event_date=pieces.event_date,
        start_time=None if pieces.all_day else pieces.start_time,
        end_time=None if pieces.all_day else pieces.end_time,
        duration_minutes=None if pieces.all_day else pieces.duration_minutes,
        all_day=pieces.all_day,
        calendar_entity_id=calendar_id,
    )


def update_calendar_event_draft(
    text: str,
    draft: CalendarEventDraft,
    calendars: tuple[EntitySnapshot, ...],
    now: datetime,
) -> CalendarDraftUpdate:
    """Merge one reply or correction into an existing event draft."""
    pieces = _parse_pieces(text, now, allow_bare=True)
    matches = select_calendar(text, calendars)
    if not matches and draft.calendar_entity_id is None and len(calendars) > 1:
        selection = resolve_candidate_reply(text, calendars, list(calendars))
        if (
            selection.kind is CandidateReplyKind.SELECTED
            and selection.entity is not None
        ):
            matches = (selection.entity,)
    if len(matches) > 1:
        return CalendarDraftUpdate(
            draft, False, render_candidate_question(matches)
        )

    changes: dict[str, object] = {}
    if pieces.event_date is not None:
        changes["event_date"] = pieces.event_date
    if pieces.all_day:
        changes.update(all_day=True, start_time=None, end_time=None, duration_minutes=None)
    elif pieces.start_time is not None:
        changes.update(start_time=pieces.start_time, all_day=False)
    if pieces.end_time is not None:
        changes.update(end_time=pieces.end_time, duration_minutes=None, all_day=False)
    if pieces.duration_minutes is not None:
        changes.update(duration_minutes=pieces.duration_minutes, end_time=None, all_day=False)
    if len(matches) == 1:
        changes["calendar_entity_id"] = matches[0].entity_id
    elif draft.calendar_entity_id is not None and not any(
        item.entity_id == draft.calendar_entity_id for item in calendars
    ):
        changes["calendar_entity_id"] = None

    title = _clean_title(text, pieces.spans, matches)
    explicit_title = _explicit_title(text) is not None
    has_metadata = bool(pieces.spans or matches)
    if explicit_title and title:
        changes["title"] = title
    elif draft.title is None and title and (not has_metadata or _EVENT_NOUN_RE.search(text)):
        changes["title"] = title

    if draft.calendar_entity_id is None and len(calendars) == 1:
        changes["calendar_entity_id"] = calendars[0].entity_id
    revised = replace(draft, **changes) if changes else draft
    return CalendarDraftUpdate(revised, revised != draft)


def calendar_event_question(
    draft: CalendarEventDraft, calendars: tuple[EntitySnapshot, ...]
) -> str | None:
    if draft.title is None:
        return "Wie soll der Termin heißen?"
    if draft.event_date is None:
        return "An welchem Datum findet der Termin statt?"
    if not draft.all_day and draft.start_time is None:
        return "Um wie viel Uhr beginnt der Termin? Du kannst auch ganztägig sagen."
    if not draft.all_day and draft.end_time is None and draft.duration_minutes is None:
        return "Wann endet der Termin oder wie lange dauert er?"
    if draft.calendar_entity_id is None or not any(
        item.entity_id == draft.calendar_entity_id for item in calendars
    ):
        if not calendars:
            return "Ich finde keinen für HomeIntent freigegebenen, beschreibbaren Kalender."
        return render_candidate_question(calendars)
    return None


def _calendar_for_id(
    entity_id: str | None, calendars: tuple[EntitySnapshot, ...]
) -> EntitySnapshot | None:
    return next((item for item in calendars if item.entity_id == entity_id), None)


def render_calendar_event_preview(
    draft: CalendarEventDraft, calendars: tuple[EntitySnapshot, ...]
) -> str:
    assert draft.title is not None and draft.event_date is not None
    calendar = _calendar_for_id(draft.calendar_entity_id, calendars)
    assert calendar is not None
    day = _WEEKDAY_LABELS[draft.event_date.weekday()]
    date_text = f"{day}, {draft.event_date.day}. {_MONTH_LABELS[draft.event_date.month]} {draft.event_date.year}"
    if draft.all_day:
        time_text = "ganztägig"
    else:
        assert draft.start_time is not None
        if draft.end_time is not None:
            end_text = draft.end_time.strftime("%H:%M")
        else:
            assert draft.duration_minutes is not None
            start = datetime.combine(draft.event_date, draft.start_time)
            end_text = (start + timedelta(minutes=draft.duration_minutes)).strftime("%H:%M")
        time_text = f"{draft.start_time.strftime('%H:%M')} bis {end_text} Uhr"
    return (
        f"Termin „{draft.title}“, {date_text}, {time_text}, "
        f"Kalender „{calendar.friendly_name}“. Soll ich den Termin eintragen?"
    )


def build_calendar_event_service_call(
    draft: CalendarEventDraft,
    calendars: tuple[EntitySnapshot, ...],
    now: datetime,
) -> CalendarEventServiceCall:
    """Validate the completed draft and build HA calendar.create_event data."""
    question = calendar_event_question(draft, calendars)
    if question is not None:
        raise ValueError(question)
    assert draft.title is not None and draft.event_date is not None
    calendar = _calendar_for_id(draft.calendar_entity_id, calendars)
    if calendar is None or "CREATE_EVENT" not in calendar.capabilities:
        raise ValueError("Der ausgewählte Kalender ist nicht mehr beschreibbar")

    if draft.all_day:
        if draft.event_date < now.date():
            raise ValueError("Das gewünschte Datum liegt bereits in der Vergangenheit")
        data = {
            "summary": draft.title,
            "start_date": draft.event_date.isoformat(),
            "end_date": (draft.event_date + timedelta(days=1)).isoformat(),
        }
        return CalendarEventServiceCall(calendar.entity_id, data)

    assert draft.start_time is not None
    start = datetime.combine(draft.event_date, draft.start_time, tzinfo=now.tzinfo)
    if draft.end_time is not None:
        end = datetime.combine(draft.event_date, draft.end_time, tzinfo=now.tzinfo)
        if end <= start:
            raise ValueError("Die Endzeit muss nach der Startzeit liegen")
    else:
        assert draft.duration_minutes is not None
        end = start + timedelta(minutes=draft.duration_minutes)
    if start <= now:
        raise ValueError("Der gewünschte Terminbeginn liegt bereits in der Vergangenheit")
    for value in (start, end):
        if value.tzinfo is not None:
            if value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset():
                raise ValueError("Die gewünschte Uhrzeit ist wegen der Zeitumstellung nicht eindeutig")
            round_trip = value.astimezone(timezone.utc).astimezone(value.tzinfo)
            if (round_trip.date(), round_trip.time().replace(tzinfo=None)) != (
                value.date(), value.time().replace(tzinfo=None)
            ):
                raise ValueError("Die gewünschte Uhrzeit existiert wegen der Zeitumstellung nicht")
    return CalendarEventServiceCall(
        calendar.entity_id,
        {
            "summary": draft.title,
            "start_date_time": start.isoformat(),
            "end_date_time": end.isoformat(),
        },
    )
