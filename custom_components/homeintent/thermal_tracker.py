"""Lifecycle-bound collection of compact, unambiguous heating experiences."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, cast

from .entities import EntitySnapshot
from .goal_run import GoalRun, GoalRunStatus, GoalRunStore
from .learning_manager import LearningManager
from .learning_policy import LearningMode
from .nlu.primitives import SemanticProperty
from .nlu.unit_reasoning import normalize_measurement
from .service_call import ServiceCallPlan
from .thermal_model import ThermalBinding, ThermalObservation


@dataclass(frozen=True)
class ActiveThermalCycle:
    binding: ThermalBinding
    started_at: datetime
    start_celsius: float
    target_celsius: float
    outdoor_celsius: float | None
    goal_id: str
    run_id: str
    user_id: str | None = None
    window_opened: bool = False
    service_succeeded: bool = True
    source_changed: bool = False
    concurrent_action: bool = False
    cycle_id: str = ""
    starting_hvac_mode: str | None = None
    expected_setpoint: float | None = None
    model_id: str | None = None
    deadline: datetime | None = None
    contamination_reason: str | None = None


class ThermalExperienceTracker:
    """Tracks only HomeIntent-owned setpoint actions and exact bindings."""

    def __init__(
        self, manager: LearningManager, *, state_path: str | Path | None = None
    ) -> None:
        self._manager = manager
        self._active: dict[str, ActiveThermalCycle] = {}
        self._counter = 0
        self._state_path = Path(state_path) if state_path is not None else None
        # Writes may finish out of order when they run in worker threads; the
        # generation keeps an older document from replacing a newer one.
        self._write_lock = threading.Lock()
        self._generation = 0
        self._written_generation = 0
        # Window sensors in each active cycle's area, taken from the snapshot
        # the cycle started (or was restored) with (7.9.6 interest).
        self._area_windows: dict[str, frozenset[str]] = {}
        self._watched: frozenset[str] = frozenset()
        self._watch_listeners: list[Callable[[], None]] = []

    @property
    def active(self) -> tuple[ActiveThermalCycle, ...]:
        return tuple(self._active[key] for key in sorted(self._active))

    @property
    def has_active_cycle(self) -> bool:
        return bool(self._active)

    @property
    def watched_entity_ids(self) -> frozenset[str]:
        """Entities an active cycle reads (read-only, empty without a cycle).

        Climate entity, temperature sensor, configured outdoor sensor and the
        window sensors of the cycle's area - everything
        ``async_observe_states`` looks at. The EventRuntime forwards state
        changes only while this is non-empty (7.9.6).
        """
        return self._watched

    def add_watch_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever ``watched_entity_ids`` changes."""
        self._watch_listeners.append(listener)

        def remove() -> None:
            if listener in self._watch_listeners:
                self._watch_listeners.remove(listener)

        return remove

    def _remember_windows(
        self, climate_id: str, area_id: str, entities: Iterable[EntitySnapshot]
    ) -> None:
        self._area_windows[climate_id] = frozenset(
            item.entity_id for item in entities
            if item.area_id == area_id and item.device_class == "window"
        )

    def _update_watched(self) -> None:
        for climate_id in tuple(self._area_windows):
            if climate_id not in self._active:
                del self._area_windows[climate_id]
        watched: set[str] = set()
        for climate_id, cycle in self._active.items():
            watched.add(climate_id)
            watched.add(cycle.binding.temperature_entity_id)
            if cycle.binding.outdoor_temperature_entity_id is not None:
                watched.add(cycle.binding.outdoor_temperature_entity_id)
            watched.update(self._area_windows.get(climate_id, ()))
        frozen = frozenset(watched)
        if frozen == self._watched:
            return
        self._watched = frozen
        for listener in tuple(self._watch_listeners):
            listener()

    @property
    def predictive_house(self):
        """Expose the read-only advisory facade to checkpoint evaluation."""
        return self._manager.predictive_house

    def observe_action(
        self,
        plan: ServiceCallPlan,
        entities: tuple[EntitySnapshot, ...],
        *,
        occurred_at: datetime,
    ) -> None:
        """Start only when one climate target and one room sensor are exact."""
        if (
            self._manager.policy.learning_mode is LearningMode.OFF
            or
            plan.domain != "climate" or plan.service != "set_temperature"
            or not isinstance(plan.entity_id, str)
        ):
            return
        data = cast(Mapping[str, object], plan.data)
        raw_target = data.get("temperature")
        if not isinstance(raw_target, (int, float)):
            return
        climate = next(
            (item for item in entities if item.entity_id == plan.entity_id), None
        )
        if climate is None or climate.area_id is None:
            return
        candidates = tuple(
            item for item in entities
            if item.area_id == climate.area_id
            and item.domain == "sensor"
            and item.device_class == "temperature"
            and item.state not in {"unknown", "unavailable"}
        )
        if len(candidates) != 1:
            return
        sensor = candidates[0]
        current = _temperature(sensor)
        target = normalize_measurement(
            float(raw_target), sensor.unit, SemanticProperty.TEMPERATURE
        )
        if current is None or target is None or target.value <= current:
            return
        previous = self._active.get(plan.entity_id)
        self._counter += 1
        outdoor_id: str | None = None
        outdoor_value: float | None = None
        model = self._manager.predictive_house.thermal_model(climate.area_id)
        if model is not None and model.binding.outdoor_temperature_entity_id is not None:
            configured = next((item for item in entities
                               if item.entity_id == model.binding.outdoor_temperature_entity_id), None)
            if configured is not None:
                outdoor_id = configured.entity_id
                outdoor_value = _temperature(configured)
        self._active[plan.entity_id] = ActiveThermalCycle(
            ThermalBinding(
                climate.area_id, sensor.entity_id, climate.entity_id,
                outdoor_id,
                confirmed=True,
            ),
            occurred_at, current, target.value, outdoor_value,
            f"pending:{self._counter}", f"thermal-pending:{self._counter}",
            concurrent_action=False,
            cycle_id=(previous.cycle_id if previous is not None and previous.cycle_id
                      else f"cycle_{uuid.uuid4().hex}"),
            starting_hvac_mode=(previous.starting_hvac_mode if previous is not None
                                else climate.state),
            expected_setpoint=target.value,
            model_id=model.model_id if model is not None else None,
        )
        self._remember_windows(plan.entity_id, climate.area_id, entities)
        self._update_watched()
        self._persist()

    def associate_goal_run(self, run: GoalRun) -> None:
        """Attach authoritative GoalRun provenance to a just-started cycle."""
        targets = {
            target
            for step in run.steps
            if step.operator_id == "CLIMATE_SET_TEMPERATURE"
            for target in step.selected_targets
        }
        for target in targets:
            cycle = self._active.get(target)
            if cycle is not None:
                self._active[target] = replace(
                    cycle, goal_id=run.goal_id, run_id=run.run_id,
                    user_id=run.user_id,
                )
        self._persist()

    async def async_observe_states(
        self,
        entities: tuple[EntitySnapshot, ...],
        *,
        occurred_at: datetime,
    ) -> None:
        # Called for every selected state change: without an active cycle
        # there is nothing to observe and nothing to write (7.9.4 P0 - an
        # unconditional fsync per event saturated Home Assistant's executor
        # during startup).
        if not self._active:
            return
        by_id = {item.entity_id: item for item in entities}
        completed: list[tuple[str, ActiveThermalCycle, bool]] = []
        changed = False
        for climate_id, cycle in tuple(self._active.items()):
            sensor = by_id.get(cycle.binding.temperature_entity_id)
            climate = by_id.get(climate_id)
            if sensor is None or climate is None:
                completed.append((climate_id, replace(
                    cycle, source_changed=True,
                    contamination_reason="measurement_source_removed",
                ), False))
                continue
            actual_setpoint = _numeric_attribute(climate.attributes.get("temperature"))
            mode_changed = (
                cycle.starting_hvac_mode is not None
                and climate.state != cycle.starting_hvac_mode
            )
            setpoint_changed = (
                actual_setpoint is not None
                and cycle.expected_setpoint is not None
                and abs(actual_setpoint - cycle.expected_setpoint) > 0.1
            )
            if mode_changed or setpoint_changed:
                reason = "hvac_mode_changed" if mode_changed else "setpoint_changed"
                completed.append((climate_id, replace(
                    cycle, source_changed=True, contamination_reason=reason
                ), False))
                continue
            windows = tuple(
                item for item in entities
                if item.area_id == cycle.binding.area_id
                and item.device_class == "window"
                and item.state.casefold() in {"on", "open", "opening"}
            )
            if windows and not cycle.window_opened:
                cycle = replace(cycle, window_opened=True)
                self._active[climate_id] = cycle
                changed = True
            current = _temperature(sensor)
            invalid = (
                sensor.state in {"unknown", "unavailable"}
                or climate.state in {"unknown", "unavailable"}
                or occurred_at <= cycle.started_at
                or occurred_at - cycle.started_at > timedelta(hours=12)
            )
            if invalid or current is None:
                completed.append((climate_id, cycle, False))
            elif current >= cycle.target_celsius - 0.1:
                completed.append((climate_id, cycle, True))
        if completed:
            for climate_id, _cycle, _reached in completed:
                self._active.pop(climate_id, None)
            self._update_watched()
        for climate_id, cycle, reached in completed:
            observation = ThermalObservation(
                cycle.started_at, occurred_at, cycle.binding.area_id,
                cycle.binding.temperature_entity_id,
                cycle.binding.climate_entity_id, cycle.start_celsius,
                cycle.target_celsius,
                max(0.0, (occurred_at - cycle.started_at).total_seconds()),
                reached, cycle.outdoor_celsius, cycle.window_opened,
                cycle.service_succeeded, cycle.source_changed,
                cycle.concurrent_action,
            )
            await self._manager.async_record_thermal_observation(
                cycle.binding, observation, goal_id=cycle.goal_id,
                run_id=cycle.run_id, user_id=cycle.user_id,
            )
            if cycle.contamination_reason == "measurement_source_removed":
                await self._manager.async_invalidate_thermal_model(
                    cycle.binding.area_id, "measurement_source_removed"
                )
        if completed or changed:
            await self._async_persist()

    async def async_finalize(
        self,
        climate_entity_id: str,
        entities: tuple[EntitySnapshot, ...],
        *,
        occurred_at: datetime,
    ) -> bool | None:
        """Close an active cycle at its authoritative goal deadline."""
        cycle = self._active.pop(climate_entity_id, None)
        if cycle is None:
            return None
        self._update_watched()
        by_id = {item.entity_id: item for item in entities}
        sensor = by_id.get(cycle.binding.temperature_entity_id)
        climate = by_id.get(climate_entity_id)
        current = _temperature(sensor) if sensor is not None else None
        source_changed = sensor is None or climate is None
        reached = (
            not source_changed
            and current is not None
            and current >= cycle.target_celsius - 0.1
        )
        finalized = replace(
            cycle, source_changed=cycle.source_changed or source_changed,
            contamination_reason=(
                "measurement_source_removed" if source_changed
                else cycle.contamination_reason
            ),
        )
        observation = ThermalObservation(
            finalized.started_at, occurred_at, finalized.binding.area_id,
            finalized.binding.temperature_entity_id,
            finalized.binding.climate_entity_id, finalized.start_celsius,
            finalized.target_celsius,
            max(0.0, (occurred_at - finalized.started_at).total_seconds()),
            reached, finalized.outdoor_celsius, finalized.window_opened,
            finalized.service_succeeded, finalized.source_changed,
            finalized.concurrent_action,
        )
        await self._manager.async_record_thermal_observation(
            finalized.binding, observation, goal_id=finalized.goal_id,
            run_id=finalized.run_id, user_id=finalized.user_id,
        )
        if finalized.contamination_reason == "measurement_source_removed":
            await self._manager.async_invalidate_thermal_model(
                finalized.binding.area_id, "measurement_source_removed"
            )
        await self._async_persist()
        return reached

    async def async_restore(
        self, entities: tuple[EntitySnapshot, ...], goal_runs: GoalRunStore,
        *, now: datetime,
    ) -> int:
        """Restore compatible observation state without executing any action."""
        if self._state_path is None or now.tzinfo is None:
            return 0
        by_id = {item.entity_id: item for item in entities}
        runs = {item.run_id: item for item in await goal_runs.async_list()}
        restored: dict[str, ActiveThermalCycle] = {}
        for cycle in await asyncio.to_thread(self._read_persisted):
            climate = by_id.get(cycle.binding.climate_entity_id)
            sensor = by_id.get(cycle.binding.temperature_entity_id)
            setpoint = (
                _numeric_attribute(climate.attributes.get("temperature"))
                if climate is not None else None
            )
            compatible = (
                not cycle.source_changed and cycle.contamination_reason is None
                and cycle.started_at.tzinfo is not None
                and timedelta(0) < now - cycle.started_at
                <= self._manager.policy.maximum_thermal_cycle_duration
                and climate is not None and sensor is not None
                and cycle.run_id in runs
                and runs[cycle.run_id].status in {
                    GoalRunStatus.PENDING,
                    GoalRunStatus.RUNNING,
                    GoalRunStatus.SUCCESS,
                    GoalRunStatus.SCHEDULED,
                }
                and climate.area_id == cycle.binding.area_id
                and sensor.area_id == cycle.binding.area_id
                and (cycle.starting_hvac_mode is None
                     or climate.state == cycle.starting_hvac_mode)
                and (
                    cycle.expected_setpoint is None
                    or (
                        setpoint is not None
                        and abs(setpoint - cycle.expected_setpoint) <= 0.1
                    )
                )
            )
            if compatible:
                restored[cycle.binding.climate_entity_id] = cycle
        self._active = restored
        self._area_windows = {}
        for climate_id, cycle in restored.items():
            self._remember_windows(climate_id, cycle.binding.area_id, entities)
        self._update_watched()
        # Startup runs on the event loop; the document is built here so the
        # worker thread never iterates state the loop may mutate.
        await self._async_persist()
        return len(restored)

    def _persisted_document(self) -> dict[str, object]:
        return {"schema_version": 1, "cycles": [_cycle_dict(item) for item in self.active]}

    def _next_document(self) -> tuple[int, dict[str, object]]:
        # Built on the event loop so no worker thread reads live state.
        self._generation += 1
        return self._generation, self._persisted_document()

    def _write_generation(self, generation: int, document: dict[str, object]) -> None:
        with self._write_lock:
            if generation <= self._written_generation:
                return
            self._write_document(document)
            self._written_generation = generation

    def _persist(self) -> None:
        self._write_generation(*self._next_document())

    async def _async_persist(self) -> None:
        await asyncio.to_thread(self._write_generation, *self._next_document())

    def _write_document(self, document: dict[str, object]) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homeintent_thermal_cycles_", dir=str(self._state_path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _read_persisted(self) -> tuple[ActiveThermalCycle, ...]:
        if self._state_path is None:
            return ()
        try:
            raw: object = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return ()
        if not isinstance(raw, Mapping):
            return ()
        document = cast(Mapping[str, object], raw)
        if document.get("schema_version") != 1:
            return ()
        values = document.get("cycles")
        if not isinstance(values, list):
            return ()
        result: list[ActiveThermalCycle] = []
        for value in cast(list[object], values):
            if not isinstance(value, Mapping):
                continue
            try:
                result.append(_cycle_from_dict(cast(Mapping[str, object], value)))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(result)


