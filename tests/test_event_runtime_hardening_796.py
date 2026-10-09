"""7.9.6: EventRuntime under load - priorities, interest, snapshot retry.

Findings of the 7.9.5 review (reproduced by
``scripts/event_runtime_repro_796.py``):

1. A full queue (4096) dropped every further event alike - a smoke detector
   behind 4096 ordinary sensor changes never reached any evaluation.
2. A failed snapshot build lost the batch it was taken for (up to 256
   events, critical ones included).
3. Every selected event was queued, snapshotted and evaluated even when no
   consumer was active (no categories, V12 off, no effect, no monitor goal,
   no thermal cycle).
4. ``MonitorGoalStore.async_load()`` read the file on every person
   transition and watched sensor change (``tests`` below:
   ``test_monitor_store_*``).

The bursts below are synchronous: no ``await`` and no loop turn between the
events, so the worker cannot run before the whole burst is queued.
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent import event_runtime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_PROACTIVE_CONTEXT_ENABLED,
    CONF_ROUTINE_DETECTION_ENABLED,
    CONF_SELECTED_ENTITIES,
)
from homeintent.effect_monitor import EffectMonitor  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.event_priority import (  # noqa: E402
    CRITICAL_DEVICE_CLASSES,
    EventPriority,
    is_critical_change,
)
from homeintent.event_runtime import SituationRuntime  # noqa: E402
from homeintent.goal_model import (  # noqa: E402
    GoalCondition,
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalTrigger,
)
from homeintent.learning_policy import LearningMode  # noqa: E402
from homeintent.monitor_goal import (  # noqa: E402
    MonitorGoalRuntime,
    MonitorGoalStore,
    MonitorRecord,
)
from homeintent.proactive_runtime import ProactiveRuntime  # noqa: E402
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.situation import SAFETY_CLASSES  # noqa: E402
from homeintent.situation_detection import (  # noqa: E402
    SAFETY_CLASSES as DETECTOR_SAFETY_CLASSES,
)
from homeintent.thermal_tracker import ThermalExperienceTracker  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402

T0 = datetime(2026, 10, 9, 6, tzinfo=timezone.utc)
SMOKE = "binary_sensor.rauchmelder"
MOISTURE = "binary_sensor.wassermelder"
DOOR = "binary_sensor.haustuer"
WINDOW = "binary_sensor.fenster_wohnzimmer"
LIGHT = "light.flur"
PERSON = "person.anna"
TEMP = "sensor.temperatur"
POWER = "sensor.leistung"
CLIMATE = "climate.wohnzimmer"
AREAS = {
    WINDOW: "wohnzimmer", TEMP: "wohnzimmer", CLIMATE: "wohnzimmer",
    DOOR: "flur", LIGHT: "flur",
}
CLASSES = {
    SMOKE: "smoke", MOISTURE: "moisture", DOOR: "door", WINDOW: "window",
    TEMP: "temperature", POWER: "power",
}
LOAD = 6000


class _Bus:
    """Runs a ``@callback`` inline like Home Assistant does."""

    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def async_listen(self, event_type: str, listener: Any) -> Any:
        assert event_type == "state_changed"
        assert not inspect.iscoroutinefunction(listener)
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, event: Any) -> None:
        for listener in list(self.listeners):
            listener(event)


class _House:
    """A stub state machine firing ``state_changed`` like Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.offset = 0

    def set(self, entity_id: str, state: str, **attributes: Any) -> None:
        device_class = CLASSES.get(entity_id)
        if device_class is not None:
            attributes.setdefault("device_class", device_class)
        old = self.hass.states.get(entity_id)
        new = State(entity_id, state, attributes)
        self.hass.states._states[entity_id] = new
        self.offset += 1
        self.hass.bus.fire(SimpleNamespace(
            data={"entity_id": entity_id, "old_state": old, "new_state": new},
            time_fired=T0 + timedelta(milliseconds=self.offset),
            context=SimpleNamespace(id=f"ctx-{self.offset}"),
        ))

    def seed(self, entity_id: str, state: str, **attributes: Any) -> None:
        device_class = CLASSES.get(entity_id)
        if device_class is not None:
            attributes.setdefault("device_class", device_class)
        self.hass.states._states[entity_id] = State(entity_id, state, attributes)


def _load_sensors(count: int = LOAD) -> list[str]:
    return [f"sensor.last_{index}" for index in range(count)]


def _snapshot_builder(hass: HomeAssistant, entity_ids: list[str], calls: list[int]) -> Any:
    """``build_entity_snapshots`` reading the stub state machine *now*."""

    def _build(*_args: Any) -> list[EntitySnapshot]:
        calls.append(1)
        snapshots = []
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            if state is None:
                continue
            snapshots.append(EntitySnapshot(
                entity_id, state.attributes.get("friendly_name", entity_id),
                entity_id.split(".", 1)[0], state.state,
                area_id=AREAS.get(entity_id),
                device_class=state.attributes.get("device_class"),
                unit=state.attributes.get("unit_of_measurement"),
                attributes=state.attributes,
                last_changed=T0,
            ))
        return snapshots

    return _build


