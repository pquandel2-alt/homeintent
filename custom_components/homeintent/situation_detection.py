"""Deterministic V12 situation detection over fresh entity snapshots.

The detector is pure: it turns one Home Assistant state change (plus the
already built entity snapshot list) into zero or more ``DetectionSignal``s.
It never schedules, persists, communicates or acts.  Irrelevant events are
rejected by ``is_relevant`` before any snapshot scan, so an event storm of
unrelated entities costs one set lookup per event.

Home presence is taken from ``person.*`` (via the caller's ``nobody_home``
value from ``UserContextStore``); the detector never derives presence itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from .entities import EntitySnapshot
from .proactive_model import (
    ProposedGoal,
    SituationEvidence,
    SituationKind,
    TargetState,
)


ENTRY_COVER_CLASSES = frozenset({"garage", "garage_door", "gate", "door"})
ENTRY_SENSOR_CLASSES = frozenset({"garage_door", "door"})
SAFETY_CLASSES: Mapping[str, str] = {
    "smoke": "smoke",
    "carbon_monoxide": "carbon_monoxide",
    "gas": "gas",
    "moisture": "water_leak",
}
_OPEN_STATES = frozenset({"open", "opening", "on"})
_CLOSED_STATES = frozenset({"closed", "closing", "off"})
_SAFETY_ALARM_STATES = frozenset({"on", "detected", "alarm"})
_APPLIANCE_RUNNING = frozenset({
    "running", "run", "läuft", "laeuft", "washing", "drying", "rinsing",
    "spinning", "active", "in_progress", "program_running",
})
_APPLIANCE_FINISHED = frozenset({
    "finished", "finish", "done", "complete", "completed", "end", "ended",
    "fertig", "beendet", "programmende", "program_finished",
})
_UNRELIABLE = frozenset({"unknown", "unavailable", ""})


@dataclass(frozen=True)
class DetectorConfig:
    entry_open_minutes: int = 15
    appliance_entity_ids: frozenset[str] = frozenset()
    appliance_finished_ttl: timedelta = timedelta(hours=4)
    # Power-metered appliances (F15): "running" above ``appliance_running_watts``,
    # "finished" once the draw stays below ``appliance_idle_watts`` for
    # ``appliance_idle_duration`` after such a run.
    appliance_running_watts: float = 10.0
    appliance_idle_watts: float = 5.0
    appliance_idle_duration: timedelta = timedelta(minutes=1)


@dataclass(frozen=True)
class DetectionSignal:
    """Condition observation for one stable semantic subject.

    ``active`` False means the condition ended; the lifecycle store resolves
    the matching situation.  ``dedupe_key`` is the stable identity: the same
    garage staying open is the same situation across any number of events.
    """

    kind: SituationKind
    dedupe_key: str
    subject_ids: tuple[str, ...]
    area_id: str | None
    active: bool
    started_at: datetime
    evidence: tuple[SituationEvidence, ...] = ()
    subject_name: str = ""
    owner_user_id: str | None = None
    proposed_goal: ProposedGoal | None = None
    persons: tuple[str, ...] = ()
    anticipation_ref: str | None = None
    extra: Mapping[str, str] = field(default_factory=lambda: _empty_extra())


def _empty_extra() -> dict[str, str]:
    return {}


class SituationDetector:
    """Closed rule set for the initial productive V12 situation kinds."""

    def __init__(self, config: DetectorConfig | None = None) -> None:
        self.config = config or DetectorConfig()
        # Power-metered appliances seen running since their last finish.
        self._power_running: set[str] = set()

    # -- early filter ---------------------------------------------------
    def is_relevant(self, entity_id: str, device_class: str | None) -> bool:
        """Cheap first gate; no snapshot scan for unrelated entities."""
        domain = entity_id.partition(".")[0]
        if entity_id in self.config.appliance_entity_ids:
            # Selected appliances first: also power/binary sensors (F15).
            return True
        if domain == "cover":
            return (device_class or "") in ENTRY_COVER_CLASSES
        if domain == "binary_sensor":
            return (
                (device_class or "") in ENTRY_SENSOR_CLASSES
                or (device_class or "") in SAFETY_CLASSES
            )
        if domain in {"light", "person"}:
            return True
        return entity_id in self.config.appliance_entity_ids

    # -- per-event detection --------------------------------------------
    def detect_state_change(
        self,
        entity: EntitySnapshot,
        previous_state: str | None,
        *,
        entities: Iterable[EntitySnapshot],
        now: datetime,
        nobody_home: bool | None,
    ) -> tuple[DetectionSignal, ...]:
        if now.tzinfo is None:
            raise ValueError("Situationserkennung benötigt eine Zeitzone")
        if not self.is_relevant(entity.entity_id, entity.device_class):
            return ()
        state = entity.state.casefold()
        if state in _UNRELIABLE:
            # Unknown is not evidence for either direction.
            return ()
        signals: list[DetectionSignal] = []
        device_class = (entity.device_class or "").casefold()
        if entity.entity_id in self.config.appliance_entity_ids:
            appliance = self._appliance(entity, previous_state, state, now)
            if appliance is not None:
                signals.append(appliance)
        elif entity.domain == "binary_sensor" and device_class in SAFETY_CLASSES:
            signals.append(self._safety(entity, state, now))
        elif (
            (entity.domain == "cover" and device_class in ENTRY_COVER_CLASSES)
            or (entity.domain == "binary_sensor" and device_class in ENTRY_SENSOR_CLASSES)
        ):
            entry = self._entry(entity, state, now)
            if entry is not None:
                signals.append(entry)
        if entity.domain == "person":
            signals.extend(self.detect_left_on(tuple(entities), now=now, nobody_home=nobody_home))
        elif entity.domain == "light" and entity.area_id is not None:
            signals.extend(self.detect_left_on(
                tuple(entities), now=now, nobody_home=nobody_home,
                only_area=entity.area_id,
            ))
        return tuple(signals)

    def detect_left_on(
        self,
        entities: tuple[EntitySnapshot, ...],
        *,
        now: datetime,
        nobody_home: bool | None,
        only_area: str | None = None,
    ) -> tuple[DetectionSignal, ...]:
        """Lights still on while ``person.*`` reports nobody home, per area."""
        by_area: dict[str, list[EntitySnapshot]] = {}
        areas: set[str] = set()
        for item in entities:
            if item.domain != "light" or item.area_id is None:
                continue
            if only_area is not None and item.area_id != only_area:
                continue
            areas.add(item.area_id)
            if item.state == "on":
                by_area.setdefault(item.area_id, []).append(item)
        signals: list[DetectionSignal] = []
        for area_id in sorted(areas):
            on = tuple(sorted(by_area.get(area_id, ()), key=lambda item: item.entity_id))
            active = nobody_home is True and bool(on)
            area_name = next(
                (item.area_name for item in entities if item.area_id == area_id and item.area_name),
                area_id,
            )
            signals.append(DetectionSignal(
                SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING,
                f"{SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING.value}:{area_id}",
                tuple(item.entity_id for item in on),
                area_id,
                active,
                now,
                (
                    SituationEvidence("nobody_home", str(nobody_home).casefold()),
                    SituationEvidence("lights_on", str(len(on))),
                ),
                area_name or area_id,
                proposed_goal=(
                    ProposedGoal(
                        tuple(TargetState(item.entity_id, "off", item.friendly_name) for item in on),
                        "Licht ausschalten",
                    )
                    if active else None
                ),
            ))
        return tuple(signals)

    # -- individual rules -------------------------------------------------
    def _safety(self, entity: EntitySnapshot, state: str, now: datetime) -> DetectionSignal:
        hazard = SAFETY_CLASSES[(entity.device_class or "").casefold()]
        return DetectionSignal(
            SituationKind.CRITICAL_SAFETY_EVENT,
            f"{SituationKind.CRITICAL_SAFETY_EVENT.value}:{entity.entity_id}",
            (entity.entity_id,),
            entity.area_id,
            state in _SAFETY_ALARM_STATES,
            _started(entity, now),
            (
                SituationEvidence("hazard", hazard),
                SituationEvidence("device_class", entity.device_class or ""),
                SituationEvidence("state", state),
            ),
            entity.friendly_name,
            extra={"hazard": hazard},
        )

    def _entry(self, entity: EntitySnapshot, state: str, now: datetime) -> DetectionSignal | None:
        if state not in _OPEN_STATES and state not in _CLOSED_STATES:
            return None
        active = state in _OPEN_STATES
        started = _started(entity, now)
        # Covers expose open/close as their baseline contract; a door/garage
        # binary_sensor has no actuator, so it can only inform.
        actionable = entity.domain == "cover"
        return DetectionSignal(
            SituationKind.ENTRY_LEFT_OPEN,
            f"{SituationKind.ENTRY_LEFT_OPEN.value}:{entity.entity_id}",
            (entity.entity_id,),
            entity.area_id,
            active,
            started,
            (
                SituationEvidence("state", state),
                SituationEvidence("device_class", entity.device_class or ""),
                SituationEvidence("open_since", started.isoformat()),
            ),
            entity.friendly_name,
            proposed_goal=(
                ProposedGoal(
                    (TargetState(entity.entity_id, "closed", entity.friendly_name),),
                    "schließen",
                )
                if active and actionable else None
            ),
        )

    def _power_watts(self, entity: EntitySnapshot, raw: str | None) -> float | None:
        try:
            value = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return value * 1000 if (entity.unit or "") == "kW" else value

    def _power_appliance(
        self, entity: EntitySnapshot, previous_state: str | None, now: datetime,
    ) -> DetectionSignal | None:
        """Finished = below the idle threshold after a run (F15).

        The signal starts at the drop; ``ProactiveEngine`` only communicates
        it once the draw stayed low for ``appliance_idle_duration`` and
        re-checks the live value then (``still_active``), so short program
        pauses do not count as "finished".
        """
        key = f"{SituationKind.APPLIANCE_FINISHED.value}:{entity.entity_id}"
        watts = self._power_watts(entity, entity.state)
        if watts is None:
            return None
        previous = self._power_watts(entity, previous_state)
        if watts >= self.config.appliance_running_watts:
            self._power_running.add(entity.entity_id)
            return DetectionSignal(
                SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                entity.area_id, False, now,
                (SituationEvidence("source", "power"), SituationEvidence("watts", f"{watts:g}")),
                _appliance_name(entity),
            )
        was_running = entity.entity_id in self._power_running or (
            previous is not None and previous >= self.config.appliance_running_watts
        )
        if watts < self.config.appliance_idle_watts and was_running:
            self._power_running.discard(entity.entity_id)
            return DetectionSignal(
                SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                entity.area_id, True, now,
                (
                    SituationEvidence("source", "power"),
                    SituationEvidence("watts", f"{watts:g}"),
                    SituationEvidence("previous_watts", f"{previous:g}" if previous is not None else ""),
                ),
                _appliance_name(entity),
            )
        return None

    def _appliance(
        self, entity: EntitySnapshot, previous_state: str | None, state: str, now: datetime,
    ) -> DetectionSignal | None:
        if _is_power_sensor(entity):
            return self._power_appliance(entity, previous_state, now)
        previous = (previous_state or "").casefold()
        key = f"{SituationKind.APPLIANCE_FINISHED.value}:{entity.entity_id}"
        if entity.domain == "binary_sensor":
            # A running/operation binary sensor: on -> off is "finished" (F15).
            if state == "off" and previous == "on":
                return DetectionSignal(
                    SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                    entity.area_id, True, now,
                    (SituationEvidence("previous_state", previous), SituationEvidence("state", state)),
                    _appliance_name(entity),
                )
            if state == "on":
                return DetectionSignal(
                    SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                    entity.area_id, False, now, (SituationEvidence("state", state),),
                    _appliance_name(entity),
                )
            return None
        if state in _APPLIANCE_FINISHED and previous in _APPLIANCE_RUNNING:
            return DetectionSignal(
                SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                entity.area_id, True, now,
                (
                    SituationEvidence("previous_state", previous),
                    SituationEvidence("state", state),
                ),
                _appliance_name(entity),
            )
        if state in _APPLIANCE_RUNNING or (
            state not in _APPLIANCE_FINISHED and previous in _APPLIANCE_FINISHED
        ):
            # A new program or leaving the finished state ends the condition.
            return DetectionSignal(
                SituationKind.APPLIANCE_FINISHED, key, (entity.entity_id,),
                entity.area_id, False, now, (SituationEvidence("state", state),),
                _appliance_name(entity),
            )
        return None

    # -- live re-evaluation for scheduled checks/snooze/restart -----------
    def still_active(
        self,
        kind: SituationKind,
        subject_ids: tuple[str, ...],
        entities: Mapping[str, EntitySnapshot],
        *,
        nobody_home: bool | None,
    ) -> bool | None:
        """Re-check the live condition; ``None`` means it cannot be decided."""
        if kind is SituationKind.ENTRY_LEFT_OPEN:
            entity = entities.get(subject_ids[0]) if subject_ids else None
            if entity is None or entity.state.casefold() in _UNRELIABLE:
                return None
            return entity.state.casefold() in _OPEN_STATES
        if kind is SituationKind.CRITICAL_SAFETY_EVENT:
            entity = entities.get(subject_ids[0]) if subject_ids else None
            if entity is None or entity.state.casefold() in _UNRELIABLE:
                return None
            return entity.state.casefold() in _SAFETY_ALARM_STATES
        if kind is SituationKind.DEVICE_LEFT_ON_WHEN_LEAVING:
            if nobody_home is not True:
                return False
            return any(
                entities[item].state == "on" for item in subject_ids if item in entities
            )
        if kind is SituationKind.APPLIANCE_FINISHED:
            entity = entities.get(subject_ids[0]) if subject_ids else None
            if entity is None:
                return None
            if _is_power_sensor(entity):
                watts = self._power_watts(entity, entity.state)
                if watts is None:
                    return None
                return watts < self.config.appliance_running_watts
            if entity.domain == "binary_sensor":
                return entity.state.casefold() == "off"
            return entity.state.casefold() in _APPLIANCE_FINISHED
        # Kinds driven by V10/V11 evidence stay active until explicitly resolved.
        return True


def _is_power_sensor(entity: EntitySnapshot) -> bool:
    return entity.domain == "sensor" and (
        (entity.device_class or "") == "power" or (entity.unit or "") in {"W", "kW"}
    )


@dataclass(frozen=True)
class HabitCandidate:
    """A V11 habit model reduced to what detection may read."""

    model_id: str
    user_id: str
    time_band: str
    weekday: int | None
    steps: tuple[tuple[str, str], ...]  # (entity_id, expected state)


def parse_habit_sequence(sequence: str) -> tuple[tuple[str, str], ...]:
    """``OPERATOR@entity=state|...`` -> ((entity, state), ...); junk -> ()."""
    steps: list[tuple[str, str]] = []
    for item in sequence.split("|"):
        _operator, at, rest = item.partition("@")
        entity_id, equals, expected = rest.partition("=")
        if not at or not equals or "." not in entity_id or expected not in {"on", "off", "open", "closed"}:
            return ()
        steps.append((entity_id, expected))
    return tuple(steps)


_WEEKDAY_PHRASE = ("werktags", "werktags", "werktags", "werktags", "werktags", "am Wochenende", "am Wochenende")
_BAND_PHRASE = {"morning": "morgens", "day": "tagsüber", "evening": "abends", "night": "nachts"}


def habit_signal(
    entity: EntitySnapshot,
    candidates: Iterable[HabitCandidate],
    *,
    now: datetime,
    local_now: datetime,
    band: str,
    home_user_ids: frozenset[str],
    entities: Mapping[str, EntitySnapshot],
) -> DetectionSignal | None:
    """The first step of exactly one valid habit just happened for its owner."""
    matches = [
        item for item in candidates
        if item.steps and item.steps[0] == (entity.entity_id, entity.state)
        and item.time_band == band
        and (item.weekday is None or (item.weekday < 5) == (local_now.weekday() < 5))
        and item.user_id in home_user_ids
    ]
    if len(matches) != 1 or len(home_user_ids) != 1:
        # Several owners/habits: identity or intent is not unique; stay silent.
        return None
    habit = matches[0]
    remaining = tuple(
        TargetState(entity_id, expected, entities[entity_id].friendly_name)
        for entity_id, expected in habit.steps[1:]
        if entity_id in entities and entities[entity_id].state != expected
        # A routine suggestion never bundles security-relevant targets behind
        # a generic question; those always need their own explicit request.
        and not _security_relevant(entities[entity_id])
    )
    if not remaining:
        return None
    phrase = f"{_WEEKDAY_PHRASE[local_now.weekday()]} {_BAND_PHRASE.get(band, '')}".strip()
    return DetectionSignal(
        SituationKind.HABIT_OPPORTUNITY,
        f"{SituationKind.HABIT_OPPORTUNITY.value}:{habit.model_id}:{local_now.date().isoformat()}",
        tuple(item.entity_id for item in remaining),
        entity.area_id, True, now,
        (
            SituationEvidence("habit_model", habit.model_id),
            SituationEvidence("habit_phrase", phrase),
            SituationEvidence("trigger_entity", entity.entity_id),
        ),
        "Routine",
        owner_user_id=habit.user_id,
        proposed_goal=ProposedGoal(remaining, "Routine starten"),
        anticipation_ref=habit.model_id,
    )


_SECURITY_DOMAINS = frozenset({"lock", "alarm_control_panel", "siren", "valve"})
_SECURITY_CLASSES = frozenset({"garage", "garage_door", "gate", "door", "lock"})


def _security_relevant(entity: EntitySnapshot) -> bool:
    return (
        entity.domain in _SECURITY_DOMAINS
        or (entity.device_class or "").casefold() in _SECURITY_CLASSES
    )


def _started(entity: EntitySnapshot, now: datetime) -> datetime:
    changed = entity.last_changed
    if changed is not None and changed.tzinfo is not None and changed <= now:
        return changed
    return now


_APPLIANCE_NAME_NOISE = re.compile(
    r"\b(?:leistung|stromverbrauch|verbrauch|power|status|zustand|betrieb|laeuft|läuft|"
    r"running|aktiv|programm|steckdose|zwischenstecker)\b",
    re.IGNORECASE,
)


def _appliance_name(entity: EntitySnapshot) -> str:
    """ "Leistung Waschmaschine" / "Waschmaschine Status" -> "Waschmaschine"."""
    cleaned = " ".join(_APPLIANCE_NAME_NOISE.sub(" ", entity.friendly_name).split())
    return cleaned or entity.friendly_name


__all__ = (
    "DetectionSignal",
    "DetectorConfig",
    "HabitCandidate",
    "habit_signal",
    "parse_habit_sequence",
    "ENTRY_COVER_CLASSES",
    "ENTRY_SENSOR_CLASSES",
    "SAFETY_CLASSES",
    "SituationDetector",
)
