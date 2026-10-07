"""What happened in a period (7.9.2 B1): an event summary, read-only.

"Was war los, während ich weg war?", "Was ist heute passiert?", "Was war
letzte Nacht?", "Was ist seit 14 Uhr passiert?", "Was war gestern los?"

7.9.1 answered "während ich weg war" with the *current* state. Now the
answer is a list of events in the period, most important first, at most
``SPOKEN_LIMIT`` spoken - the rest on "Was noch?":

1. alarms (smoke, water, gas, CO detectors that went on);
2. monitors and automations that ran (their own run record);
3. doors, gates and - while the speaker was away - windows that opened,
   with time; motion while the speaker was away;
4. appliances that finished;
5. what HomeIntent executed (its execution trace);
6. people arriving or leaving. A non-administrator hears only about
   themselves by name; others are summarized ("jemand ist um 15:02
   heimgekommen").

Sources: the Home Assistant recorder (``history.get_significant_states``
through the one adapter in ``history_query``), HomeIntent's execution
trace. No own data store. Without the recorder: an honest answer.

The period: "während ich weg war" is the speaker's last absence from the
history of their ``person`` entity (still away -> until now).

Home-Assistant-free except for ``async_collect``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .entities import EntitySnapshot, normalize_for_compare
from .energy_query import spoken_period

__all__ = (
    "MAX_EVENTS",
    "MAX_HISTORY_ENTITIES",
    "MAX_SUMMARY_DAYS",
    "SPOKEN_LIMIT",
    "SummaryEvent",
    "SummaryQuery",
    "last_absence",
    "parse_summary_query",
    "render_summary",
    "states_of",
    "summarize",
)

SPOKEN_LIMIT = 5
# Bounds (7.9.3 A4): the period, the entities read and the events kept.
MAX_SUMMARY_DAYS = 7
MAX_HISTORY_ENTITIES = 600
MAX_EVENTS = 400

# "Was war los / ist passiert / hat sich getan / habe ich verpasst …?"
_HAPPEN_PREDICATES = frozenset({
    "los", "passiert", "geschehen", "getan", "verpasst", "versaeumt", "vorgefallen", "neuigkeiten", "neues",
    "gab", "war",
})
# The speaker's absence as a construction (7.9.3 A4): a subordinator
# ("während", "als", "seit", "solange") + "ich" + an away predicate ("weg",
# "unterwegs", "fort", "gegangen", "nicht da/zuhause", "aus dem Haus"), or a
# possessive absence noun ("in/während/nach meiner Abwesenheit").
_SUBORDINATORS = frozenset({"waehrend", "als", "seit", "solange", "wo"})
_AWAY_PREDICATES = frozenset({
    "weg", "unterwegs", "fort", "gegangen", "losgegangen", "weggegangen", "weggefahren", "losgefahren",
    "verreist", "abwesend", "draussen", "raus",
})
_ABSENCE_NOUNS = frozenset({"abwesenheit", "abwesend"})
# "Was hab ich verpasst?" - the speaker missed it, so it is their absence.
_MISSED = frozenset({"verpasst", "versaeumt"})
_BARE_WAR_WORDS = frozenset({
    "was", "war", "heute", "gestern", "letzte", "vergangene", "nacht", "seit", "uhr", "morgen", "denn",
    "eigentlich", "so", "alles", "hier", "zuhause", "zu", "hause", "im", "haus", "diese", "woche",
    "waehrend", "ich", "weg", "als", "unterwegs", "meiner", "abwesenheit", "nicht", "da", "fort", "solange",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18",
    "19", "20", "21", "22", "23",
})
_ALARM_CLASSES = frozenset({"smoke", "moisture", "gas", "carbon_monoxide", "safety", "tamper", "problem"})
_ENTRY_CLASSES = frozenset({"door", "garage_door", "opening"})
_WINDOW_CLASSES = frozenset({"window"})
_MOTION_CLASSES = frozenset({"motion", "occupancy", "presence"})
_ACCESS_COVER_CLASSES = frozenset({"garage", "gate", "door"})
_FINISHED = frozenset({
    "finished", "finish", "done", "complete", "completed", "end", "ended", "fertig", "beendet",
    "programmende", "program_finished",
})
_PRIORITY = {"alarm": 0, "monitor": 1, "access": 2, "motion": 3, "appliance": 4, "homeintent": 5, "person": 6}


@dataclass(frozen=True)
class SummaryQuery:
    away: bool
    start: datetime | None
    end: datetime | None
    label: str


@dataclass(frozen=True)
class SummaryEvent:
    kind: str
    at: datetime
    text: str  # spoken, without the time
    timed: bool = True  # False: the text carries its own times ("du warst …")

    @property
    def priority(self) -> int:
        return _PRIORITY.get(self.kind, 9)


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _contains(words: Sequence[str], phrase: Sequence[str]) -> bool:
    return any(tuple(words[index:index + len(phrase)]) == tuple(phrase) for index in range(len(words)))


def _absence_clause(words: Sequence[str]) -> bool:
    """"während/als/seit ich weg war", "seit ich gegangen bin", "als ich
    nicht da war", "in meiner Abwesenheit"."""
    for index, word in enumerate(words):
        if word in {"meiner", "meine"} and index + 1 < len(words) and words[index + 1] in _ABSENCE_NOUNS:
            return True
        if word not in _SUBORDINATORS or index + 1 >= len(words) or words[index + 1] != "ich":
            continue
        clause = words[index + 2:index + 7]
        if set(clause) & _AWAY_PREDICATES:
            return True
        if any(a == "nicht" and b in {"da", "zuhause", "daheim", "hier", "zu"} for a, b in zip(clause, clause[1:])):
            return True
        if _contains(clause, ("aus", "dem", "haus")):
            return True
    return False


