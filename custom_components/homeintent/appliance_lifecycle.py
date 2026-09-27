"""Deterministic, read-only lifecycle view for household appliances.

The view only reports attributes or entities exposed by Home Assistant.  A
power value is deliberately not converted into a running/finished claim: that
would be a statistical hypothesis and must not be presented as an observed
fact or become an action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .entities import EntitySnapshot, normalize_for_compare, spoken_state
from .world_model import WorldModel


class ApplianceQuestion(StrEnum):
    STATUS = "status"
    PROGRESS = "progress"
    REMAINING = "remaining"
    PROGRAM = "program"
    FINISH = "finish"


@dataclass(frozen=True)
class ApplianceEvidence:
    entity_id: str
    property: str
    value: str
    observed: bool = True


@dataclass(frozen=True)
class ApplianceLifecycleAnswer:
    text: str
    question: ApplianceQuestion
    evidence: tuple[ApplianceEvidence, ...] = ()
    ambiguous: bool = False


_APPLIANCE = r"(?:waschmaschine|waeschetrockner|trockner|geschirrspueler|spuelmaschine)"
_QUESTION_PATTERNS: tuple[tuple[ApplianceQuestion, re.Pattern[str]], ...] = (
    (ApplianceQuestion.FINISH, re.compile(
        r"\bwann\s+(?:ist|wird|waere)\s+(?:die|der|das|mein\w*)?\s*(?P<target>.+?)\s+"
        r"(?:fertig|beendet|durch)\s*[?.!]*$"
    )),
    (ApplianceQuestion.REMAINING, re.compile(
        rf"\bwie\s+lange\s+(?:laeuft\s+)?(?:die|der|das|mein\w*)?\s*"
        rf"(?P<target>.*?{_APPLIANCE}.*?)\s*(?:noch)?\s*[?.!]*$"
    )),
    (ApplianceQuestion.PROGRESS, re.compile(
        rf"\bwie\s+weit\s+(?:ist|sind)\s+(?:die|der|das|mein\w*)?\s*"
        rf"(?P<target>.*?{_APPLIANCE}.*?)\s*[?.!]*$"
    )),
    (ApplianceQuestion.PROGRAM, re.compile(
        rf"\bwelches\s+programm\s+(?:laeuft|hat|nutzt)\s+(?:die|der|das|mein\w*)?\s*"
        rf"(?P<target>.*?{_APPLIANCE}.*?)\s*[?.!]*$"
    )),
    (ApplianceQuestion.STATUS, re.compile(
        rf"\b(?:laeuft|arbeitet)\s+(?:die|der|das|mein\w*)?\s*"
        rf"(?P<target>.*?{_APPLIANCE}.*?)\s*(?:noch)?\s*[?.!]*$"
    )),
    (ApplianceQuestion.STATUS, re.compile(
        r"\b(?:was\s+macht|wie\s+ist\s+der\s+status\s+(?:von|der))\s+"
        rf"(?:die|der|das|mein\w*)?\s*(?P<target>.*?{_APPLIANCE}.*?)\s*[?.!]*$"
    )),
)

_PROPERTY_WORDS = frozenset({
    "status", "zustand", "betriebszustand", "programm", "program", "phase",
    "fortschritt", "progress", "restzeit", "verbleibend", "remaining",
    "dauer", "duration", "fertigstellungszeit", "programmende", "endzeit",
    "completion", "time", "power", "leistung", "energie", "verbrauch",
})
_UNAVAILABLE = frozenset({"", "unknown", "unavailable", "none", "null"})


def _parse_question(text: str) -> tuple[ApplianceQuestion, str] | None:
    key = normalize_for_compare(text)
    for question, pattern in _QUESTION_PATTERNS:
        if (match := pattern.search(key)) is not None:
            target = match.group("target").strip(" .?!")
            return question, target
    return None


def _base_name(entity: EntitySnapshot) -> str:
    words = normalize_for_compare(entity.friendly_name).split()
    return " ".join(word for word in words if word not in _PROPERTY_WORDS)


def _matching_groups(
    target: str,
    entities: list[EntitySnapshot],
    world_model: WorldModel | None,
) -> tuple[tuple[str, tuple[EntitySnapshot, ...]], ...]:
    if world_model is not None:
        devices = [
            device for device in world_model.devices
            if target == normalize_for_compare(device.name)
            or normalize_for_compare(device.name).startswith(target + " ")
            or target.startswith(normalize_for_compare(device.name) + " ")
        ]
        if devices:
            return tuple(
                (device.name, world_model.entities_for_device(device.device_id))
                for device in devices
            )

    grouped: dict[str, list[EntitySnapshot]] = {}
    for entity in entities:
        base = _base_name(entity)
        if target == base or base.startswith(target + " ") or target.startswith(base + " "):
            grouped.setdefault(base, []).append(entity)
    return tuple(
        (base.title(), tuple(items))
        for base, items in sorted(grouped.items())
    )


def _find_entity(
    entities: tuple[EntitySnapshot, ...],
    *,
    words: tuple[str, ...],
    device_classes: tuple[str, ...] = (),
) -> EntitySnapshot | None:
    candidates = [
        entity for entity in entities
        if normalize_for_compare(entity.state) not in _UNAVAILABLE
        and (
            entity.device_class in device_classes
            or any(word in normalize_for_compare(entity.friendly_name) for word in words)
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _format_duration(entity: EntitySnapshot) -> str:
    value = str(entity.state)
    try:
        seconds = float(value.replace(",", "."))
    except ValueError:
        return value
    unit = normalize_for_compare(entity.unit or "")
    if unit in {"h", "hour", "hours", "stunde", "stunden"}:
        seconds *= 3600
    elif unit in {"min", "minute", "minutes", "minuten"}:
        seconds *= 60
    hours, remainder = divmod(max(0, round(seconds)), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return (
            f"{hours} {'Stunde' if hours == 1 else 'Stunden'} und "
            f"{minutes} {'Minute' if minutes == 1 else 'Minuten'}"
        )
    if hours:
        return f"{hours} {'Stunde' if hours == 1 else 'Stunden'}"
    return f"{minutes} {'Minute' if minutes == 1 else 'Minuten'}"


def _format_finish(value: str, now: datetime) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None and now.tzinfo is not None:
        parsed = parsed.astimezone(now.tzinfo)
    day = "heute" if parsed.date() == now.date() else (
        "morgen" if parsed.date() == now.date() + timedelta(days=1)
        else f"am {parsed:%d.%m.}"
    )
    return f"{day} um {parsed:%H:%M} Uhr"


def match_appliance_lifecycle_query(
    text: str,
    entities: list[EntitySnapshot],
    now: datetime,
    world_model: WorldModel | None = None,
) -> ApplianceLifecycleAnswer | None:
    """Answer an appliance lifecycle question from explicit HA evidence."""
    parsed = _parse_question(text)
    if parsed is None:
        return None
    question, target = parsed
    groups = _matching_groups(target, entities, world_model)
    if not groups:
        return ApplianceLifecycleAnswer(
            f"Ich finde keine für Assist freigegebenen Statusdaten zu {target}.",
            question,
        )
    if len(groups) != 1:
        return ApplianceLifecycleAnswer(
            "Welche Maschine meinst du? Ich habe nichts ausgeführt.",
            question,
            ambiguous=True,
        )
    label, related = groups[0]
    specs = {
        ApplianceQuestion.STATUS: (("status", "zustand", "betriebszustand", "phase"), ("enum",)),
        ApplianceQuestion.PROGRESS: (("fortschritt", "progress"), ()),
        ApplianceQuestion.REMAINING: (("restzeit", "verbleibend", "remaining", "dauer"), ("duration",)),
        ApplianceQuestion.PROGRAM: (("programm", "program"), ("enum",)),
        ApplianceQuestion.FINISH: (("fertigstellungszeit", "programmende", "endzeit", "completion"), ("timestamp",)),
    }
    words, classes = specs[question]
    entity = _find_entity(related, words=words, device_classes=classes)
    if entity is None:
        return ApplianceLifecycleAnswer(
            f"{label} stellt dafür keine eindeutige, direkt beobachtete Information bereit.",
            question,
        )
    evidence = (ApplianceEvidence(entity.entity_id, question.value, str(entity.state)),)
    if question is ApplianceQuestion.FINISH:
        rendered = _format_finish(str(entity.state), now)
        if rendered is None:
            return ApplianceLifecycleAnswer(
                f"{entity.friendly_name} liefert keine gültige Fertigstellungszeit.",
                question,
                evidence,
            )
        answer = f"Laut {entity.friendly_name} ist die Fertigstellung {rendered}."
    elif question is ApplianceQuestion.REMAINING:
        answer = f"Laut {entity.friendly_name} verbleiben {_format_duration(entity)}."
    elif question is ApplianceQuestion.PROGRESS:
        suffix = " Prozent" if entity.unit == "%" else (f" {entity.unit}" if entity.unit else "")
        answer = f"{label}: {entity.state}{suffix} Fortschritt."
    elif question is ApplianceQuestion.PROGRAM:
        answer = f"{label} verwendet das Programm {spoken_state(entity.state)}."
    else:
        answer = f"{label} meldet den Status „{spoken_state(entity.state)}“."
    return ApplianceLifecycleAnswer(answer, question, evidence)


__all__ = [
    "ApplianceEvidence",
    "ApplianceLifecycleAnswer",
    "ApplianceQuestion",
    "match_appliance_lifecycle_query",
]
