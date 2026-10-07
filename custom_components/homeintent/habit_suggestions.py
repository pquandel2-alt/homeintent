"""Suggestions from habits (7.9.2 B2) - statistics, no learning model.

What HomeIntent recognized before 7.9.2 (``habit_discovery``,
``situation.RoutineStatistics``, ``situation_detection.habit_signal``):
repeated *GoalRun sequences* become routine drafts in the Learning Center
(opt-in), per-entity hour/weekday counts flag anomalies, and the first step
of a learned habit can trigger a proactive hint. None of them proposes an
*automation* for something a person keeps doing by voice at the same time.

This module adds exactly that, on the same deterministic footing:

* observations are the person's own executed commands from HomeIntent's
  persisted execution trace (``user_present``, executed, same actor);
* an effect is the same service on the same targets ("light.turn_off" on
  the ground floor lights);
* a habit is an effect on at least ``MIN_DAYS`` of the last ``WINDOW_DAYS``
  days, every time within ``TIME_TOLERANCE_MINUTES`` of the median time; if
  all those days are working days it is "an Werktagen", else "täglich";
* never proposed: accesses (gates, doors, locks, main/gas valves), alarm
  panels and genus-critical devices.

A suggestion is offered at most once per pattern, only to the person who
did it, never while another question is open; "Ja" leads to the ordinary
automation preview with its own "Ja". "Schlag mir nichts mehr vor" switches
it off; "… per Push" sends new suggestions as push instead.

Home-Assistant-free and deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .entities import EntitySnapshot, normalize_for_compare

__all__ = (
    "MIN_DAYS",
    "TIME_TOLERANCE_MINUTES",
    "WINDOW_DAYS",
    "HabitObservation",
    "HabitStore",
    "TimedHabit",
    "describe_habit",
    "discover_habits",
    "observations_from_trace",
    "parse_habit_request",
)

# "an mindestens 4 von 7 Tagen" - documented constants (B2).
MIN_DAYS = 4
WINDOW_DAYS = 7
TIME_TOLERANCE_MINUTES = 45
_WORKDAYS = frozenset({0, 1, 2, 3, 4})
_NEVER_DOMAINS = frozenset({"lock", "alarm_control_panel", "camera", "script", "scene", "button", "notify"})


@dataclass(frozen=True)
class HabitObservation:
    actor: str
    at: datetime
    domain: str
    service: str
    targets: tuple[str, ...]

    @property
    def effect(self) -> tuple[str, str, tuple[str, ...]]:
        return self.domain, self.service, self.targets


@dataclass(frozen=True)
class TimedHabit:
    habit_id: str
    actor: str
    domain: str
    service: str
    targets: tuple[str, ...]
    hour: int
    minute: int
    workdays_only: bool
    days: int  # on how many of the window's days


def observations_from_trace(records: Iterable[Any]) -> list[HabitObservation]:
    """User-triggered, executed commands from the execution trace."""
    found: list[HabitObservation] = []
    for record in records:
        if not getattr(record, "user_present", False) or not getattr(record, "executed", True):
            continue
        actor = getattr(record, "actor", "voice")
        if actor == "voice" or getattr(record, "origin", None) not in {None, "explicit_command"}:
            continue
        for plan in getattr(record, "plans", ()):
            head, _, ids = plan.partition(" ")
            domain, _, service = head.partition(".")
            targets = tuple(sorted(item.strip() for item in ids.split(",") if item.strip()))
            if domain and service and targets:
                found.append(HabitObservation(actor, record.time, domain, service, targets))
    return found


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _allowed(targets: Sequence[str], entities: Mapping[str, EntitySnapshot]) -> bool:
    from .nlu.automation_access import access_kind
    from .nlu.device_ontology import entity_genera, genus

    for entity_id in targets:
        domain = entity_id.split(".", 1)[0]
        if domain in _NEVER_DOMAINS:
            return False
        entity = entities.get(entity_id)
        if access_kind(entity, entity_id) is not None:
            return False
        if entity is not None and any(genus(key).critical for key in entity_genera(entity)):
            return False
    return True


def discover_habits(
    observations: Sequence[HabitObservation],
    now: datetime,
    entities: Sequence[EntitySnapshot],
) -> list[TimedHabit]:
    """Every habit in the last ``WINDOW_DAYS`` days, most frequent first."""
    by_id = {entity.entity_id: entity for entity in entities}
    since = now - timedelta(days=WINDOW_DAYS)
    groups: dict[tuple[str, str, str, tuple[str, ...]], list[HabitObservation]] = {}
    for item in observations:
        if since <= item.at <= now:
            groups.setdefault((item.actor, *item.effect), []).append(item)
    habits: list[TimedHabit] = []
    for (actor, domain, service, targets), items in groups.items():
        per_day: dict[str, HabitObservation] = {}
        for item in sorted(items, key=lambda value: value.at):
            per_day.setdefault(item.at.date().isoformat(), item)
        if len(per_day) < MIN_DAYS or not _allowed(targets, by_id):
            continue
        center = int(median(_minutes(item.at) for item in per_day.values()))
        if any(abs(_minutes(item.at) - center) > TIME_TOLERANCE_MINUTES for item in per_day.values()):
            continue
        rounded = int(round(center / 5) * 5) % (24 * 60)
        workdays = all(item.at.weekday() in _WORKDAYS for item in per_day.values())
        raw = repr((actor, domain, service, targets, rounded // 30, workdays)).encode()
        habits.append(TimedHabit(
            "habit-" + hashlib.sha256(raw).hexdigest()[:16], actor, domain, service, targets,
            rounded // 60, rounded % 60, workdays, len(per_day),
        ))
    return sorted(habits, key=lambda habit: (-habit.days, habit.habit_id))


_VERBS = {
    "turn_off": "aus", "turn_on": "ein", "close_cover": "runter", "open_cover": "hoch",
}


def describe_habit(habit: TimedHabit, entities: Sequence[EntitySnapshot]) -> str:
    """"Du schaltest an Werktagen gegen 22:30 Uhr die Lichter im Erdgeschoss
    aus." - from the targets as the registry names them."""
    by_id = {entity.entity_id: entity for entity in entities}
    members = [by_id[item] for item in habit.targets if item in by_id]
    when = "an Werktagen" if habit.workdays_only else "jeden Tag"
    clock = f"gegen {habit.hour}:{habit.minute:02d} Uhr"
    floors = {item.floor_name for item in members}
    if members and all(item.domain == "light" for item in members) and len(members) > 1 and len(floors) == 1 and None not in floors:
        floor = next(iter(floors))
        what = f"die Lichter im {floor}"
    elif len(members) == 1:
        what = members[0].friendly_name
    else:
        names = [item.friendly_name for item in members] or list(habit.targets)
        what = ", ".join(names[:-1]) + " und " + names[-1] if len(names) > 1 else names[0]
    verb = "fährst" if habit.domain == "cover" else "schaltest"
    particle = _VERBS.get(habit.service, "")
    return f"Du {verb} {when} {clock} {what} {particle}".rstrip() + "."