class _Harness:
    def __init__(
        self, monkeypatch: Any, entity_ids: list[str], *, categories: str = "",
        options: dict[str, Any] | None = None, **fields: Any,
    ) -> None:
        self.hass = HomeAssistant()
        self.hass.bus = _Bus()
        self.house = _House(self.hass)
        self.entry = ConfigEntry(options={
            CONF_SELECTED_ENTITIES: list(entity_ids),
            CONF_AGENT_EVENT_CATEGORIES: categories,
            **(options or {}),
        })
        self.data = HomeIntentRuntimeData(**fields)
        self.runtime = SituationRuntime(self.hass, self.entry, self.data)
        self.builds: list[int] = []
        self.build = _snapshot_builder(self.hass, list(entity_ids), self.builds)
        monkeypatch.setattr(event_runtime, "build_entity_snapshots", self.build)
        # Every evaluation: (entity, state, previous, time, house view).
        self.evaluated: list[tuple[str, str, str | None, Any, dict[str, str]]] = []
        original = self.runtime._async_process_state_changed

        async def _record(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
            data = raw_event.data
            old = data.get("old_state")
            self.evaluated.append((
                data["entity_id"], data["new_state"].state,
                getattr(old, "state", None), raw_event.time_fired,
                {item.entity_id: item.state for item in entities},
            ))
            await original(raw_event, entities, by_id, **kwargs)

        monkeypatch.setattr(self.runtime, "_async_process_state_changed", _record)

    def evaluations_of(self, entity_id: str) -> list[tuple[str, str | None]]:
        return [
            (state, previous) for entity, state, previous, *_ in self.evaluated
            if entity == entity_id
        ]

    @property
    def metrics(self) -> Any:
        return self.runtime.metrics

    def pending_tasks(self) -> list[asyncio.Task[Any]]:
        return [task for task in self.hass._tasks if not task.done()]


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(500):
        pending = [task for task in hass._tasks if not task.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(async_signal=AsyncMock())


def _signals(agent: SimpleNamespace) -> list[dict[str, Any]]:
    return [call.args[0] for call in agent.async_signal.await_args_list]


def _store(tmp_path: Path, name: str = "monitor_goals.json") -> MonitorGoalStore:
    return MonitorGoalStore(tmp_path / name)


def _goal(goal_id: str, trigger: GoalTrigger, condition: GoalCondition | None = None) -> GoalModel:
    return GoalModel(
        GoalKind.MONITOR_AND_NOTIFY, goal_id=goal_id, lifecycle=GoalLifecycle.MONITOR,
        provenance=GoalProvenance("wenn", "admin"),
        trigger=trigger, conditions=(condition,) if condition is not None else (),
        recipient_person_ids=("person.anna",),
    )


def _value_goal(goal_id: str, entity_id: str) -> MonitorRecord:
    return MonitorRecord(_goal(goal_id, GoalTrigger(
        "value_change", entity_id=entity_id, delta=2.0, unit="°C",
        window_seconds=600, direction="either",
    )))


def _person_goal(goal_id: str, person: str) -> MonitorRecord:
    return MonitorRecord(_goal(goal_id, GoalTrigger(
        "person_leaves_zone", person_entity_id=person, zone_id="home",
    )))


def _nobody_goal(goal_id: str, persons: tuple[str, ...] = ()) -> MonitorRecord:
    return MonitorRecord(_goal(goal_id, GoalTrigger(
        "nobody_home", household_person_ids=persons,
    )))


def _monitor(store: MonitorGoalStore) -> MonitorGoalRuntime:
    async def _refresh() -> list[EntitySnapshot]:
        return []

    async def _deliver(*_args: Any) -> bool:
        return True

    async def _history(*_args: Any) -> list[tuple[datetime, float]]:
        return []

    return MonitorGoalRuntime(
        store, SimpleNamespace(
            async_seen_idempotency_key=AsyncMock(return_value=False),
            async_append=AsyncMock(),
        ),
        SimpleNamespace(), _refresh, _deliver, read_history=_history,
    )


# --------------------------------------------------------------------------
# Event classification
# --------------------------------------------------------------------------

def test_critical_classes_are_the_existing_safety_lists_and_nothing_else():
    assert set(SAFETY_CLASSES) <= CRITICAL_DEVICE_CLASSES
    assert set(DETECTOR_SAFETY_CLASSES) <= CRITICAL_DEVICE_CLASSES
    assert CRITICAL_DEVICE_CLASSES == frozenset({
        "smoke", "carbon_monoxide", "moisture", "gas", "safety", "tamper", "problem",
    })
    assert is_critical_change(
        SMOKE, State(SMOKE, "off", {"device_class": "smoke"}),
        State(SMOKE, "on", {"device_class": "smoke"}),
    )
    # The clearing edge counts as well.
    assert is_critical_change(
        MOISTURE, State(MOISTURE, "on", {"device_class": "moisture"}),
        State(MOISTURE, "off", {"device_class": "moisture"}),
    )
    # A gas meter is not a gas detector; a window is not a safety device.
    assert not is_critical_change(
        "sensor.gaszaehler", State("sensor.gaszaehler", "120.5", {"device_class": "gas"}),
        State("sensor.gaszaehler", "120.6", {"device_class": "gas"}),
    )
    assert not is_critical_change(
        WINDOW, State(WINDOW, "off", {"device_class": "window"}),
        State(WINDOW, "on", {"device_class": "window"}),
    )


def test_classification_of_house_wide_consumers(monkeypatch):
    harness = _Harness(monkeypatch, [TEMP, DOOR, PERSON, LIGHT], categories="safety")
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    number = lambda value: State(TEMP, value, {"device_class": "temperature"})  # noqa: E731
    assert classify(TEMP, number("20.1"), number("20.2")) is EventPriority.COALESCIBLE
    assert classify(TEMP, number("20.1"), number("unavailable")) is EventPriority.LOSSLESS
    assert classify(TEMP, None, number("20.1")) is EventPriority.LOSSLESS
    assert classify(DOOR, State(DOOR, "off"), State(DOOR, "on")) is EventPriority.LOSSLESS
    assert classify(PERSON, State(PERSON, "1"), State(PERSON, "2")) is EventPriority.LOSSLESS
    # A binary sensor with numeric labels is not a measurement.
    assert classify(DOOR, State(DOOR, "1"), State(DOOR, "2")) is EventPriority.LOSSLESS
    # Routine statistics observe every event: nothing is coalescible then.
    harness.entry.options[CONF_ROUTINE_DETECTION_ENABLED] = True
    assert classify(TEMP, number("20.1"), number("20.2")) is EventPriority.LOSSLESS


def test_specific_consumers_make_their_entities_lossless(monkeypatch, tmp_path):
    store = _store(tmp_path)
    harness = _Harness(
        monkeypatch, [TEMP, POWER, PERSON], categories="safety",
        monitor_goals=store, monitor_runtime=_monitor(store),
    )

    async def scenario() -> None:
        await store.async_save(_value_goal("goal_temp", TEMP))
        harness.runtime.async_start()
        classify = harness.runtime.interest.classify
        assert classify(
            TEMP, State(TEMP, "20.1"), State(TEMP, "20.2")
        ) is EventPriority.LOSSLESS
        assert classify(
            POWER, State(POWER, "100"), State(POWER, "101")
        ) is EventPriority.COALESCIBLE

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# 13.1 / 13.2 critical events under load
# --------------------------------------------------------------------------

def _safety_harness(monkeypatch: Any, **fields: Any) -> tuple[_Harness, SimpleNamespace]:
    agent = _agent()
    sensors = _load_sensors()
    harness = _Harness(
        monkeypatch, [*sensors, SMOKE, MOISTURE], categories="safety",
        proactive_agent=agent, **fields,
    )
    for sensor in sensors:
        harness.house.seed(sensor, "20.0")
    harness.house.seed(SMOKE, "off")
    harness.house.seed(MOISTURE, "off")
    return harness, agent


def test_critical_events_behind_a_full_queue_of_ordinary_events(monkeypatch):
    harness, agent = _safety_harness(monkeypatch)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        # 6000 ordinary sensor changes, no loop turn in between.
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        harness.house.set(SMOKE, "on")
        harness.house.set(MOISTURE, "on")
        assert len(harness.hass._tasks) == 1  # one worker, no task per event
        await _settle(harness.hass)
        assert harness.pending_tasks() == []
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert harness.evaluations_of(MOISTURE) == [("on", "off")]
    critical = {
        payload["source_entity_id"] for payload in _signals(agent)
        if payload["safety_critical"]
    }
    assert critical == {SMOKE, MOISTURE}
    assert metrics.dropped_critical == 0
    assert metrics.dropped_lossless == 0
    assert metrics.worker_starts <= 1
    assert metrics.max_queue_depth <= event_runtime.MAX_PENDING_EVENTS
    # The ordinary changes beyond the bound are counted, not hidden.
    assert metrics.received == LOAD + 2
    assert metrics.dropped_coalescible == LOAD + 2 - event_runtime.MAX_PENDING_EVENTS
    assert metrics.processed == event_runtime.MAX_PENDING_EVENTS
    assert harness.pending_tasks() == []


def test_critical_intermediate_states_reach_the_safety_evaluation(monkeypatch):
    harness, agent = _safety_harness(monkeypatch)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        harness.house.set(SMOKE, "on")
        harness.house.set(SMOKE, "off")
        harness.house.set(MOISTURE, "on")
        harness.house.set(MOISTURE, "off")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of(SMOKE) == [("on", "off"), ("off", "on")]
    assert harness.evaluations_of(MOISTURE) == [("on", "off"), ("off", "on")]
    alarms = [
        (payload["source_entity_id"], payload["expected_source_state"])
        for payload in _signals(agent) if payload["safety_critical"]
    ]
    assert alarms == [(SMOKE, "on"), (MOISTURE, "on")]
    assert harness.metrics.dropped_critical == 0


def test_critical_events_are_kept_even_without_any_consumer(monkeypatch):
    harness = _Harness(monkeypatch, [SMOKE, TEMP])
    harness.house.seed(SMOKE, "off")
    harness.house.seed(TEMP, "20.0")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(TEMP, "21.0")
        harness.house.set(SMOKE, "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert harness.evaluations_of(TEMP) == []
    assert harness.metrics.filtered_no_interest == 1


def test_critical_displaces_lossless_only_when_nothing_coalescible_is_left(
    monkeypatch, caplog
):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
    harness = _Harness(monkeypatch, [DOOR, SMOKE], categories="safety")
    harness.house.seed(DOOR, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        with caplog.at_level(logging.WARNING, logger=event_runtime.__name__):
            for index in range(4):
                harness.house.set(DOOR, "on" if index % 2 == 0 else "off")
            harness.house.set(SMOKE, "on")
            for index in range(5):
                harness.house.set(SMOKE, "off" if index % 2 == 0 else "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    # The first critical event displaced a door edge; four critical events
    # then filled the queue and nothing may displace them: the rest is the
    # documented hard limit, counted and logged as safety relevant.
    assert metrics.dropped_lossless == 4
    assert metrics.dropped_critical == 2
    safety = [record for record in caplog.records if record.getMessage().startswith("SAFETY:")]
    assert len(safety) == 1 and safety[0].levelno == logging.ERROR


# --------------------------------------------------------------------------
# 13.3 expected effects under load
# --------------------------------------------------------------------------

def test_expected_effect_sees_its_intermediate_state_under_load(monkeypatch):
    effects = EffectMonitor()
    expired: list[Any] = []
    harness, _agent_ = _safety_harness(monkeypatch, effect_monitor=effects)
    harness.entry.options[CONF_SELECTED_ENTITIES].append(LIGHT)
    harness.build = _snapshot_builder(
        harness.hass, harness.entry.options[CONF_SELECTED_ENTITIES], harness.builds
    )
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", harness.build)
    harness.house.seed(LIGHT, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        handler = effects._handler

        async def _expired(effect: Any) -> None:
            expired.append(effect)
            assert handler is not None
            await handler(effect)

        effects.set_expired_handler(_expired)
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        assert harness.runtime.interest.classify(
            LIGHT, State(LIGHT, "off"), State(LIGHT, "on")
        ) is EventPriority.LOSSLESS
        harness.house.set(LIGHT, "on")
        harness.house.set(LIGHT, "off")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert harness.evaluations_of(LIGHT) == [("on", "off"), ("off", "on")]
    assert effects.pending == ()
    assert expired == []
    assert harness.metrics.dropped_lossless == 0
    assert harness.metrics.dropped_critical == 0


def test_effect_deadline_waits_for_its_still_queued_state_change(monkeypatch):
    effects = EffectMonitor(timeout=timedelta(milliseconds=100))
    harness = _Harness(monkeypatch, [LIGHT], effect_monitor=effects)
    harness.house.seed(LIGHT, "off")
    expired: list[Any] = []
    gate = asyncio.Event() if False else None  # created inside the loop

    async def scenario() -> None:
        nonlocal gate
        gate = asyncio.Event()
        stop = harness.runtime.async_start()

        async def _expired(effect: Any) -> None:
            expired.append(effect)

        effects.set_expired_handler(_expired)
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        original = event_runtime.build_entity_snapshots
        built = asyncio.Event()

        def _slow(*args: Any) -> list[EntitySnapshot]:
            built.set()
            return original(*args)

        monkeypatch.setattr(event_runtime, "build_entity_snapshots", _slow)
        # The worker is busy longer than the effect's deadline.
        process = harness.runtime._async_process_state_changed

        async def _busy(raw_event: Any, *args: Any, **kwargs: Any) -> None:
            assert gate is not None
            await gate.wait()
            await process(raw_event, *args, **kwargs)

        monkeypatch.setattr(harness.runtime, "_async_process_state_changed", _busy)
        harness.house.set(LIGHT, "on")
        await asyncio.sleep(0.3)  # past the 100 ms deadline
        assert expired == []
        assert harness.runtime.has_pending(LIGHT)
        gate.set()
        await _settle(harness.hass)
        await asyncio.sleep(0.15)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert expired == []
    assert effects.pending == ()


def test_effect_still_expires_when_nothing_is_queued(monkeypatch):
    effects = EffectMonitor(timeout=timedelta(milliseconds=50))
    harness = _Harness(monkeypatch, [LIGHT], effect_monitor=effects)
    expired: list[Any] = []

    async def scenario() -> None:
        stop = harness.runtime.async_start()

        async def _expired(effect: Any) -> None:
            expired.append(effect.entity_id)

        effects.set_expired_handler(_expired)
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        await asyncio.sleep(0.2)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert expired == [LIGHT]


# --------------------------------------------------------------------------
# 13.4 / 13.5 monitor goals under load
# --------------------------------------------------------------------------

def _monitor_harness(
    monkeypatch: Any, tmp_path: Path, records: list[MonitorRecord], *, categories: str,
) -> tuple[_Harness, MonitorGoalStore, list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    store = _store(tmp_path)
    monitor = _monitor(store)
    transitions: list[tuple[Any, ...]] = []
    values: list[tuple[Any, ...]] = []
    process_person = monitor.async_process_person_transition
    process_value = monitor.async_process_value_change

    async def _person(*args: Any, **kwargs: Any) -> Any:
        transitions.append(args)
        return await process_person(*args, **kwargs)

    async def _value(*args: Any, **kwargs: Any) -> Any:
        values.append(args)
        return await process_value(*args, **kwargs)

    monitor.async_process_person_transition = _person  # type: ignore[method-assign]
    monitor.async_process_value_change = _value  # type: ignore[method-assign]
    sensors = _load_sensors()
    harness = _Harness(
        monkeypatch, [*sensors, PERSON, TEMP], categories=categories,
        monitor_goals=store, monitor_runtime=monitor,
    )
    for sensor in sensors:
        harness.house.seed(sensor, "20.0")
    harness.house.seed(PERSON, "home")
    harness.house.seed(TEMP, "20.0")

    async def _prepare() -> None:
        for record in records:
            await store.async_save(record)

    asyncio.run(_prepare())
    return harness, store, transitions, values


@pytest.mark.parametrize("categories", ["safety", ""], ids=["house_wide", "monitor_only"])
def test_person_leaving_and_returning_under_load(monkeypatch, tmp_path, categories):
    harness, store, transitions, _values = _monitor_harness(
        monkeypatch, tmp_path, [_person_goal("goal_anna", PERSON)], categories=categories,
    )
    reads = store.read_count

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        harness.house.set(PERSON, "not_home")
        harness.house.set(PERSON, "home")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert transitions == [(PERSON, "home", "not_home"), (PERSON, "not_home", "home")]
    assert harness.metrics.dropped_lossless == 0
    assert store.read_count == reads  # no file read per event
    if not categories:
        # Only the monitor goal listens: the ordinary sensors never queue.
        assert harness.metrics.filtered_no_interest == LOAD
        assert harness.metrics.queued == 2


def test_value_monitor_gets_each_evaluable_value_in_order_under_load(monkeypatch, tmp_path):
    harness, store, _transitions, values = _monitor_harness(
        monkeypatch, tmp_path, [_value_goal("goal_temp", TEMP)], categories="safety",
    )
    reads = store.read_count

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        harness.house.set(TEMP, "unavailable")
        harness.house.set(TEMP, "25.0")
        harness.house.set(TEMP, "21.0")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [args for args in values if args[0] == TEMP] == [(TEMP, 25.0), (TEMP, 21.0)]
    assert harness.metrics.dropped_lossless == 0
    assert store.read_count == reads


# --------------------------------------------------------------------------
# 13.6 snapshot failures
# --------------------------------------------------------------------------

def _flaky_build(harness: _Harness, monkeypatch: Any, failures: int) -> list[float]:
    attempts: list[float] = []
    build = harness.build

    def _flaky(*args: Any) -> list[EntitySnapshot]:
        attempts.append(time.monotonic())
        if failures < 0 or len(attempts) <= failures:
            raise RuntimeError("Registry nicht bereit")
        return build(*args)

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", _flaky)
    return attempts


def test_one_failed_snapshot_loses_nothing_and_duplicates_nothing(monkeypatch, caplog):
    monkeypatch.setattr(event_runtime, "MAX_BATCH_EVENTS", 2)
    harness = _Harness(monkeypatch, [DOOR, SMOKE], categories="opening_while_away")
    harness.house.seed(DOOR, "off")
    harness.house.seed(SMOKE, "off")
    attempts = _flaky_build(harness, monkeypatch, failures=1)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        with caplog.at_level(logging.ERROR, logger=event_runtime.__name__):
            harness.house.set(DOOR, "on")
            harness.house.set(SMOKE, "on")
            harness.house.set(DOOR, "off")
            harness.house.set(DOOR, "on")
            harness.house.set(SMOKE, "off")
            await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [(entity, state) for entity, state, *_ in harness.evaluated] == [
        (DOOR, "on"), (SMOKE, "on"), (DOOR, "off"), (DOOR, "on"), (SMOKE, "off"),
    ]
    # Each event saw the house of its own moment, also after the retry.
    assert [view for *_, view in harness.evaluated] == [
        {DOOR: "on", SMOKE: "off"},
        {DOOR: "on", SMOKE: "on"},
        {DOOR: "off", SMOKE: "on"},
        {DOOR: "on", SMOKE: "on"},
        {DOOR: "on", SMOKE: "off"},
    ]
    metrics = harness.metrics
    assert metrics.snapshot_failures == 1
    assert metrics.snapshot_retries >= 1
    assert metrics.worker_starts == 1
    assert len(harness.hass._tasks) == 1
    assert metrics.processed == 5
    assert len(attempts) == 1 + 3  # one failure, then three batches of <= 2
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_repeated_snapshot_failures_back_off_without_a_hot_loop(monkeypatch):
    harness = _Harness(monkeypatch, [DOOR], categories="opening_while_away")
    harness.house.seed(DOOR, "off")
    attempts = _flaky_build(harness, monkeypatch, failures=-1)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(DOOR, "on")
        await asyncio.sleep(0.4)
        stop()
        await _settle(harness.hass)

    asyncio.run(scenario())
    # 50 ms, 100 ms, 200 ms ...: a handful of attempts, not thousands.
    assert 2 <= len(attempts) <= 5
    gaps = [later - earlier for earlier, later in zip(attempts, attempts[1:])]
    assert all(gap >= 0.04 for gap in gaps)
    assert harness.evaluated == []
    assert harness.pending_tasks() == []


def test_unload_during_snapshot_retries_ends_worker_and_old_worker_stays_out(monkeypatch):
    harness = _Harness(monkeypatch, [DOOR, SMOKE], categories="opening_while_away")
    harness.house.seed(DOOR, "off")
    harness.house.seed(SMOKE, "off")
    attempts = _flaky_build(harness, monkeypatch, failures=-1)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(SMOKE, "on")
        harness.house.set(DOOR, "on")
        await asyncio.sleep(0.08)
        old_worker = harness.hass._tasks[0]
        stop()
        await asyncio.sleep(0)
        assert old_worker.done()
        assert harness.runtime._pending is None
        failed = len(attempts)
        monkeypatch.setattr(event_runtime, "build_entity_snapshots", harness.build)
        restarted = harness.runtime.async_start()
        harness.house.set(DOOR, "off")
        await _settle(harness.hass)
        assert len(attempts) == failed  # the old worker never retried again
        restarted()

    asyncio.run(scenario())
    # Nothing from before the unload, the new event once, by one new worker.
    assert [(entity, state) for entity, state, *_ in harness.evaluated] == [(DOOR, "off")]
    assert harness.metrics.worker_starts == 1
    assert harness.pending_tasks() == []


def test_critical_event_survives_snapshot_failures_and_is_evaluated(monkeypatch):
    agent = _agent()
    harness = _Harness(monkeypatch, [SMOKE], categories="safety", proactive_agent=agent)
    harness.house.seed(SMOKE, "off")
    _flaky_build(harness, monkeypatch, failures=event_runtime.SNAPSHOT_MAX_ATTEMPTS - 1)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(SMOKE, "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert [payload["source_entity_id"] for payload in _signals(agent)] == [SMOKE]
    metrics = harness.metrics
    assert metrics.snapshot_failures == event_runtime.SNAPSHOT_MAX_ATTEMPTS - 1
    assert metrics.snapshot_retries == event_runtime.SNAPSHOT_MAX_ATTEMPTS - 1
    assert metrics.snapshot_degraded_batches == 0


def test_persistent_snapshot_failure_evaluates_critical_without_registry(monkeypatch, caplog):
    monkeypatch.setattr(event_runtime, "SNAPSHOT_RETRY_BASE_SECONDS", 0.001)
    agent = _agent()
    harness = _Harness(
        monkeypatch, [SMOKE, DOOR, TEMP], categories="safety", proactive_agent=agent,
    )
    harness.house.seed(SMOKE, "off")
    harness.house.seed(DOOR, "off")
    harness.house.seed(TEMP, "20.0")
    attempts = _flaky_build(harness, monkeypatch, failures=-1)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        with caplog.at_level(logging.ERROR, logger=event_runtime.__name__):
            harness.house.set(TEMP, "20.5")
            harness.house.set(SMOKE, "on")
            harness.house.set(DOOR, "on")
            await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert len(attempts) == event_runtime.SNAPSHOT_MAX_ATTEMPTS
    assert metrics.snapshot_degraded_batches == 1
    # Critical and lossless are evaluated (no registry data), the plain
    # value change is counted as dropped - nothing vanishes silently.
    assert [(entity, state) for entity, state, *_ in harness.evaluated] == [
        (SMOKE, "on"), (DOOR, "on"),
    ]
    assert metrics.dropped_coalescible == 1
    assert metrics.dropped_critical == metrics.dropped_lossless == 0
    assert [payload["source_entity_id"] for payload in _signals(agent)] == [SMOKE]
    assert any(
        record.getMessage().startswith("SAFETY:") for record in caplog.records
    )
    assert harness.pending_tasks() == []


# --------------------------------------------------------------------------
# 13.7 no active consumer
# --------------------------------------------------------------------------

def test_no_active_consumer_queues_nothing_and_starts_no_worker(monkeypatch, tmp_path):
    effects = EffectMonitor()
    store = _store(tmp_path)
    tracker = ThermalExperienceTracker(
        SimpleNamespace(policy=SimpleNamespace(learning_mode=LearningMode.OFF)),
        state_path=tmp_path / "thermal.json",
    )
    sensors = _load_sensors()
    harness = _Harness(
        monkeypatch, [*sensors, DOOR, PERSON, LIGHT],
        effect_monitor=effects, thermal_tracker=tracker,
        monitor_goals=store, monitor_runtime=_monitor(store),
    )
    proactive = ProactiveRuntime(
        harness.hass, harness.entry, harness.data, str(tmp_path / "proactive.json")
    )
    harness.data.proactive_context = proactive
    assert proactive.enabled is False  # exists, but disabled: not a consumer
    for sensor in sensors:
        harness.house.seed(sensor, "20.0")
    reads: list[Path] = []
    original_read = Path.read_text

    def _no_read(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self)
        return original_read(self, *args, **kwargs)

    async def scenario() -> None:
        await store.async_load()
        stop = harness.runtime.async_start()
        monkeypatch.setattr(Path, "read_text", _no_read)
        for sensor in sensors:
            harness.house.set(sensor, "21.0")
        monkeypatch.setattr(Path, "read_text", original_read)
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert metrics.received == LOAD
    assert metrics.filtered_no_interest == LOAD
    assert metrics.queued == 0
    assert metrics.processed == 0
    assert metrics.snapshot_builds == 0
    assert metrics.worker_starts == 0
    assert harness.builds == []
    assert harness.hass._tasks == []
    assert reads == []
    assert not (tmp_path / "thermal.json").exists()


def test_house_wide_option_change_is_picked_up_without_restart(monkeypatch):
    harness = _Harness(monkeypatch, [DOOR])
    harness.house.seed(DOOR, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(DOOR, "on")
        harness.entry.options = {
            **harness.entry.options, CONF_AGENT_EVENT_CATEGORIES: "opening_while_away",
        }
        harness.house.set(DOOR, "off")
        harness.entry.options = {
            **harness.entry.options, CONF_AGENT_EVENT_CATEGORIES: "",
        }
        harness.house.set(DOOR, "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of(DOOR) == [("off", "on")]


def test_enabled_proactive_context_is_a_consumer(monkeypatch, tmp_path):
    harness = _Harness(
        monkeypatch, [DOOR, POWER], options={CONF_PROACTIVE_CONTEXT_ENABLED: True},
    )
    proactive = ProactiveRuntime(
        harness.hass, harness.entry, harness.data, str(tmp_path / "proactive.json")
    )
    harness.data.proactive_context = proactive
    assert proactive.enabled is True
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    door = lambda value: State(DOOR, value, {"device_class": "door"})  # noqa: E731
    assert classify(DOOR, door("off"), door("on")) is EventPriority.LOSSLESS
    assert classify(POWER, State(POWER, "5"), State(POWER, "6")) is EventPriority.COALESCIBLE


# --------------------------------------------------------------------------
# Interest index follows its providers
# --------------------------------------------------------------------------

def test_interest_follows_effect_registration_fulfilment_and_timeout(monkeypatch):
    effects = EffectMonitor(timeout=timedelta(milliseconds=50))
    harness = _Harness(monkeypatch, [LIGHT, "light.bad"], effect_monitor=effects)
    classify = harness.runtime.interest.classify
    on = (State(LIGHT, "off"), State(LIGHT, "on"))

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        assert classify(LIGHT, *on) is None
        effects.register(ServiceCallPlan("light", "turn_on", [LIGHT, "light.bad"], {}))
        assert effects.watched_entity_ids == {LIGHT, "light.bad"}
        assert classify(LIGHT, *on) is EventPriority.LOSSLESS
        # A second effect for the same entity replaces the first.
        effects.register(ServiceCallPlan("light", "turn_off", LIGHT, {}))
        assert len([e for e in effects.pending if e.entity_id == LIGHT]) == 1
        effects.observe(LIGHT, "off")  # fulfilled
        assert classify(LIGHT, *on) is None
        assert classify("light.bad", *on) is EventPriority.LOSSLESS
        await asyncio.sleep(0.15)  # light.bad times out
        assert effects.watched_entity_ids == frozenset()
        assert classify("light.bad", *on) is None
        stop()
        await effects.async_close()

    asyncio.run(scenario())


def test_interest_follows_thermal_cycles(monkeypatch, tmp_path):
    manager = SimpleNamespace(
        policy=SimpleNamespace(learning_mode=LearningMode.SILENT_LEARN),
        predictive_house=SimpleNamespace(thermal_model=lambda _area: None),
        async_record_thermal_observation=AsyncMock(),
        async_invalidate_thermal_model=AsyncMock(),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    harness = _Harness(monkeypatch, [CLIMATE, TEMP, WINDOW, DOOR], thermal_tracker=tracker)
    entities = (
        EntitySnapshot(CLIMATE, "Heizung", "climate", "heat", area_id="wohnzimmer",
                       attributes={"temperature": 22.0}),
        EntitySnapshot(TEMP, "Temperatur", "sensor", "19.0", area_id="wohnzimmer",
                       device_class="temperature", unit="°C"),
        EntitySnapshot(WINDOW, "Fenster", "binary_sensor", "off", area_id="wohnzimmer",
                       device_class="window"),
        EntitySnapshot(DOOR, "Tür", "binary_sensor", "off", area_id="flur",
                       device_class="door"),
    )
    classify = harness.runtime.interest.classify

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        assert tracker.watched_entity_ids == frozenset()
        assert classify(TEMP, State(TEMP, "19"), State(TEMP, "19.5")) is None
        tracker.observe_action(
            ServiceCallPlan("climate", "set_temperature", CLIMATE, {"temperature": 22.0}),
            entities, occurred_at=T0,
        )
        assert tracker.watched_entity_ids == {CLIMATE, TEMP, WINDOW}
        for entity_id in (CLIMATE, TEMP, WINDOW):
            assert classify(
                entity_id, State(entity_id, "1"), State(entity_id, "2")
            ) is EventPriority.LOSSLESS
        assert classify(DOOR, State(DOOR, "off"), State(DOOR, "on")) is None
        reached = (entities[0], EntitySnapshot(
            TEMP, "Temperatur", "sensor", "22.0", area_id="wohnzimmer",
            device_class="temperature", unit="°C",
        ), entities[2])
        await tracker.async_observe_states(reached, occurred_at=T0 + timedelta(minutes=30))
        assert tracker.watched_entity_ids == frozenset()
        assert classify(TEMP, State(TEMP, "22"), State(TEMP, "22.5")) is None
        stop()

    asyncio.run(scenario())


def test_interest_follows_monitor_goal_saves_and_deletes(monkeypatch, tmp_path):
    store = _store(tmp_path)
    harness = _Harness(
        monkeypatch, [TEMP, PERSON, "person.ben"],
        monitor_goals=store, monitor_runtime=_monitor(store),
    )
    classify = harness.runtime.interest.classify
    value = (State(TEMP, "20"), State(TEMP, "21"))
    leave = (State(PERSON, "home"), State(PERSON, "not_home"))
    ben = (State("person.ben", "home"), State("person.ben", "not_home"))

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        # Not loaded yet: every person and sensor counts (conservative).
        assert classify(TEMP, *value) is EventPriority.LOSSLESS
        await store.async_load()
        assert classify(TEMP, *value) is None
        assert classify(PERSON, *leave) is None
        await store.async_save(_value_goal("goal_temp", TEMP))
        assert classify(TEMP, *value) is EventPriority.LOSSLESS
        await store.async_save(_person_goal("goal_anna", PERSON))
        assert classify(PERSON, *leave) is EventPriority.LOSSLESS
        assert classify("person.ben", *ben) is None
        await store.async_save(_nobody_goal("goal_nobody", ("person.ben",)))
        assert classify("person.ben", *ben) is EventPriority.LOSSLESS
        await store.async_delete("goal_nobody")
        assert classify("person.ben", *ben) is None
        # A household goal follows the configured household: every person.
        await store.async_save(_nobody_goal("goal_household"))
        assert store.nobody_home_uses_household
        assert classify("person.ben", *ben) is EventPriority.LOSSLESS
        # A disabled goal is not a consumer.
        await store.async_save(MonitorRecord(_value_goal("goal_temp", TEMP).goal, enabled=False))
        assert classify(TEMP, *value) is None
        await store.async_delete("goal_anna")
        assert classify(PERSON, *leave) is EventPriority.LOSSLESS  # household goal
        await store.async_delete("goal_household")
        assert classify(PERSON, *leave) is None
        stop()

    asyncio.run(scenario())


def test_failed_monitor_write_leaves_cache_and_interest_unchanged(monkeypatch, tmp_path):
    store = _store(tmp_path)
    harness = _Harness(monkeypatch, [TEMP], monitor_goals=store, monitor_runtime=_monitor(store))
    classify = harness.runtime.interest.classify
    value = (State(TEMP, "20"), State(TEMP, "21"))

    def _broken(_records: Any) -> None:
        raise OSError("Datenträger voll")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        await store.async_load()
        monkeypatch.setattr(store, "_write", _broken)
        with pytest.raises(OSError):
            await store.async_save(_value_goal("goal_temp", TEMP))
        assert await store.async_load() == ()
        assert store.watched_value_entity_ids == frozenset()
        assert classify(TEMP, *value) is None
        stop()

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# 13.10 coalescing semantics
# --------------------------------------------------------------------------

def test_adjacent_value_changes_merge_into_one_evaluation(monkeypatch):
    harness = _Harness(monkeypatch, [POWER], categories="device_unavailable")
    harness.house.seed(POWER, "100")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(POWER, "101", unit_of_measurement="W", step=1)
        harness.house.set(POWER, "102", unit_of_measurement="W", step=2)
        harness.house.set(POWER, "103", unit_of_measurement="W", step=3)
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    # First old_state, last new_state, last event's time.
    assert harness.evaluations_of(POWER) == [("103", "100")]
    (_entity, _state, _previous, fired, _view) = harness.evaluated[0]
    assert fired == T0 + timedelta(milliseconds=3)
    assert harness.metrics.coalesced == 2
    assert harness.metrics.queued == 3


def test_interleaved_value_changes_merge_but_keep_the_exact_history(monkeypatch):
    harness = _Harness(monkeypatch, [POWER, TEMP, DOOR], categories="device_unavailable")
    harness.house.seed(POWER, "100")
    harness.house.seed(TEMP, "20.0")
    harness.house.seed(DOOR, "off")
    attributes: list[Any] = []
    process = harness.runtime._async_process_state_changed

    async def _attrs(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
        entity = by_id.get(raw_event.data["entity_id"])
        attributes.append((entity.entity_id, entity.state, entity.attributes.get("step")))
        await process(raw_event, entities, by_id, **kwargs)

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", _attrs)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(POWER, "101", step=1)
        harness.house.set(DOOR, "on")  # lossless, between the two
        harness.house.set(TEMP, "20.5")
        harness.house.set(POWER, "102", step=2)
        harness.house.set(TEMP, "21.0")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [
        (entity, state, previous) for entity, state, previous, *_ in harness.evaluated
    ] == [
        (DOOR, "on", "off"),
        (POWER, "102", "100"),
        (TEMP, "21.0", "20.0"),
    ]
    # The door event saw the house of its moment: power already at 101,
    # temperature not yet changed - merged evaluations moved, history did not.
    assert harness.evaluated[0][4] == {POWER: "101", TEMP: "20.0", DOOR: "on"}
    assert harness.evaluated[1][4] == {POWER: "102", TEMP: "20.5", DOOR: "on"}
    assert attributes[1] == (POWER, "102", 2)
    assert harness.metrics.coalesced == 2


def test_safety_person_effect_and_monitor_edges_are_never_merged(monkeypatch, tmp_path):
    store = _store(tmp_path)
    effects = EffectMonitor()
    harness = _Harness(
        monkeypatch, [SMOKE, PERSON, LIGHT, TEMP, DOOR], categories="device_unavailable",
        effect_monitor=effects, monitor_goals=store, monitor_runtime=_monitor(store),
    )
    for entity_id, value in ((SMOKE, "off"), (PERSON, "home"), (LIGHT, "off"),
                             (TEMP, "20.0"), (DOOR, "off")):
        harness.house.seed(entity_id, value)

    async def scenario() -> None:
        await store.async_save(_value_goal("goal_temp", TEMP))
        stop = harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        for entity_id, values in (
            (SMOKE, ("on", "off", "on")),
            (PERSON, ("not_home", "home", "not_home")),
            (LIGHT, ("on", "off", "on")),
            (TEMP, ("21.0", "22.0", "23.0")),
            (DOOR, ("on", "off", "on")),
        ):
            for value in values:
                harness.house.set(entity_id, value)
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    for entity_id in (SMOKE, PERSON, LIGHT, TEMP, DOOR):
        assert len(harness.evaluations_of(entity_id)) == 3, entity_id
    assert harness.metrics.coalesced == 0


def test_a_merge_after_a_critical_event_does_not_rewrite_its_view(monkeypatch):
    harness = _Harness(monkeypatch, [SMOKE, POWER], categories="safety")
    harness.house.seed(SMOKE, "off")
    harness.house.seed(POWER, "100")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(POWER, "101")
        harness.house.set(SMOKE, "on")
        harness.house.set(POWER, "102")
        harness.house.set(SMOKE, "off")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    views = {(entity, state): view for entity, state, _p, _t, view in harness.evaluated}
    assert views[(SMOKE, "on")] == {SMOKE: "on", POWER: "101"}
    assert views[(SMOKE, "off")] == {SMOKE: "off", POWER: "102"}
    assert harness.evaluations_of(POWER) == [("102", "100")]


def test_a_dropped_later_change_never_leaks_into_an_earlier_event(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 3)
    sensors = [f"sensor.v_{index}" for index in range(4)]
    harness = _Harness(monkeypatch, [DOOR, *sensors], categories="safety")
    harness.house.seed(DOOR, "off")
    for sensor in sensors:
        harness.house.seed(sensor, "1")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(DOOR, "on")  # lossless, queued first
        for sensor in sensors:  # two fit, two are dropped
            harness.house.set(sensor, "2")
        harness.house.set(DOOR, "off")  # evicts the oldest value change
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert metrics.dropped_coalescible == 3
    first = harness.evaluated[0]
    assert first[:2] == (DOOR, "on")
    # Nothing the door's first event could not have seen yet.
    assert all(first[4][sensor] == "1" for sensor in sensors)
    last = harness.evaluated[-1]
    assert last[:2] == (DOOR, "off")
    assert all(last[4][sensor] == "2" for sensor in sensors)


# --------------------------------------------------------------------------
# 13.11 unload under load
# --------------------------------------------------------------------------

def test_unload_during_a_burst_stops_everything_and_reload_starts_one_worker(monkeypatch):
    harness, _agent_ = _safety_harness(monkeypatch)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        harness.house.set(SMOKE, "on")
        for _ in range(3):
            await asyncio.sleep(0)
        assert 0 < harness.metrics.processed < event_runtime.MAX_PENDING_EVENTS
        stop()
        processed = harness.metrics.processed
        old_metrics = harness.metrics
        await _settle(harness.hass)
        assert harness.pending_tasks() == []
        assert harness.runtime._pending is None
        assert old_metrics.processed == processed  # nothing after the unload
        # Events after the unload are ignored by the old listener.
        assert harness.hass.bus.listeners == []
        restarted = harness.runtime.async_start()
        assert harness.metrics is not old_metrics
        assert harness.metrics.received == 0
        harness.house.set(MOISTURE, "on")
        harness.house.set(SMOKE, "off")
        assert len(harness.pending_tasks()) == 1
        await _settle(harness.hass)
        assert harness.metrics.worker_starts == 1
        assert harness.metrics.processed == 2
        restarted()

    asyncio.run(scenario())
    assert harness.pending_tasks() == []


# --------------------------------------------------------------------------
# The callback stays cheap
# --------------------------------------------------------------------------

def test_callback_does_no_snapshot_registry_or_file_work(monkeypatch, tmp_path):
    harness = _Harness(monkeypatch, _load_sensors(), categories="safety")
    for sensor in _load_sensors():
        harness.house.seed(sensor, "20.0")

    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("blocking work in the event-bus callback")

    async def scenario() -> float:
        stop = harness.runtime.async_start()
        monkeypatch.setattr(event_runtime, "build_entity_snapshots", _forbidden)
        monkeypatch.setattr(Path, "read_text", _forbidden)
        monkeypatch.setattr(Path, "write_text", _forbidden)
        started = time.perf_counter()
        for sensor in _load_sensors():
            harness.house.set(sensor, "21.0")
        elapsed = time.perf_counter() - started
        monkeypatch.setattr(event_runtime, "build_entity_snapshots", harness.build)
        monkeypatch.undo()
        stop()
        await _settle(harness.hass)
        return elapsed

    elapsed = asyncio.run(scenario())
    assert harness.metrics.received == LOAD
    assert len(harness.hass._tasks) == 1
    assert elapsed < 5.0


# --------------------------------------------------------------------------
# 13.8 MonitorGoalStore cache
# --------------------------------------------------------------------------

def _counting(store: MonitorGoalStore, monkeypatch: Any) -> list[int]:
    reads: list[int] = []
    original = store._read

    def _read() -> Any:
        reads.append(1)
        return original()

    monkeypatch.setattr(store, "_read", _read)
    return reads


def test_monitor_store_hundred_parallel_first_loads_read_once(tmp_path, monkeypatch):
    seed = _store(tmp_path)
    asyncio.run(seed.async_save(_value_goal("goal_temp", TEMP)))
    store = _store(tmp_path)
    reads = _counting(store, monkeypatch)

    async def scenario() -> list[tuple[MonitorRecord, ...]]:
        return list(await asyncio.gather(*(store.async_load() for _ in range(100))))

    results = asyncio.run(scenario())
    assert reads == [1]
    assert all(result == results[0] and len(result) == 1 for result in results)
    for _ in range(20):
        asyncio.run(store.async_load())
    assert reads == [1]


def test_monitor_store_events_read_nothing_after_the_first_load(tmp_path, monkeypatch):
    for records in ([], [_person_goal("goal_anna", PERSON), _value_goal("goal_temp", TEMP)]):
        directory = tmp_path / str(len(records))
        store = _store(directory)
        for record in records:
            asyncio.run(store.async_save(record))
        store = _store(directory)
        monitor = _monitor(store)
        reads = _counting(store, monkeypatch)

        async def scenario() -> None:
            await store.async_load()
            for index in range(50):
                await monitor.async_process_person_transition(
                    PERSON, "home" if index % 2 == 0 else "not_home",
                    "not_home" if index % 2 == 0 else "home",
                    occurred_at=T0 + timedelta(seconds=index),
                )
            for index in range(50):
                await monitor.async_process_value_change(
                    TEMP, 20.0 + index, occurred_at=T0 + timedelta(seconds=index)
                )
                await monitor.async_process_value_change(
                    "sensor.fremd", 20.0 + index, occurred_at=T0 + timedelta(seconds=index)
                )

        asyncio.run(scenario())
        assert reads == [1], records


def test_monitor_store_save_and_delete_during_the_first_load(tmp_path, monkeypatch):
    seed = _store(tmp_path)
    asyncio.run(seed.async_save(_value_goal("goal_a", TEMP)))
    store = _store(tmp_path)
    reads = _counting(store, monkeypatch)

    async def scenario() -> None:
        loads = [store.async_load() for _ in range(10)]
        await asyncio.gather(
            *loads,
            store.async_save(_value_goal("goal_b", POWER)),
            store.async_delete("goal_a"),
        )

    asyncio.run(scenario())
    assert reads == [1]
    ids = {record.goal.goal_id for record in asyncio.run(store.async_load())}
    assert ids == {"goal_b"}
    assert {item.goal.goal_id for item in asyncio.run(_store(tmp_path).async_load())} == ids


def test_monitor_store_concurrent_saves_and_save_with_delete_lose_nothing(tmp_path):
    store = _store(tmp_path)

    async def scenario() -> None:
        await asyncio.gather(
            store.async_save(_value_goal("goal_a", TEMP)),
            store.async_save(_value_goal("goal_b", POWER)),
        )
        await asyncio.gather(
            store.async_save(_person_goal("goal_c", PERSON)),
            store.async_delete("goal_a"),
        )
        # Saving an existing id replaces exactly that record.
        await store.async_save(MonitorRecord(
            _value_goal("goal_b", POWER).goal, cooldown_seconds=60,
        ))

    asyncio.run(scenario())
    records = asyncio.run(_store(tmp_path).async_load())
    assert sorted(record.goal.goal_id for record in records) == ["goal_b", "goal_c"]
    assert next(r for r in records if r.goal.goal_id == "goal_b").cooldown_seconds == 60
    assert store.watched_value_entity_ids == {POWER}
    assert store.watched_person_entity_ids == {PERSON}


def test_monitor_store_delete_of_a_missing_id_writes_nothing(tmp_path, monkeypatch):
    store = _store(tmp_path)
    asyncio.run(store.async_save(_value_goal("goal_a", TEMP)))
    writes: list[int] = []
    original = store._write
    monkeypatch.setattr(store, "_write", lambda records: writes.append(1) or original(records))
    assert asyncio.run(store.async_delete("goal_x")) is False
    assert writes == []
    assert asyncio.run(store.async_delete("goal_a")) is True
    assert writes == [1]
    assert store.watched_value_entity_ids == frozenset()


def test_monitor_store_failed_read_is_not_cached_and_can_be_retried(tmp_path, monkeypatch):
    seed = _store(tmp_path)
    asyncio.run(seed.async_save(_value_goal("goal_a", TEMP)))
    store = _store(tmp_path)
    original = store._read
    failures = [1]

    def _flaky() -> Any:
        if failures:
            failures.pop()
            raise PermissionError("gesperrt")
        return original()

    monkeypatch.setattr(store, "_read", _flaky)
    assert asyncio.run(store.async_load()) == ()
    assert not store.loaded
    # A writer must not replace unreadable goals with its own record alone.
    failures.append(1)
    with pytest.raises(PermissionError):
        asyncio.run(store.async_save(_value_goal("goal_b", POWER)))
    assert [r.goal.goal_id for r in asyncio.run(store.async_load())] == ["goal_a"]
    assert store.loaded and store.watched_value_entity_ids == {TEMP}


def test_monitor_store_reload_reads_again_on_purpose(tmp_path, monkeypatch):
    store = _store(tmp_path)
    asyncio.run(store.async_save(_value_goal("goal_a", TEMP)))
    other = _store(tmp_path)  # e.g. a second writer outside this instance
    asyncio.run(other.async_save(_value_goal("goal_b", POWER)))
    reads = _counting(store, monkeypatch)
    assert {r.goal.goal_id for r in asyncio.run(store.async_load())} == {"goal_a"}
    assert reads == []
    reloaded = asyncio.run(store.async_reload())
    assert reads == [1]
    assert {r.goal.goal_id for r in reloaded} == {"goal_a", "goal_b"}
    assert store.watched_value_entity_ids == {TEMP, POWER}


def test_monitor_store_failed_write_keeps_previous_cache(tmp_path, monkeypatch):
    store = _store(tmp_path)
    asyncio.run(store.async_save(_value_goal("goal_a", TEMP)))

    def _broken(_records: Any) -> None:
        raise OSError("Datenträger voll")

    monkeypatch.setattr(store, "_write", _broken)
    with pytest.raises(OSError):
        asyncio.run(store.async_save(_value_goal("goal_b", POWER)))
    with pytest.raises(OSError):
        asyncio.run(store.async_delete("goal_a"))
    assert [r.goal.goal_id for r in asyncio.run(store.async_load())] == ["goal_a"]
    assert store.watched_value_entity_ids == {TEMP}


# --------------------------------------------------------------------------
# 12 / 17 the calendar RuntimeWarning
# --------------------------------------------------------------------------

def test_service_stub_answers_like_home_assistant():
    services = HomeAssistant().services
    assert asyncio.run(services.async_call(
        "calendar", "get_events", {}, return_response=True,
    )) == {}
    assert asyncio.run(services.async_call("light", "turn_on", {})) is None


@pytest.mark.filterwarnings("error::RuntimeWarning")
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_calendar_read_awaits_everything(monkeypatch):
    """The arbiter shadow sentences that left a coroutine unawaited (7.9.5)."""
    from _testhaus import HouseConversation

    unraisable: list[Any] = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
    house = HouseConversation(monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for text in (
            "Was steht heute im Kalender?",
            "Welche Termine habe ich morgen?",
            "Habe ich morgen einen Termin?",
        ):
            house.say(text)
        gc.collect()
    messages = [str(item.message) for item in caught] + [
        str(getattr(item, "exc_value", item)) for item in unraisable
    ]
    assert not [message for message in messages if "never awaited" in message], messages
