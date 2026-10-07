"""Vacation mode (7.9.2 B4): an absence profile with an end date.

"Ich bin bis Sonntag weg", "Wir fahren bis zum 20. in den Urlaub",
"Urlaubsmodus bis Freitag" - and "Urlaub vorbei", "Wir sind zurück", or
automatically at the end date. "Was macht der Urlaubsmodus gerade?"
answers the state.

What it does, each part listed in the preview, all after one "Ja":

1. stricter monitoring: every door/window opening and every motion in the
   house is pushed at once to the confirmed household - the existing
   monitor types, bounded by the end date;
2. optional ("… und simuliere Anwesenheit"): lights switch on and off at
   the household's *usual* times (``habit_suggestions`` over the last
   weeks), each day shifted by a small deterministic offset derived from
   date and entity id - only lights; without enough history the preview
   says so and uses fixed evening times instead;
3. an existing vacation helper (``input_boolean`` named Urlaub/Abwesenheit)
   is switched on - only if there is exactly one - and off at the end.

Everything it creates is recorded in a small store and removed completely
at the end (the end automation deletes every vacation automation and
itself; "Urlaub vorbei" does the same through preview and "Ja").
Never heating, gates, doors or locks: the generated actions are only
``notify.send_message``, ``light.turn_on/off``, ``input_boolean.turn_off``
and ``homeintent.delete_automation`` (checked by ``validate_plan``).

Home-Assistant-free; dates are local.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from .entities import EntitySnapshot, normalize_for_compare

__all__ = (
    "ALLOWED_ACTIONS",
    "MAX_DAYS",
    "VacationPlan",
    "VacationRecord",
    "VacationStore",
    "build_plan",
    "parse_vacation_request",
    "plan_configs",
    "validate_plan",
)

MAX_DAYS = 30
JITTER_MINUTES = 15
FIXED_SIMULATION = ((19, 0), (22, 30))  # offered when no habit history exists
ALLOWED_ACTIONS = frozenset({
    "notify.send_message", "light.turn_on", "light.turn_off", "input_boolean.turn_off",
    "homeintent.delete_automation",
})

_WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6,
}
_MONTHS = {
    "januar": 1, "februar": 2, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
    "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_HELPER_WORDS = ("urlaub", "abwesen", "vacation", "holiday", "verreist")
_ENTRY_CLASSES = frozenset({"door", "window", "garage_door", "opening"})
_MOTION_CLASSES = frozenset({"motion", "occupancy", "presence"})


def _words(text: str) -> list[str]:
    """Words; a dot survives only inside a date ("20.", "20.10.")."""
    folded = normalize_for_compare(text)
    words = "".join(char if char.isalnum() or char == "." else " " for char in folded).split()
    return [
        (word.rstrip(".") + "." if word.endswith(".") else word) if word[:1].isdigit() else word.strip(".")
        for word in words if word.strip(".")
    ]


# --- language ------------------------------------------------------------------

@dataclass(frozen=True)
class VacationRequest:
    action: str  # "start" | "end" | "status"
    end: date | None = None
    end_spoken: str = ""
    simulate: bool = False


def _end_date(words: list[str], today: date) -> tuple[date, str] | None:
    """"bis Sonntag", "bis zum 20.", "bis 20.10.", "bis zum 20. Oktober", "bis morgen"."""
    if "bis" not in words:
        return None
    rest = words[words.index("bis") + 1:]
    rest = [word for word in rest if word not in {"zum", "zu", "einschliesslich", "inklusive", "am", "naechsten", "kommenden"}]
    if not rest:
        return None
    head = rest[0]
    if head == "morgen":
        return today + timedelta(days=1), "morgen"
    if head == "uebermorgen":
        return today + timedelta(days=2), "übermorgen"
    if head in _WEEKDAYS:
        delta = (_WEEKDAYS[head] - today.weekday()) % 7 or 7
        return today + timedelta(days=delta), head.capitalize()
    match = re.fullmatch(r"(\d{1,2})\.(?:(\d{1,2})\.?(\d{2,4})?)?", head)
    if match is None:
        return None
    day = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    year = int(match.group(3)) if match.group(3) else None
    if month is None and len(rest) > 1 and rest[1] in _MONTHS:
        month = _MONTHS[rest[1]]
    if year is not None and year < 100:
        year += 2000
    if month is None:
        month, year_guess = today.month, today.year
        if day < today.day:
            month += 1
            if month > 12:
                month, year_guess = 1, year_guess + 1
        year = year or year_guess
    year = year or (today.year if (month, day) >= (today.month, today.day) else today.year + 1)
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    return found, f"{found.day}.{found.month}."


_TRAVEL_WORDS = frozenset({
    "urlaub", "ferien", "verreist", "weg", "unterwegs", "fahren", "fahre", "fliegen", "fliege", "reisen", "reise",
})
# What a vacation, a trip or an absence is called (7.9.3 A3).
_SUBJECT_STEMS = ("urlaub", "ferien", "reise", "abwesenheit")
# Ending it: "vorbei", "zu Ende", "beenden", "aus", "abschalten" …
_END_WORDS = frozenset({
    "vorbei", "beendet", "beende", "beenden", "aus", "ausschalten", "abschalten", "deaktiviere",
    "deaktivieren", "deaktiviert", "stoppe", "stoppen",
})
# Coming back: "zurück", "heim", "angekommen", "wieder da/zuhause/daheim/hier".
_RETURN_WORDS = frozenset({
    "zurueck", "heim", "heimgekommen", "heimgekehrt", "angekommen", "zurueckgekommen", "zurueckgekehrt",
    "wiederda",
})
_PLACE_AFTER_WIEDER = frozenset({"da", "zuhause", "daheim", "hier", "zurueck", "heim"})
# A program, mode or preset whose *value* is "Urlaub" (7.9.3 A2).
_SETTING_NOUNS = frozenset({
    "modus", "programm", "preset", "betriebsart", "profil", "voreinstellung", "einstellung", "betriebsmodus",
})
_VALUE_WORDS = ("urlaub", "ferien", "abwesen")
# A statement about people: first person with being, travelling or leaving.
_PERSON_VERBS = frozenset({
    "bin", "sind", "fahren", "fahre", "fliegen", "fliege", "gehen", "gehe", "reisen", "reise", "verreisen",
    "verreise", "machen", "mache", "waren", "war", "kommen", "komme",
})


def _subject_named(words: Sequence[str]) -> bool:
    return any(word.startswith(_SUBJECT_STEMS) for word in words) or "verreist" in words


def _person_statement(words: Sequence[str]) -> bool:
    """"Ich bin / wir sind / wir fahren …": a statement about people."""
    present = set(words)
    return bool(present & {"ich", "wir"}) and bool(present & _PERSON_VERBS)


def device_value_reading(text: str) -> bool:
    """"Stell das Heizprogramm auf Urlaub", "Modus Urlaub", "Preset
    Abwesend": the word is the *value* of a device setting, never the
    vacation mode (7.9.3 A2).  A statement about people ("Wir sind bis
    Sonntag auf Urlaub") stays the vacation reading."""
    words = _words(text)
    if _person_statement(words):
        return False
    for index, word in enumerate(words):
        if not word.startswith(_VALUE_WORDS) or word.startswith("urlaubsmodus"):
            continue
        before = words[index - 1] if index else ""
        after = words[index + 1] if index + 1 < len(words) else ""
        if before == "auf" or before in _SETTING_NOUNS or before.endswith(tuple(_SETTING_NOUNS)):
            return True
        if after in _SETTING_NOUNS and not (word == "urlaubs" and after == "modus"):
            return True
    return False


def _returns(words: Sequence[str]) -> bool:
    """Coming back: "zurück", "heimgekommen", "wieder da/zuhause"."""
    words = " ".join(words).replace("zu hause", "zuhause").split()
    glued = [a + b if a == "wieder" and b in _PLACE_AFTER_WIEDER else a for a, b in zip(words, [*words[1:], ""])]
    present = set(glued)
    if present & _RETURN_WORDS - {"wiederda"}:
        return True
    return any(word.startswith("wieder") and word[len("wieder"):] in _PLACE_AFTER_WIEDER for word in glued)


def _ends(words: Sequence[str]) -> bool:
    """Ending: "vorbei", "zu Ende", "beenden", "aus", "abschalten", "ist um"."""
    present = set(words)
    return bool(present & _END_WORDS) or {"zu", "ende"} <= present or (
        bool(present & {"um", "rum"}) and "ist" in present
    )


def parse_vacation_request(text: str, today: date) -> VacationRequest | None:
    words = _words(text)
    present = set(words)
    if not words:
        return None
    for index, word in enumerate(words):
        # A negation is respected ("Schalte den Urlaubsmodus nicht ein");
        # "nicht da/zuhause" is the absence itself.
        if word in {"nicht", "kein", "keinen", "keine"} and (
            index + 1 >= len(words) or words[index + 1] not in {"da", "zuhause", "daheim", "zu"}
        ):
            return None
    if device_value_reading(text):
        return None  # "Stell das Heizprogramm auf Urlaub" is a device command (A2)
    if present & {"wenn", "sobald", "falls"}:
        return None  # a condition, never a statement
    away_words = present | ({"weg"} if any(
        a == "nicht" and b in {"da", "zuhause", "daheim"} for a, b in zip(words, words[1:])
    ) else set())
    vacation = bool(present & {"urlaub", "urlaubsmodus", "ferien", "verreist"}) or any(
        word.startswith("urlaub") for word in words
    )
    subject = vacation or _subject_named(words)
    if vacation and present & {"was", "wie", "ist", "laeuft"} and present & {"macht", "an", "aktiv", "laeuft", "lange", "stand"}:
        if not present & {"bis"} and not _ends(words):
            return VacationRequest("status")
    question = bool(present & {"was", "wie", "welche", "warst", "wann", "wo", "wer"})
    if subject and not question and "bis" not in present and (_ends(words) or _returns(words)):
        # "Urlaub vorbei", "Die Reise ist zu Ende", "Wir sind aus dem
        # Urlaub zurück", "Beende die Abwesenheit" (A3).
        return VacationRequest("end")
    if not question and "bis" not in present and _returns(words) and (
        present & {"ich", "wir"} or words[:2] == ["wieder", "da"]
    ):
        # "Wir sind wieder da", "Ich bin wieder zuhause": a return; ends a
        # running vacation mode only after its own question (A3).
        return VacationRequest("return")
    away = vacation or (
        away_words & {"weg", "verreist", "unterwegs"} and present & {"bin", "sind"} and "bis" in present
    )
    if not away:
        return None
    if question or present & {"war"}:
        return None
    found = _end_date(words, today)
    simulate = bool(present & {"simuliere", "simulier", "anwesenheitssimulation", "simulation"}) or (
        "anwesenheit" in present and bool(present & {"simuliere", "simulier", "vortaeuschen", "vortaeusche"})
    )
    helper_named = "urlaubsmodus" in present or {"urlaubs", "modus"} <= present
    if found is None and not simulate and helper_named and not present & _TRAVEL_WORDS:
        # "Schalte den Urlaubsmodus ein" names a helper and stays the
        # device command it always was; the profile needs an end date or
        # travel words ("Wir fahren in den Urlaub").
        return None
    if found is None:
        return VacationRequest("start", None, "", simulate)
    return VacationRequest("start", found[0], found[1], simulate)


def names_helper(text: str) -> bool:
    """"Schalte den Urlaubsmodus aus" names the helper device."""
    words = _words(text)
    return "urlaubsmodus" in words or ("urlaubs" in words and "modus" in words)


# --- plan ------------------------------------------------------------------------

@dataclass(frozen=True)
class SimulatedLight:
    entity_id: str
    name: str
    on: tuple[int, int]
    off: tuple[int, int]


@dataclass(frozen=True)
class VacationPlan:
    end: datetime
    watched: tuple[str, ...]
    lights: tuple[SimulatedLight, ...]
    simulation_from_history: bool
    helper: str | None
    helper_name: str | None
    recipients: tuple[str, ...]


def vacation_helpers(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [
        entity for entity in entities
        if entity.domain == "input_boolean"
        and any(word in normalize_for_compare(f"{entity.friendly_name} {entity.entity_id}") for word in _HELPER_WORDS)
    ]


def _usual_light_times(habits: Sequence[Any], entities: Mapping[str, EntitySnapshot]) -> list[SimulatedLight]:
    """On/off times of lights from the household's habits (B2 data)."""
    ons: dict[str, tuple[int, int]] = {}
    offs: dict[str, tuple[int, int]] = {}
    for habit in habits:
        for target in habit.targets:
            if not target.startswith("light."):
                continue
            if habit.service == "turn_on":
                ons.setdefault(target, (habit.hour, habit.minute))
            elif habit.service == "turn_off":
                offs.setdefault(target, (habit.hour, habit.minute))
    lights = []
    for entity_id in sorted(set(ons) & set(offs)):
        if ons[entity_id] < offs[entity_id] and entity_id in entities:
            lights.append(SimulatedLight(entity_id, entities[entity_id].friendly_name, ons[entity_id], offs[entity_id]))
    return lights


def _fixed_lights(entities: Sequence[EntitySnapshot]) -> list[SimulatedLight]:
    """Fixed evening times for the lights of the living room (or the room
    with the most lights) - offered honestly when no history exists."""
    lights = [entity for entity in entities if entity.domain == "light" and entity.area_name]
    if not lights:
        return []
    areas: dict[str, list[EntitySnapshot]] = {}
    for entity in lights:
        areas.setdefault(entity.area_name or "", []).append(entity)
    chosen = next((name for name in areas if normalize_for_compare(name) in {"wohnzimmer", "stube"}), None)
    if chosen is None:
        chosen = max(sorted(areas), key=lambda name: len(areas[name]))
    first = sorted(areas[chosen], key=lambda entity: entity.entity_id)[0]
    return [SimulatedLight(first.entity_id, first.friendly_name, FIXED_SIMULATION[0], FIXED_SIMULATION[1])]


def build_plan(
    request: VacationRequest,
    now: datetime,
    entities: Sequence[EntitySnapshot],
    habits: Sequence[Any],
    recipients: Sequence[str],
) -> VacationPlan:
    assert request.end is not None
    end = datetime.combine(request.end, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=23, minute=59)
    watched = tuple(sorted(
        entity.entity_id for entity in entities
        if entity.domain == "binary_sensor" and entity.device_class in _ENTRY_CLASSES | _MOTION_CLASSES
    ))
    by_id = {entity.entity_id: entity for entity in entities}
    lights: list[SimulatedLight] = []
    from_history = False
    if request.simulate:
        lights = _usual_light_times(habits, by_id)
        from_history = bool(lights)
        if not lights:
            lights = _fixed_lights(entities)
    helpers = vacation_helpers(entities)
    helper = helpers[0] if len(helpers) == 1 else None
    return VacationPlan(
        end, watched, tuple(lights), from_history,
        helper.entity_id if helper is not None else None,
        helper.friendly_name if helper is not None else None,
        tuple(recipients),
    )


def jitter_minutes(day: date, entity_id: str, kind: str) -> int:
    """A small, reproducible offset per day and light (−15 … +15 minutes)."""
    raw = hashlib.sha256(f"{day.isoformat()}|{entity_id}|{kind}".encode()).digest()
    return raw[0] % (2 * JITTER_MINUTES + 1) - JITTER_MINUTES


def _at(day: date, clock: tuple[int, int], offset: int) -> str:
    moment = datetime.combine(day, datetime.min.time()) + timedelta(hours=clock[0], minutes=clock[1] + offset)
    return f"{moment:%H:%M:00}"


def plan_configs(plan: VacationPlan, today: date) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """(automation id, config) of every vacation automation; the last one
    ends the vacation and deletes all of them, itself included."""
    end_iso = plan.end.isoformat()
    before_end = {"condition": "template", "value_template": "{{ now() < as_datetime('" + end_iso + "') }}"}
    created: list[tuple[str, dict[str, Any]]] = []
    if plan.watched and plan.recipients:
        created.append((uuid.uuid4().hex, {
            "alias": "Urlaubsmodus: Türen, Fenster und Bewegung melden",
            "description": "Urlaubsmodus (HomeIntent): jede Öffnung und jede Bewegung sofort an den Haushalt.",
            "triggers": [{"trigger": "state", "entity_id": list(plan.watched), "to": "on"}],
            "conditions": [before_end],
            "actions": [{
                "action": "notify.send_message",
                "target": {"entity_id": list(plan.recipients)},
                "data": {
                    "title": "HomeIntent",
                    "message": "Urlaubsmodus: {{ trigger.to_state.name }} – "
                               "{{ 'Bewegung erkannt' if trigger.to_state.attributes.device_class in "
                               "['motion', 'occupancy', 'presence'] else 'geöffnet' }}.",
                },
            }],
            "mode": "queued",
        }))
    days = [today + timedelta(days=offset) for offset in range((plan.end.date() - today).days + 1)][:MAX_DAYS]
    for light in plan.lights:
        # One trigger per distinct time; the day's own plan decides whether
        # it is today's switching time (several days may share a time).
        schedule: dict[str, dict[str, str]] = {}
        times: set[tuple[str, str]] = set()
        for day in days:
            for kind, clock in (("on", light.on), ("off", light.off)):
                at = _at(day, clock, jitter_minutes(day, light.entity_id, kind))[:5]
                schedule.setdefault(day.isoformat(), {})[kind] = at
                times.add((kind, at))
        triggers = [
            {"trigger": "time", "at": f"{at}:00", "id": f"{kind}|{at}"} for kind, at in sorted(times)
        ]
        planned = json.dumps(schedule, sort_keys=True)
        created.append((uuid.uuid4().hex, {
            "alias": f"Urlaubsmodus: Anwesenheit simulieren ({light.name})",
            "description": "Urlaubsmodus (HomeIntent): Licht zu gewohnten Zeiten, täglich leicht verschoben.",
            "triggers": triggers,
            "conditions": [
                before_end,
                {
                    "condition": "template",
                    "value_template": (
                        "{% set plan = " + planned + " %}{% set key = trigger.id.split('|') %}"
                        "{{ plan.get(now().date().isoformat(), {}).get(key[0]) == key[1] }}"
                    ),
                },
            ],
            "actions": [{
                "choose": [{
                    "conditions": [{"condition": "template", "value_template": "{{ trigger.id.startswith('on|') }}"}],
                    "sequence": [{"action": "light.turn_on", "target": {"entity_id": light.entity_id}}],
                }],
                "default": [{"action": "light.turn_off", "target": {"entity_id": light.entity_id}}],
            }],
            "mode": "queued",
        }))
    end_id = uuid.uuid4().hex
    end_actions: list[dict[str, Any]] = []
    if plan.helper is not None:
        end_actions.append({"action": "input_boolean.turn_off", "target": {"entity_id": plan.helper}})
    for automation_id, _ in created:
        end_actions.append({"action": "homeintent.delete_automation", "data": {"automation_id": automation_id}})
    end_actions.append({"action": "homeintent.delete_automation", "data": {"automation_id": end_id}})
    created.append((end_id, {
        "alias": "Urlaubsmodus: Ende",
        "description": f"Urlaubsmodus (HomeIntent): endet am {plan.end:%d.%m.%Y um %H:%M} Uhr und nimmt alles zurück.",
        "triggers": [
            {"trigger": "time", "at": f"{plan.end:%H:%M:00}"},
            {"trigger": "homeassistant", "event": "start"},
        ],
        "conditions": [{"condition": "template", "value_template": "{{ now() >= as_datetime('" + end_iso + "') }}"}],
        "actions": end_actions,
        "mode": "single",
    }))
    return created, end_id


def _actions(steps: Any) -> list[str]:
    found: list[str] = []
    if isinstance(steps, list):
        for step in steps:
            found.extend(_actions(step))
    elif isinstance(steps, dict):
        if isinstance(steps.get("action"), str):
            found.append(steps["action"])
        for value in steps.values():
            if isinstance(value, (list, dict)):
                found.extend(_actions(value))
    return found


def validate_plan(configs: Sequence[tuple[str, Mapping[str, Any]]], entities: Sequence[EntitySnapshot]) -> str | None:
    """The validator for vacation automations: only the allowed actions,
    only lights, the helper and notify targets that exist."""
    known = {entity.entity_id for entity in entities}
    for _, config in configs:
        for action in _actions(config.get("actions")):
            if action not in ALLOWED_ACTIONS:
                return f"nicht erlaubte Aktion {action}"
        for step in _flatten(config.get("actions")):
            target = (step.get("target") or {}).get("entity_id") if isinstance(step, dict) else None
            ids = [target] if isinstance(target, str) else list(target or [])
            for entity_id in ids:
                if entity_id not in known:
                    return f"unbekanntes Ziel {entity_id}"
                if step.get("action", "").startswith("light.") and not entity_id.startswith("light."):
                    return "Simulation schaltet nur Lichter"
    return None


def _flatten(steps: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for step in steps:
            found.extend(_flatten(step))
    elif isinstance(steps, dict):
        if "action" in steps:
            found.append(steps)
        for value in steps.values():
            if isinstance(value, (list, dict)):
                found.extend(_flatten(value))
    return found


def describe_plan(plan: VacationPlan, spoken_end: str) -> str:
    """The preview: every part listed."""
    parts = []
    if plan.watched and plan.recipients:
        parts.append(
            f"Jede Tür- oder Fensteröffnung und jede Bewegung ({len(plan.watched)} Melder) schicke ich "
            "sofort als Push an euch"
        )
    if plan.lights:
        names = ", ".join(f"„{light.name}“ {light.on[0]}:{light.on[1]:02d}–{light.off[0]}:{light.off[1]:02d} Uhr"
                          for light in plan.lights)
        if plan.simulation_from_history:
            parts.append(f"Ich simuliere Anwesenheit zu euren gewohnten Zeiten, täglich leicht verschoben: {names}")
        else:
            parts.append(
                "Für eine Simulation zu euren gewohnten Zeiten fehlt mir Verlauf; ich nehme stattdessen feste "
                f"Zeiten, täglich leicht verschoben: {names}"
            )
    if plan.helper_name is not None:
        parts.append(f"„{plan.helper_name}“ schalte ich jetzt ein und am Ende wieder aus")
    listed = "; ".join(f"{index}. {part}" for index, part in enumerate(parts, start=1))
    end = f"{plan.end:%d.%m.} um {plan.end:%H:%M} Uhr"
    return (
        f"Urlaubsmodus bis {spoken_end} ({end}): {listed}. Am Ende nehme ich alles wieder zurück. "
        "Heizung, Tore, Türen und Schlösser bleiben unberührt. Soll ich das so einrichten?"
    )


# --- store -----------------------------------------------------------------------

@dataclass
class VacationRecord:
    end: str
    automation_ids: list[str] = field(default_factory=list)
    helper: str | None = None
    lights: list[str] = field(default_factory=list)
    watched: int = 0
    created_by: str | None = None
    helper_id: str | None = None


class VacationStore:
    """The one active vacation profile (JSON file)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> VacationRecord | None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (FileNotFoundError, ValueError):
            return None
        if not isinstance(raw, dict) or not raw:
            return None
        return VacationRecord(**{key: raw[key] for key in VacationRecord.__dataclass_fields__ if key in raw})

    def save(self, record: VacationRecord | None) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".vacation-")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(asdict(record) if record is not None else {}, stream, ensure_ascii=False)
        os.replace(temp, self.path)
