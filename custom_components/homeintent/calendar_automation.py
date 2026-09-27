"""Compositional extraction of calendar-event automation triggers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import EntitySnapshot, normalize_for_compare
from .productivity import parse_duration_seconds


@dataclass(frozen=True)
class CalendarAutomationDraft:
    calendar_entity_id: str
    event: str
    offset_minutes: int | None
    summary_contains: str | None
    action_text: str
    # Without a named calendar a titled event ("der Termin Urlaub") is
    # watched in every calendar; the title condition keeps it precise.
    calendar_entity_ids: tuple[str, ...] = ()


_CALENDAR_CUE_RE = re.compile(
    r"\b(?:[a-zäöüß]*kalender|[a-zäöüß]*termin|ereignis|veranstaltung)\w*\b", re.IGNORECASE
)
_ACTION_START_RE = re.compile(
    r"\b(?:schalt(?:e|en|et)?|mach(?:e|en|t)?|fahr(?:e|en|t)?|"
    r"öffn(?:e|en|et)?|oeffn(?:e|en|et)?|schließ(?:e|en|t)?|schliess(?:e|en|t)?|"
    r"stell(?:e|en|t)?|setz(?:e|en|t)?|start(?:e|en|et)?|aktivier(?:e|en|t)?|"
    r"send(?:e|en|et)?|benachrichtig(?:e|en|t)?|erinner(?:e|n|t)?)\b",
    re.IGNORECASE,
)
_EVENT_RE = re.compile(
    r"\b(?P<start>beginn(?:t|en)?|start(?:et|en)?|anfäng(?:t|en)?|anfaeng(?:t|en)?|"
    r"losgeht)|(?P<end>end(?:et|en)?|vorbei\s+ist|aufhört|aufhoert)\b",
    re.IGNORECASE,
)
_OFFSET_RE = re.compile(r"\b(vor|nach)\b", re.IGNORECASE)


def _calendar_names(entity: EntitySnapshot) -> tuple[str, ...]:
    return (entity.friendly_name, *entity.aliases, entity.entity_id.split(".", 1)[1].replace("_", " "))


def _resolve_calendar(text: str, entities: list[EntitySnapshot]) -> EntitySnapshot | None:
    calendars = [entity for entity in entities if entity.domain == "calendar"]
    matched: list[EntitySnapshot] = []
    normalized = normalize_for_compare(text)
    for calendar in calendars:
        if any(
            re.search(rf"(?<!\w){re.escape(normalize_for_compare(name))}(?!\w)", normalized)
            for name in _calendar_names(calendar)
            if normalize_for_compare(name)
        ):
            matched.append(calendar)
    unique = {entity.entity_id: entity for entity in matched}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return calendars[0] if len(calendars) == 1 else None


def _extract_summary(
    trigger_clause: str,
    calendar: EntitySnapshot,
    event_start: int,
    offset_match: re.Match[str] | None,
) -> str | None:
    value = trigger_clause[:event_start]
    value = re.sub(r"\b(?:wenn|sobald|falls|ein|eine|einen|einem|einer|der|die|das|im|im\s+kalender)\b", " ", value, flags=re.IGNORECASE)
    if offset_match is not None:
        value = value[offset_match.end():]
    for name in _calendar_names(calendar):
        value = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b([a-zäöüß]+)termin\w*\b", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:[a-zäöüß]*kalender|termin|ereignis|veranstaltung)\w*\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,:.-")
    if not value:
        return None
    # Duration words before "vor/nach" are not event-title text.
    value = re.sub(
        r"^(?:\d+|[a-zäöüß]+)\s*(?:stunden?|minuten?|sekunden?)\s+", "", value,
        flags=re.IGNORECASE,
    ).strip()
    return value[:100] or None


def parse_calendar_automation_draft(
    text: str, entities: list[EntitySnapshot]
) -> CalendarAutomationDraft | None:
    if not _CALENDAR_CUE_RE.search(text):
        return None
    calendar = _resolve_calendar(text, entities)
    all_calendars = tuple(entity for entity in entities if entity.domain == "calendar")
    unnamed = calendar is None and len(all_calendars) > 1
    if calendar is None:
        if not unnamed:
            return None
        calendar = all_calendars[0]
    action_matches = list(_ACTION_START_RE.finditer(text))
    if not action_matches:
        return None
    reverse_marker = re.search(r"\s*,?\s+(wenn|sobald|falls)\s+", text, re.IGNORECASE)
    if action_matches[0].start() == 0 and reverse_marker is not None:
        action_text = text[:reverse_marker.start()].strip(" ,")
        trigger_clause = text[reverse_marker.start():].strip(" ,")
    else:
        action_match = action_matches[-1]
        trigger_clause = text[:action_match.start()].strip(" ,")
        action_text = text[action_match.start():].strip(" ,")
    event_matches = list(_EVENT_RE.finditer(trigger_clause))
    if len(event_matches) > 1:
        return None
    event_match = event_matches[0] if event_matches else None
    event_start = event_match.start() if event_match is not None else len(trigger_clause)
    event = "start" if event_match is None or event_match.group("start") else "end"
    offset_matches = list(_OFFSET_RE.finditer(trigger_clause[:event_start]))
    offset_match = offset_matches[-1] if offset_matches else None
    if event_match is None and offset_match is None:
        return None
    offset_minutes: int | None = None
    if offset_match is not None:
        seconds = parse_duration_seconds(trigger_clause[:offset_match.start()])
        if seconds is not None:
            if seconds % 60:
                return None  # HA calendar offsets are minute-based in our AST.
            offset_minutes = seconds // 60
            if offset_match.group(1).casefold() == "vor":
                offset_minutes *= -1
    summary = _extract_summary(trigger_clause, calendar, event_start, offset_match)
    if unnamed and summary is None:
        return None
    return CalendarAutomationDraft(
        calendar.entity_id, event, offset_minutes, summary, action_text,
        tuple(entity.entity_id for entity in all_calendars) if unnamed else (calendar.entity_id,),
    )