def parse_summary_query(text: str, now: datetime) -> SummaryQuery | None:
    """"Was war los, während ich weg war?" and periods, else ``None``."""
    words = _words(text)
    present = set(words)
    predicates = present & _HAPPEN_PREDICATES
    if predicates == {"war"} and not present <= _BARE_WAR_WORDS:
        # "Was war die Temperatur gestern?" is a history question; only a
        # bare "Was war (letzte Nacht)?" asks what happened.
        predicates = set()
    asks = bool(present & {"was", "gibt"}) and bool(predicates)
    if not asks and "zusammenfassung" not in present:
        return None
    period = spoken_period(words, now)
    if _absence_clause(words) or (period is None and present & _MISSED and present & {"ich", "hab", "habe"}):
        return SummaryQuery(True, None, None, "während du weg warst")
    if period is None:
        return None
    return SummaryQuery(False, period[0], period[1], period[2])


def last_absence(
    rows: Sequence[tuple[datetime, str]], now: datetime
) -> tuple[datetime, datetime] | None:
    """The last away interval of a person: (left, came back), or (left, now)
    while still away. A history that starts away counts from its start."""
    left: datetime | None = None
    result: tuple[datetime, datetime] | None = None
    previous: str | None = None
    for moment, state in sorted(rows, key=lambda item: item[0]):
        if state in {"unknown", "unavailable"}:
            continue
        if state != "home" and previous in {None, "home"}:
            left = moment
        elif state == "home" and previous is not None and previous != "home" and left is not None:
            result = (left, moment)
            left = None
        previous = state
    if left is not None and previous != "home":
        return left, now
    return result


def _clock(moment: datetime) -> str:
    return f"{moment:%H:%M}"


def _name(entity: EntitySnapshot | None, entity_id: str) -> str:
    return entity.friendly_name if entity is not None else entity_id


def _counts(entity: EntitySnapshot | None, away: bool) -> bool:
    """Whether a binary sensor can yield an event in this kind of period."""
    device_class = entity.device_class if entity is not None else None
    if device_class in _ALARM_CLASSES | _ENTRY_CLASSES:
        return True
    return away and device_class in _WINDOW_CLASSES | _MOTION_CLASSES


