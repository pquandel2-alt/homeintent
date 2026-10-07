"""Batteries and unreachable devices (7.9.2 B3).

Read-only questions are answered without "Ja":

* "Welche Batterien sind schwach?" - battery sensors (``device_class:
  battery``) below ``LOW_BATTERY_PERCENT``, lowest first;
* "Welche Geräte sind nicht erreichbar?" - released sensors and actors whose
  state is ``unavailable``.

Monitoring ("Melde dich, wenn ein Gerät nicht mehr erreichbar ist") is an
automation like every other one (preview and "Ja"): a state trigger to
``unavailable`` held for ``UNAVAILABLE_MIN_SECONDS``. Home Assistant
starts a ``for`` timer at every transition, also the ones at its own start,
so a restart can never cause a flood - only devices still unreachable the
minimum time after the restart are reported, each once.

Home-Assistant-free and deterministic; the language is constructions over
closed word classes, never sentences.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from .entities import EntitySnapshot, format_spoken_number, normalize_for_compare

__all__ = (
    "DEVICE_DOMAINS",
    "LOW_BATTERY_PERCENT",
    "UNAVAILABLE_MIN_SECONDS",
    "answer_health_query",
    "battery_entities",
    "device_entities",
    "is_availability_event",
    "HealthReport",
    "parse_health_report",
    "report_message",
)

# "schwach" without a number: below this many percent (said in the answer).
LOW_BATTERY_PERCENT = 20
# "nicht mehr erreichbar": at least this long (said in the preview).
UNAVAILABLE_MIN_SECONDS = 600

# Released sensors and actors - what "ein Gerät" means. Helpers, scenes,
# scripts, people, notification and speech services are no devices.
DEVICE_DOMAINS = frozenset({
    "sensor", "binary_sensor", "light", "switch", "cover", "climate", "fan", "lock", "valve",
    "media_player", "vacuum", "lawn_mower", "humidifier", "water_heater", "alarm_control_panel",
    "camera", "assist_satellite",
})

_BATTERY_WORDS = frozenset({"batterie", "batterien", "akku", "akkus", "batteriestand", "batteriestaende"})
_LOW_WORDS = frozenset({"schwach", "schwache", "schwachen", "leer", "leere", "leeren", "niedrig", "niedrige",
                        "niedrigen", "aufladen", "wechseln", "tauschen", "erneuern", "knapp"})
_SCHEDULE_WORDS = frozenset({
    "jeden", "jede", "jedes", "taeglich", "woechentlich", "monatlich", "werktags", "wochentags",
    "montags", "dienstags", "mittwochs", "donnerstags", "freitags", "samstags", "sonntags", "immer",
    "wenn", "sobald", "falls",
})
_QUESTION_WORDS = frozenset({"welche", "welcher", "welches", "sind", "ist", "gibt", "wie", "wo", "muss", "muessen"})
_UNREACHABLE_PHRASES = (
    ("nicht", "erreichbar"), ("nicht", "verfuegbar"), ("unverfuegbar",), ("offline",), ("ausgefallen",),
    ("nicht", "verbunden"), ("keine", "verbindung"),
)


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


def battery_entities(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if entity.domain == "sensor" and entity.device_class == "battery"]


def device_entities(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return [entity for entity in entities if entity.domain in DEVICE_DOMAINS]


def _percent(entity: EntitySnapshot) -> float | None:
    try:
        return float(entity.state)
    except (TypeError, ValueError):
        return None


def _german_list(values: Sequence[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    return ", ".join(values[:-1]) + " und " + values[-1]


def _threshold(words: list[str]) -> float:
    """"unter 30 Prozent" in the question, else the default."""
    for index, word in enumerate(words[:-1]):
        if word in {"unter", "weniger"} and index + 1 < len(words):
            follow = words[index + 1] if words[index + 1] != "als" else (words[index + 2] if index + 2 < len(words) else "")
            if follow.isdigit():
                return float(follow)
    return float(LOW_BATTERY_PERCENT)


def _has_limit(words: list[str]) -> bool:
    return _threshold(words) != float(LOW_BATTERY_PERCENT) or any(
        word in {"unter", "weniger"} for word in words
    )


def _battery_answer(words: list[str], entities: Sequence[EntitySnapshot]) -> str:
    batteries = battery_entities(entities)
    if not batteries:
        return "Ich sehe keinen Batteriesensor. Batteriestände kann ich nur über freigegebene Batteriesensoren nennen."
    limit = _threshold(words)
    readable = [(entity, value) for entity in batteries if (value := _percent(entity)) is not None]
    low = sorted(((entity, value) for entity, value in readable if value < limit), key=lambda item: item[1])
    unknown = [entity.friendly_name for entity in batteries if _percent(entity) is None]
    limit_text = f"{format_spoken_number(limit)} %"
    if low:
        listed = _german_list([f"{entity.friendly_name} ({format_spoken_number(value)} %)" for entity, value in low])
        head = f"Unter {limit_text} liegt {listed}." if len(low) == 1 else f"Unter {limit_text} liegen {listed}."
    elif readable:
        lowest, value = min(readable, key=lambda item: item[1])
        head = (
            f"Keine Batterie liegt unter {limit_text}; am niedrigsten ist {lowest.friendly_name} "
            f"mit {format_spoken_number(value)} %."
        )
    else:
        head = "Keine Batterie meldet gerade einen Wert."
    if unknown:
        head += f" Keinen Wert melden: {_german_list(unknown)}."
    return head


def _unreachable_answer(entities: Sequence[EntitySnapshot]) -> str:
    devices = device_entities(entities)
    gone = sorted(entity.friendly_name for entity in devices if entity.state == "unavailable")
    if not gone:
        return f"Kein Gerät meldet „nicht verfügbar“: alle {len(devices)} freigegebenen Geräte sind erreichbar."
    verb = "ist" if len(gone) == 1 else "sind"
    return f"Nicht erreichbar {verb}: {_german_list(gone)}."


def answer_health_query(text: str, entities: Sequence[EntitySnapshot]) -> str | None:
    """The answer to a battery or reachability question, else ``None``."""
    words = _words(text)
    if not words or not (set(words) & (_QUESTION_WORDS | {"zeig", "zeige", "nenne", "liste"})):
        return None
    if set(words) & _SCHEDULE_WORDS or ("um" in words and "uhr" in words):
        # "Sag mir jeden Sonntag, welche Batterien …" is a report to set up,
        # never answered now (the schedule would be dropped).
        return None
    if set(words) & _BATTERY_WORDS and (set(words) & _LOW_WORDS or _has_limit(words)):
        return _battery_answer(words, entities)
    if any(
        all(part in words for part in phrase) for phrase in _UNREACHABLE_PHRASES
    ):
        return _unreachable_answer(entities)
    return None


# Predicates of an availability event ("… nicht mehr erreichbar ist",
# "… ausfällt", "… offline geht"), as normalized word sequences.
_AVAILABILITY_PREDICATES = (
    ("nicht", "mehr", "erreichbar"), ("nicht", "erreichbar"), ("nicht", "mehr", "verfuegbar"),
    ("nicht", "verfuegbar"), ("ausfaellt",), ("ausfallen",), ("ausgefallen",), ("offline",),
    ("nicht", "mehr", "antwortet"), ("keine", "verbindung"), ("die", "verbindung", "verliert"),
)


def is_availability_event(words: Sequence[str]) -> tuple[int, int] | None:
    """The span of an availability predicate in the event clause, if any."""
    keys = [normalize_for_compare(word.strip(",.;:!?")) for word in words]
    for phrase in _AVAILABILITY_PREDICATES:
        for start in range(len(keys) - len(phrase) + 1):
            if tuple(keys[start:start + len(phrase)]) == phrase:
                return start, start + len(phrase)
    return None



# --- scheduled reports ("Sag mir jeden Sonntag, welche Batterien …") -------

@dataclass(frozen=True)
class HealthReport:
    """A recurring report of weak batteries or unreachable devices."""

    kind: str  # "battery" | "unavailable"
    threshold: float
    weekdays: tuple[str, ...]  # () = every day
    hour: int | None
    minute: int = 0
    spoken_schedule: str = ""


_REPORT_VERBS = frozenset({
    "sag", "sage", "schick", "schicke", "melde", "meld", "gib", "berichte", "informiere", "zeig", "zeige",
    "nenne", "nenn", "benachrichtige",
})
_WEEKDAY_STEMS = {
    "montag": "mon", "dienstag": "tue", "mittwoch": "wed", "donnerstag": "thu", "freitag": "fri",
    "samstag": "sat", "sonntag": "sun",
}
_WORKDAYS = ("mon", "tue", "wed", "thu", "fri")
_CLOCK_RE = re.compile(r"\bum\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)


def _schedule(words: list[str]) -> tuple[tuple[str, ...], str] | None:
    for index, word in enumerate(words):
        stem = word.removesuffix("s")
        previous = words[index - 1] if index else ""
        if stem in _WEEKDAY_STEMS and (word.endswith("s") or previous in {"jeden", "jedem"}):
            return (_WEEKDAY_STEMS[stem],), f"jeden {stem.capitalize()}"
        if word in {"werktags", "wochentags"} or (word in {"werktag", "wochentag"} and previous in {"jeden", "jedem"}):
            return _WORKDAYS, "an Werktagen"
        if word in {"taeglich"} or (word in {"tag", "morgen", "abend"} and previous == "jeden"):
            return (), "jeden Tag"
    return None


def parse_health_report(text: str) -> HealthReport | None:
    """A report verb + a schedule + a battery/reachability question."""
    words = _words(text)
    if not words or not set(words) & _REPORT_VERBS:
        return None
    schedule = _schedule(words)
    if schedule is None:
        return None
    if set(words) & _BATTERY_WORDS and (set(words) & _LOW_WORDS or _has_limit(words)):
        kind = "battery"
    elif any(all(part in words for part in phrase) for phrase in _UNREACHABLE_PHRASES):
        kind = "unavailable"
    else:
        return None
    clock = _CLOCK_RE.search(text)
    hour = int(clock.group("hour")) if clock is not None else None
    minute = int(clock.group("minute") or 0) if clock is not None else 0
    if hour is not None and not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return HealthReport(kind, _threshold(words), schedule[0], hour, minute, schedule[1])


def report_message(report: HealthReport, entities: Sequence[EntitySnapshot]) -> tuple[str, str] | None:
    """(spoken form, Home Assistant template) of the report - the template is
    built from entity ids and registry names only, never from user text."""
    if report.kind == "battery":
        members = battery_entities(entities)
        if not members:
            return None
        limit = format_spoken_number(report.threshold)
        names = json.dumps({entity.entity_id: entity.friendly_name for entity in members}, ensure_ascii=False)
        template = (
            "{% set ns = namespace(items=[]) %}"
            "{% for entity, name in " + names + ".items() %}"
            "{% set value = states(entity) | float(-1) %}"
            "{% if 0 <= value < " + f"{report.threshold:g}" + " %}"
            "{% set ns.items = ns.items + [name ~ ' (' ~ (value | round(0) | int) ~ ' %)'] %}"
            "{% endif %}{% endfor %}"
            "{{ ('Batterien unter " + limit + " %: ' ~ ns.items | join(', ')) if ns.items "
            "else 'Keine Batterie liegt unter " + limit + " %.' }}"
        )
        # The spoken form is what the report would say right now (7.9.3 A5).
        low = []
        for entity in members:
            try:
                value = float(entity.state)
            except (TypeError, ValueError):
                continue
            if 0 <= value < report.threshold:
                low.append(f"{entity.friendly_name} ({round(value)} %)")
        spoken = f"Batterien unter {limit} %: " + ", ".join(low) if low else f"Keine Batterie liegt unter {limit} %."
        return spoken, template
    members = device_entities(entities)
    if not members:
        return None
    names = json.dumps({entity.entity_id: entity.friendly_name for entity in members}, ensure_ascii=False)
    template = (
        "{% set ns = namespace(items=[]) %}"
        "{% for entity, name in " + names + ".items() %}"
        "{% if states(entity) == 'unavailable' %}{% set ns.items = ns.items + [name] %}{% endif %}"
        "{% endfor %}"
        "{{ ('Nicht erreichbar: ' ~ ns.items | join(', ')) if ns.items else 'Alle Geräte sind erreichbar.' }}"
    )
    down = [entity.friendly_name for entity in members if entity.state == "unavailable"]
    return ("Nicht erreichbar: " + ", ".join(down) if down else "Alle Geräte sind erreichbar."), template
