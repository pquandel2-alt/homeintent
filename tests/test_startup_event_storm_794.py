"""7.9.4 P0: a burst of ``state_changed`` events must not stall Home Assistant.

Live finding (7.9.3, fresh install): after the restart the Home Assistant UI
stayed unreachable and the log showed a large number of pending
``state_changed`` tasks from ``event_runtime.py``. Disabling HomeIntent made
Home Assistant start normally again.

Causes:

- ``SituationRuntime`` listened to *every* state change with a coroutine, so
  Home Assistant created one task per event in the whole house;
- each task rebuilt the snapshots of all selected entities (listing the whole
  state machine for the Assist exposure);
- each task let ``ThermalExperienceTracker`` write and ``fsync`` its file in
  the executor, even without an active heating cycle; the writes queue on one
  lock and so held all executor threads Home Assistant needs to start;
- a burst of sensor events before the first monitor-store read started one
  disk read each.

"Before red": the same file against 7.9.3 (``3f72611``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent import event_runtime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_SELECTED_ENTITIES,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.event_runtime import SituationRuntime  # noqa: E402
from homeintent.monitor_goal import MonitorGoalStore  # noqa: E402
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402
from homeintent.thermal_model import ThermalBinding  # noqa: E402
from homeintent.thermal_tracker import (  # noqa: E402
    ActiveThermalCycle,
    ThermalExperienceTracker,
)
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

T0 = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)
WINDOW = "binary_sensor.kellerfenster"
LIGHT = "light.kellerlicht"


class _Bus:
    """Delivers events like Home Assistant: a ``@callback`` runs inline,
    a coroutine function becomes one task per event."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self.listeners: list[Any] = []
        self.tasks: list[asyncio.Task[Any]] = []

    def async_listen(self, event_type: str, listener: Any) -> Any:
        assert event_type == "state_changed"
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, event: Any) -> None:
        for listener in list(self.listeners):
            if inspect.iscoroutinefunction(listener):
                self.tasks.append(asyncio.ensure_future(listener(event)))
            else:
                listener(event)


def _event(entity_id: str, old: str, new: str, offset: int = 0) -> Any:
    return SimpleNamespace(
        data={
            "entity_id": entity_id,
            "old_state": SimpleNamespace(state=old),
            "new_state": SimpleNamespace(state=new),
        },
        time_fired=T0 + timedelta(seconds=offset),
        context=SimpleNamespace(id=f"ctx-{entity_id}-{offset}"),
    )


def _hass() -> HomeAssistant:
    hass = HomeAssistant()
    hass.bus = _Bus(hass)
    return hass


class _CountingTracker:
    def __init__(self) -> None:
        self.calls = 0

    async def async_observe_states(self, entities: Any, *, occurred_at: Any) -> None:
        self.calls += 1


def _runtime(
    hass: HomeAssistant,
    *,
    selected: list[str] | None = None,
    categories: str = "",
    **runtime_fields: Any,
) -> tuple[SituationRuntime, HomeIntentRuntimeData]:
    options: dict[str, Any] = {CONF_AGENT_EVENT_CATEGORIES: categories}
    if selected is not None:
        options[CONF_SELECTED_ENTITIES] = selected
    data = HomeIntentRuntimeData(**runtime_fields)
    return SituationRuntime(hass, ConfigEntry(options=options), data), data


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(50):
        pending = [task for task in hass._tasks if not task.done()]
        pending += [task for task in hass.bus.tasks if not task.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def test_listener_is_an_event_loop_callback_not_a_coroutine():
    hass = _hass()
    runtime, _ = _runtime(hass)
    stop = runtime.async_start()
    (listener,) = hass.bus.listeners
    # Home Assistant creates one task per event for a coroutine listener
    # and runs an unmarked plain function in the executor.
    assert not inspect.iscoroutinefunction(listener)
    assert getattr(listener, "_hass_callback", False) is True
    stop()
    assert hass.bus.listeners == []


def test_events_of_unselected_entities_create_no_task_and_no_snapshot(monkeypatch):
    hass = _hass()
    tracker = _CountingTracker()
    runtime, _ = _runtime(hass, selected=[WINDOW], thermal_tracker=tracker)
    built = []
    monkeypatch.setattr(
        event_runtime, "build_entity_snapshots",
        lambda *args: built.append(1) or [],
    )

    async def scenario() -> None:
        stop = runtime.async_start()
        for index in range(5000):
            hass.bus.fire(_event(f"sensor.fremd_{index}", "unavailable", "21.5", index))
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert hass.bus.tasks == []
    assert hass._tasks == []
    assert built == []
    assert tracker.calls == 0


def test_dynamic_exposure_filter_follows_assist_exposure(monkeypatch):
    hass = _hass()
    # 7.9.6: a selected event is queued only for an active consumer.
    runtime, _ = _runtime(hass, categories="opening_while_away")
    exposed = {WINDOW}
    monkeypatch.setattr(
        "homeintent.hass_entities.async_should_expose",
        lambda _hass, _domain, entity_id: entity_id in exposed,
    )
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [])

    async def scenario() -> int:
        stop = runtime.async_start()
        hass.bus.fire(_event(LIGHT, "off", "on"))  # not exposed
        hass.bus.fire(_event("automation.x", "off", "on"))  # domain not selectable
        assert runtime._pending is None
        hass.bus.fire(_event(WINDOW, "off", "on"))
        queued = len(runtime._pending) if runtime._pending is not None else 0
        await _settle(hass)
        stop()
        return queued

    assert asyncio.run(scenario()) == 1