def summarize(
    history: Mapping[str, Sequence[tuple[datetime, str]]],
    entities: Sequence[EntitySnapshot],
    start: datetime,
    end: datetime,
    *,
    away: bool,
    speaker_person: str | None,
    speaker_is_admin: bool,
    automation_runs: Sequence[tuple[datetime, str]] = (),
    executions: Sequence[tuple[datetime, str]] = (),
    person_names: Mapping[str, str] | None = None,
) -> list[SummaryEvent]:
    """Every notable event in [start, end], most important first."""
    by_id = {entity.entity_id: entity for entity in entities}
    events: list[SummaryEvent] = []
    own_moves: list[tuple[datetime, bool]] = []
    for entity_id, rows in history.items():
        entity = by_id.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        if domain == "binary_sensor" and not _counts(entity, away):
            continue  # never an event in this period (A4: no needless work)
        ordered = sorted(rows, key=lambda item: item[0])
        found = 0
        for (_, before), (moment, state) in zip(ordered, ordered[1:]):
            if not (start <= moment <= end) or state == before:
                continue
            if found >= MAX_EVENTS:
                break  # later events of this entity can never be spoken
            found += 1
            name = _name(entity, entity_id)
            device_class = entity.device_class if entity is not None else None
            if domain == "binary_sensor" and state == "on" and before == "off":
                if device_class in _ALARM_CLASSES:
                    events.append(SummaryEvent("alarm", moment, f"{name} hat ausgelöst"))
                elif device_class in _ENTRY_CLASSES:
                    events.append(SummaryEvent("access", moment, f"{name} wurde geöffnet"))
                elif away and device_class in _WINDOW_CLASSES:
                    events.append(SummaryEvent("access", moment, f"{name} wurde geöffnet"))
                elif away and device_class in _MOTION_CLASSES:
                    events.append(SummaryEvent("motion", moment, f"{name} hat Bewegung erkannt"))
            elif domain == "cover" and state in {"open", "opening"} and before in {"closed", "closing"} and (
                device_class in _ACCESS_COVER_CLASSES
            ):
                events.append(SummaryEvent("access", moment, f"{name} wurde geöffnet"))
            elif domain == "lock" and state == "unlocked" and before == "locked":
                events.append(SummaryEvent("access", moment, f"{name} wurde aufgeschlossen"))
            elif domain == "alarm_control_panel" and state == "triggered":
                events.append(SummaryEvent("alarm", moment, f"{name} hat Alarm ausgelöst"))
            elif domain == "sensor" and normalize_for_compare(state) in _FINISHED and (
                normalize_for_compare(before) not in _FINISHED
            ):
                label = name.replace(" Status", "").strip()
                events.append(SummaryEvent("appliance", moment, f"{label} ist fertig"))
            elif domain == "person" and {state, before} & {"home"} and before not in {"unknown", "unavailable"}:
                arrived = state == "home"
                own = entity_id == speaker_person
                if own:
                    if not away:  # the speaker's own absence is the period itself
                        own_moves.append((moment, arrived))
                    continue
                who = (person_names or {}).get(entity_id) or _name(entity, entity_id)
                if speaker_is_admin:
                    text = f"{who} ist heimgekommen" if arrived else f"{who} ist gegangen"
                else:
                    text = "jemand ist heimgekommen" if arrived else "jemand ist gegangen"
                events.append(SummaryEvent("person", moment, text))
    for moment, name in automation_runs:
        if start <= moment <= end:
            events.append(SummaryEvent("monitor", moment, f"„{name}“ hat ausgelöst"))
    for moment, text in executions:
        if start <= moment <= end:
            events.append(SummaryEvent("homeintent", moment, text))
    own = _own_moves(own_moves)
    if own is not None:
        events.append(own)
    # Most important first, within the same importance in time order (A4).
    return sorted(events, key=lambda event: (event.priority, event.at, event.text))[:MAX_EVENTS]


def _own_moves(moves: Sequence[tuple[datetime, bool]]) -> SummaryEvent | None:
    """The speaker's own coming and going, once and short (7.9.3 A4)."""
    if not moves:
        return None
    ordered = sorted(moves)
    left = [moment for moment, arrived in ordered if not arrived]
    came = [moment for moment, arrived in ordered if arrived]
    first = ordered[0][0]
    if len(left) == 1 and len(came) == 1 and left[0] < came[0]:
        text = f"du warst von {_clock(left[0])} bis {_clock(came[0])} Uhr weg"
    elif len(moves) == 1:
        text = f"du bist um {_clock(first)} Uhr {'heimgekommen' if came else 'gegangen'}"
    else:
        text = f"du bist {len(left)}-mal gegangen und {len(came)}-mal heimgekommen"
    return SummaryEvent("person", first, text, timed=False)


_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def day_label(moment: datetime, today: Any = None) -> str:
    """"" for today, "gestern", else "am Mo, 05.10." (7.9.3 A4)."""
    if today is None or moment.date() == today:
        return ""
    if (today - moment.date()).days == 1:
        return "gestern"
    return f"am {_WEEKDAYS[moment.weekday()]}, {moment:%d.%m.}"