# --- per-user preferences and shown patterns ----------------------------------

@dataclass
class HabitStore:
    """Which patterns were offered or rejected, and each user's choice of
    channel ("voice", "push", "off"). A small JSON file.

    ``load`` and ``save`` touch the disk and run in the executor; every
    other method works on the loaded data in memory and marks it dirty."""

    path: str
    data: dict[str, Any] | None = None
    dirty: bool = False

    def load(self) -> None:
        """Read the file (executor)."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.data = loaded if isinstance(loaded, dict) else {}
        except (FileNotFoundError, ValueError):
            self.data = {}

    def save(self) -> None:
        """Write the file atomically (executor)."""
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".habits-")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(self._data(), stream, ensure_ascii=False, indent=1)
        os.replace(temp, self.path)
        self.dirty = False

    def _data(self) -> dict[str, Any]:
        if self.data is None:
            self.data = {}
        return self.data

    def mode(self, actor: str) -> str:
        return str(self._data().get("modes", {}).get(actor, "voice"))

    def set_mode(self, actor: str, mode: str) -> None:
        self._data().setdefault("modes", {})[actor] = mode
        self.dirty = True

    def offered(self, habit_id: str) -> bool:
        return habit_id in self._data().get("offered", {})

    def mark(self, habit_id: str, outcome: str) -> None:
        self._data().setdefault("offered", {})[habit_id] = outcome
        self.dirty = True


# --- language ------------------------------------------------------------------

def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def parse_habit_request(text: str) -> str | None:
    """"list", "suggest", "off", "push", "voice" - else ``None``."""
    words = _words(text)
    present = set(words)
    if not words:
        return None
    if present & {"gewohnheiten", "gewohnheit"} and present & {"welche", "erkannt", "kennst", "hast", "zeig"}:
        return "list"
    if present & {"vorschlaege", "vorschlag", "vorschlagen", "vor"} and (
        present & {"nichts", "keine", "nicht", "nie"} and present & {"mehr", "schlag", "schlage", "vor"}
    ):
        return "off"
    if present & {"vorschlaege", "vorschlag"} and "push" in present:
        return "push"
    if present & {"vorschlaege", "vorschlag"} and present & {"wieder", "sprache"} and present & {"mach", "schlag", "gib"}:
        return "voice"
    if present & {"vorschlaege", "vorschlag"} and present & {"automationen", "automation", "hast", "gibt", "welche"}:
        return "suggest"
    return None
