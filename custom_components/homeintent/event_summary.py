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

# "Was war los / ist passiert / hat sich getan / habe ich verpasst …?"
_HAPPEN_PREDICATES = frozenset({
    "los", "passiert", "geschehen", "getan", "verpasst", "vorgefallen", "neuigkeiten", "neues", "gab", "war",
})
_AWAY_PHRASES = (
    ("waehrend", "ich", "weg", "war"), ("als", "ich", "weg", "war"), ("waehrend", "ich", "unterwegs", "war"),
    ("seit", "ich", "weg", "bin"), ("in", "meiner", "abwesenheit"), ("waehrend", "meiner", "abwesenheit"),
    ("waehrend", "ich", "nicht", "da", "war"), ("seit", "ich", "gegangen", "bin"),
)
_BARE_WAR_WORDS = frozenset({
    "was", "war", "heute", "gestern", "letzte", "vergangene", "nacht", "seit", "uhr", "morgen", "denn",
    "eigentlich", "so", "alles", "hier", "zuhause", "zu", "hause", "im", "haus", "diese", "woche",
    "waehrend", "ich", "weg", "als", "unterwegs", "meiner", "abwesenheit", "nicht", "da",
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

    @property
    def priority(self) -> int:
        return _PRIORITY.get(self.kind, 9)


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def _contains(words: Sequence[str], phrase: Sequence[str]) -> bool:
    return any(tuple(words[index:index + len(phrase)]) == tuple(phrase) for index in range(len(words)))


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
    if any(_contains(words, phrase) for phrase in _AWAY_PHRASES):
        return SummaryQuery(True, None, None, "während du weg warst")
    period = spoken_period(words, now)
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
    for entity_id, rows in history.items():
        entity = by_id.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        ordered = sorted(rows, key=lambda item: item[0])
        for (_, before), (moment, state) in zip(ordered, ordered[1:]):
            if not (start <= moment <= end) or state == before:
                continue
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
                if own and away:
                    continue  # the speaker's own absence is the period itself
                who = (person_names or {}).get(entity_id) or _name(entity, entity_id)
                if own:
                    text = "du bist heimgekommen" if arrived else "du bist gegangen"
                elif speaker_is_admin:
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
    return sorted(events, key=lambda event: (event.priority, event.at))


def render_summary(events: Sequence[SummaryEvent], label: str) -> tuple[str, tuple[SummaryEvent, ...]]:
    """The spoken summary and the events left for "Was noch?"."""
    if not events:
        return f"{label[:1].upper()}{label[1:]} ist nichts Auffälliges passiert.", ()
    spoken = events[:SPOKEN_LIMIT]
    rest = tuple(events[SPOKEN_LIMIT:])
    parts = [f"um {_clock(event.at)} {event.text}" for event in spoken]
    head = f"{label[:1].upper()}{label[1:]}: " + "; ".join(parts) + "."
    if rest:
        head += f" Dazu kommen {len(rest)} weitere Ereignisse – frag „Was noch?“."
    return head, rest


def render_rest(rest: Sequence[SummaryEvent]) -> str:
    parts = [f"um {_clock(event.at)} {event.text}" for event in rest[:SPOKEN_LIMIT]]
    text = "Außerdem: " + "; ".join(parts) + "."
    if len(rest) > SPOKEN_LIMIT:
        text += f" Und {len(rest) - SPOKEN_LIMIT} weitere."
    return text


# --- Home Assistant side ------------------------------------------------------

_HISTORY_DOMAINS = frozenset({"binary_sensor", "cover", "lock", "alarm_control_panel", "sensor", "person"})


def history_entities(entities: Sequence[EntitySnapshot], hass: Any) -> list[str]:
    """The entities whose history can contain a summary event."""
    ids = [
        entity.entity_id for entity in entities
        if entity.domain in _HISTORY_DOMAINS
        and (entity.domain != "sensor" or (entity.device_class is None and not entity.unit))
    ]
    ids.extend(state.entity_id for state in states_of(hass, "person"))
    return sorted(set(ids))


def states_of(hass: Any, domain: str) -> list[Any]:
    """Every state object of one domain (``hass.states.async_all``)."""
    states = getattr(hass, "states", None)
    if states is None or not hasattr(states, "async_all"):
        return []
    return [state for state in states.async_all() if state.entity_id.split(".", 1)[0] == domain]


def window_start(now: datetime, days: int = 7) -> datetime:
    return now - timedelta(days=days)