def _entry(event: SummaryEvent, today: Any) -> str:
    day = day_label(event.at, today)
    if not event.timed:
        return f"{day} {event.text}".strip()
    return f"{day} um {_clock(event.at)} {event.text}".strip()


def period_label(start: datetime, end: datetime, today: Any) -> str:
    """"14:00 bis 18:00 Uhr", over several days with date and time."""
    if start.date() == end.date() == today:
        return f"{_clock(start)} bis {_clock(end)} Uhr"
    if start.date() == end.date():
        return f"{_WEEKDAYS[start.weekday()]}, {start:%d.%m.}, {_clock(start)} bis {_clock(end)} Uhr"
    return (
        f"{_WEEKDAYS[start.weekday()]}, {start:%d.%m.} {_clock(start)} Uhr bis "
        f"{_WEEKDAYS[end.weekday()]}, {end:%d.%m.} {_clock(end)} Uhr"
    )


def render_summary(
    events: Sequence[SummaryEvent], label: str, today: Any = None
) -> tuple[str, tuple[SummaryEvent, ...]]:
    """The spoken summary and the events left for "Was noch?".  ``today``
    (a date) adds the day to every entry of another day."""
    if not events:
        return f"{label[:1].upper()}{label[1:]} ist nichts Auffälliges passiert.", ()
    spoken = events[:SPOKEN_LIMIT]
    rest = tuple(events[SPOKEN_LIMIT:])
    parts = [_entry(event, today) for event in spoken]
    head = f"{label[:1].upper()}{label[1:]}: " + "; ".join(parts) + "."
    if rest:
        head += f" Dazu kommen {len(rest)} weitere Ereignisse – frag „Was noch?“."
    return head, rest


def render_rest(rest: Sequence[SummaryEvent], today: Any = None) -> str:
    parts = [_entry(event, today) for event in rest[:SPOKEN_LIMIT]]
    text = "Außerdem: " + "; ".join(parts) + "."
    if len(rest) > SPOKEN_LIMIT:
        text += f" Und {len(rest) - SPOKEN_LIMIT} weitere."
    return text


# --- Home Assistant side ------------------------------------------------------

_HISTORY_DOMAINS = frozenset({"binary_sensor", "cover", "lock", "alarm_control_panel", "sensor", "person"})


def _relevant(entity: EntitySnapshot) -> bool:
    """Only classes that can yield a summary event (7.9.3 A4): doors,
    windows, gates, detectors, motion, locks, alarm panels, persons and
    appliance status sensors (a text state without unit or class)."""
    if entity.domain == "binary_sensor":
        return entity.device_class in _ALARM_CLASSES | _ENTRY_CLASSES | _WINDOW_CLASSES | _MOTION_CLASSES
    if entity.domain == "cover":
        return entity.device_class in _ACCESS_COVER_CLASSES
    if entity.domain == "sensor":
        return entity.device_class is None and not entity.unit and entity.state_class is None
    return entity.domain in _HISTORY_DOMAINS


def history_entities(entities: Sequence[EntitySnapshot], hass: Any) -> list[str]:
    """The entities whose history can contain a summary event, at most
    ``MAX_HISTORY_ENTITIES`` (detectors and accesses first)."""
    rank = {"alarm_control_panel": 0, "lock": 1, "cover": 2, "person": 3, "binary_sensor": 4, "sensor": 5}
    chosen = sorted(
        (entity for entity in entities if entity.domain in _HISTORY_DOMAINS and _relevant(entity)),
        key=lambda entity: (rank.get(entity.domain, 9), entity.device_class not in _ALARM_CLASSES, entity.entity_id),
    )
    ids = {entity.entity_id for entity in chosen[:MAX_HISTORY_ENTITIES]}
    ids.update(state.entity_id for state in states_of(hass, "person"))
    return sorted(ids)


def states_of(hass: Any, domain: str) -> list[Any]:
    """Every state object of one domain (``hass.states.async_all``)."""
    states = getattr(hass, "states", None)
    if states is None or not hasattr(states, "async_all"):
        return []
    return [state for state in states.async_all() if state.entity_id.split(".", 1)[0] == domain]


def window_start(now: datetime, days: int = MAX_SUMMARY_DAYS) -> datetime:
    return now - timedelta(days=days)