def test_burst_of_selected_events_uses_one_worker_and_few_snapshots(monkeypatch):
    hass = _hass()
    agent = SimpleNamespace(async_signal=AsyncMock())
    tracker = _CountingTracker()
    runtime, _ = _runtime(
        hass, selected=[WINDOW], categories="opening_while_away",
        proactive_agent=agent, thermal_tracker=tracker,
    )
    window = EntitySnapshot(
        WINDOW, "Kellerfenster", "binary_sensor", "on",
        area_id="cellar", device_class="window",
    )
    built = []

    def _snapshots(*_args: Any) -> list[EntitySnapshot]:
        built.append(1)
        return [window]

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", _snapshots)
    events = 1000

    async def scenario() -> None:
        stop = runtime.async_start()
        for index in range(events):
            hass.bus.fire(_event(WINDOW, "off", "on", index))
        assert len(hass._tasks) == 1  # one worker, not one task per event
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert hass.bus.tasks == []
    assert len(hass._tasks) == 1
    assert tracker.calls == events  # every selected event is still processed
    assert len(built) <= -(-events // event_runtime.MAX_BATCH_EVENTS)


def test_events_are_processed_in_order_and_a_failure_does_not_stop_the_worker(monkeypatch):
    hass = _hass()
    # 7.9.6: a selected event is queued only for an active consumer.
    runtime, _ = _runtime(hass, selected=[WINDOW, LIGHT], categories="opening_while_away")
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [])
    seen: list[str] = []

    async def _process(raw_event: Any, entities: Any, by_id: Any) -> None:
        seen.append(raw_event.data["new_state"].state)
        if raw_event.data["new_state"].state == "boom":
            raise RuntimeError("defekt")

    monkeypatch.setattr(runtime, "_async_process_state_changed", _process)

    async def scenario() -> None:
        stop = runtime.async_start()
        for state in ("1", "boom", "2", "3"):
            hass.bus.fire(_event(WINDOW, "x", state))
        await _settle(hass)
        hass.bus.fire(_event(LIGHT, "x", "4"))  # a finished worker restarts
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert seen == ["1", "boom", "2", "3", "4"]


def test_stop_cancels_the_worker_and_ignores_later_events(monkeypatch):
    hass = _hass()
    # 7.9.6: a selected event is queued only for an active consumer.
    runtime, _ = _runtime(hass, selected=[WINDOW], categories="opening_while_away")
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [])
    processed: list[Any] = []

    async def _process(raw_event: Any, entities: Any, by_id: Any) -> None:
        processed.append(raw_event)
        await asyncio.sleep(0)

    monkeypatch.setattr(runtime, "_async_process_state_changed", _process)

    async def scenario() -> None:
        stop = runtime.async_start()
        for index in range(100):
            hass.bus.fire(_event(WINDOW, "off", "on", index))
        stop()
        runtime.async_enqueue_state_changed(_event(WINDOW, "off", "on", 999))
        await _settle(hass)

    asyncio.run(scenario())
    assert processed == []
    assert runtime._pending is None
    assert all(task.done() for task in hass._tasks)


def test_queue_is_bounded_and_overflow_is_reported_once(monkeypatch, caplog):
    hass = _hass()
    # 7.9.6: a selected event is queued only for an active consumer.
    runtime, _ = _runtime(hass, selected=[WINDOW], categories="opening_while_away")
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 10)
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [])

    async def scenario() -> None:
        stop = runtime.async_start()
        with caplog.at_level(logging.WARNING, logger=event_runtime.__name__):
            for index in range(25):
                hass.bus.fire(_event(WINDOW, "off", "on", index))
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    assert runtime._dropped == 15
    warnings = [r for r in caplog.records if "behind on state changes" in r.getMessage()]
    assert len(warnings) == 1


def _cycle(**changes: Any) -> ActiveThermalCycle:
    cycle = ActiveThermalCycle(
        ThermalBinding("wohnzimmer", "sensor.temp", "climate.heizung", None, confirmed=True),
        T0, 19.0, 22.0, None, "goal_1", "run_1",
        cycle_id="cycle_1", starting_hvac_mode="heat", expected_setpoint=22.0,
    )
    for key, value in changes.items():
        cycle = ActiveThermalCycle(**{**cycle.__dict__, key: value})
    return cycle