def _temperature(entity: EntitySnapshot) -> float | None:
    try:
        raw = float(entity.state)
    except ValueError:
        return None
    normalized = normalize_measurement(
        raw, entity.unit, SemanticProperty.TEMPERATURE
    )
    return normalized.value if normalized is not None else None


def _numeric_attribute(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _cycle_dict(cycle: ActiveThermalCycle) -> dict[str, object]:
    return {
        "cycle_id": cycle.cycle_id,
        "goal_id": cycle.goal_id,
        "run_id": cycle.run_id,
        "user_id": cycle.user_id,
        "area_id": cycle.binding.area_id,
        "climate_entity_id": cycle.binding.climate_entity_id,
        "temperature_entity_id": cycle.binding.temperature_entity_id,
        "outdoor_entity_id": cycle.binding.outdoor_temperature_entity_id,
        "started_at": cycle.started_at.isoformat(),
        "start_temperature": cycle.start_celsius,
        "target_temperature": cycle.target_celsius,
        "outdoor_temperature": cycle.outdoor_celsius,
        "starting_hvac_mode": cycle.starting_hvac_mode,
        "expected_setpoint": cycle.expected_setpoint,
        "window_opened": cycle.window_opened,
        "service_succeeded": cycle.service_succeeded,
        "source_changed": cycle.source_changed,
        "concurrent_action": cycle.concurrent_action,
        "contamination_reason": cycle.contamination_reason,
        "model_id": cycle.model_id,
        "deadline": cycle.deadline.isoformat() if cycle.deadline else None,
    }


def _cycle_from_dict(raw: Mapping[str, object]) -> ActiveThermalCycle:
    started = datetime.fromisoformat(str(raw["started_at"]))
    if started.tzinfo is None:
        raise ValueError("cycle timestamp must be timezone-aware")
    deadline_raw = raw.get("deadline")
    deadline = datetime.fromisoformat(str(deadline_raw)) if deadline_raw else None
    if deadline is not None and deadline.tzinfo is None:
        raise ValueError("cycle deadline must be timezone-aware")
    return ActiveThermalCycle(
        ThermalBinding(
            str(raw["area_id"]), str(raw["temperature_entity_id"]),
            str(raw["climate_entity_id"]),
            str(raw["outdoor_entity_id"]) if raw.get("outdoor_entity_id") else None,
            confirmed=True,
        ),
        started, _required_number(raw["start_temperature"]),
        _required_number(raw["target_temperature"]),
        _numeric_attribute(raw.get("outdoor_temperature")),
        str(raw["goal_id"]), str(raw["run_id"]),
        str(raw["user_id"]) if raw.get("user_id") else None,
        bool(raw.get("window_opened", False)),
        bool(raw.get("service_succeeded", True)),
        bool(raw.get("source_changed", False)),
        bool(raw.get("concurrent_action", False)),
        str(raw.get("cycle_id", "")),
        str(raw["starting_hvac_mode"]) if raw.get("starting_hvac_mode") else None,
        _numeric_attribute(raw.get("expected_setpoint")),
        str(raw["model_id"]) if raw.get("model_id") else None,
        deadline,
        str(raw["contamination_reason"]) if raw.get("contamination_reason") else None,
    )


def _required_number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("cycle temperature must be numeric")
    return float(value)


__all__ = ("ActiveThermalCycle", "ThermalExperienceTracker")