def _climate_entities(temperature: str, *, window: str = "off") -> tuple[EntitySnapshot, ...]:
    return (
        EntitySnapshot(
            "climate.heizung", "Heizung", "climate", "heat", area_id="wohnzimmer",
            attributes={"temperature": 22.0},
        ),
        EntitySnapshot(
            "sensor.temp", "Temperatur", "sensor", temperature, area_id="wohnzimmer",
            unit="°C", device_class="temperature",
        ),
        EntitySnapshot(
            "binary_sensor.fenster", "Fenster", "binary_sensor", window,
            area_id="wohnzimmer", device_class="window",
        ),
    )


def _count_writes(monkeypatch: Any, tracker: ThermalExperienceTracker) -> list[int]:
    writes: list[int] = []
    original = tracker._write_document

    def _write(document: dict[str, object]) -> None:
        writes.append(1)
        original(document)

    monkeypatch.setattr(tracker, "_write_document", _write)
    return writes


def test_thermal_tracker_without_active_cycle_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "cycles.json"
    tracker = ThermalExperienceTracker(SimpleNamespace(), state_path=path)
    writes = _count_writes(monkeypatch, tracker)

    async def scenario() -> None:
        for index in range(500):
            await tracker.async_observe_states(
                _climate_entities("20.0"), occurred_at=T0 + timedelta(seconds=index)
            )

    asyncio.run(scenario())
    assert writes == []
    assert not path.exists()


def test_thermal_tracker_writes_only_when_a_cycle_changes(tmp_path, monkeypatch):
    path = tmp_path / "cycles.json"
    manager = SimpleNamespace(
        async_record_thermal_observation=AsyncMock(),
        async_invalidate_thermal_model=AsyncMock(),
    )
    tracker = ThermalExperienceTracker(manager, state_path=path)
    tracker._active["climate.heizung"] = _cycle()
    writes = _count_writes(monkeypatch, tracker)

    async def scenario() -> None:
        # Still heating, window closed: nothing changes, nothing is written.
        for index in range(1, 50):
            await tracker.async_observe_states(
                _climate_entities("20.0"), occurred_at=T0 + timedelta(minutes=index)
            )
        assert writes == []
        # The window opens once: one write; staying open writes no more.
        for index in range(50, 60):
            await tracker.async_observe_states(
                _climate_entities("20.5", window="on"),
                occurred_at=T0 + timedelta(minutes=index),
            )
        assert len(writes) == 1
        assert tracker.active[0].window_opened
        # Target reached: the cycle completes and is recorded and written.
        await tracker.async_observe_states(
            _climate_entities("22.0"), occurred_at=T0 + timedelta(minutes=70)
        )

    asyncio.run(scenario())
    assert len(writes) == 2
    assert tracker.active == ()
    manager.async_record_thermal_observation.assert_awaited_once()
    observation = manager.async_record_thermal_observation.await_args.args[1]
    assert observation.reached_target is True
    assert observation.window_opened is True


def test_monitor_store_reads_once_for_a_burst_before_the_first_load(tmp_path, monkeypatch):
    store = MonitorGoalStore(tmp_path / "monitor_goals.json")
    reads: list[int] = []
    original = store._read

    def _read() -> Any:
        reads.append(1)
        return original()

    monkeypatch.setattr(store, "_read", _read)

    async def scenario() -> list[frozenset[str]]:
        return list(await asyncio.gather(
            *(store.async_watched_entities() for _ in range(500))
        ))

    results = asyncio.run(scenario())
    assert reads == [1]
    assert set(results) == {frozenset()}


def test_full_pipeline_still_signals_a_selected_situation(monkeypatch):
    # Unchanged behaviour through the real listener: one notice per event.
    hass = _hass()
    agent = SimpleNamespace(async_signal=AsyncMock())
    runtime, _ = _runtime(
        hass, selected=[WINDOW], categories="opening_while_away", proactive_agent=agent,
    )
    window = EntitySnapshot(
        WINDOW, "Kellerfenster", "binary_sensor", "on",
        area_id="cellar", device_class="window",
    )
    monkeypatch.setattr(event_runtime, "build_entity_snapshots", lambda *_: [window])

    async def scenario() -> None:
        stop = runtime.async_start()
        night = _event(WINDOW, "off", "on")
        night.time_fired = datetime(2026, 9, 1, 23, tzinfo=timezone.utc)
        hass.bus.fire(night)
        hass.bus.fire(night)  # the same occurrence twice: signalled once
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    agent.async_signal.assert_awaited_once()
    payload = agent.async_signal.await_args.args[0]
    assert payload["mode"] == "inform"
    assert payload["source_entity_id"] == WINDOW
