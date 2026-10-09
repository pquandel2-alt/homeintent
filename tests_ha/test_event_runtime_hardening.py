"""7.9.6 against a REAL Home Assistant: 6000 state changes without a yield.

The 7.9.4 storm test hands the loop back every 250 sensors. Here all 6000
ordinary sensor changes are set in one synchronous burst - the worker cannot
run before the end - followed by an expected effect's light going
``off -> on -> off`` and a smoke and a water detector going off. Home
Assistant's real state machine, event bus and task tracking are used.

Set ``HOMEINTENT_METRICS_OUT`` to a file path to keep the measured metrics.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeintent import event_runtime, thermal_tracker
from custom_components.homeintent.const import DOMAIN
from custom_components.homeintent.service_call import ServiceCallPlan

SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    "agent_auto_enabled": False,
    "documents_enabled": False,
}
LOAD = 6000
SMOKE = "binary_sensor.rauchmelder"
MOISTURE = "binary_sensor.wassermelder"
LIGHT = "light.flur"


def _keep(name: str, metrics: dict[str, Any]) -> None:
    print(f"\n{name}: {json.dumps(metrics, sort_keys=True)}")
    target = os.environ.get("HOMEINTENT_METRICS_OUT")
    if target:
        path = Path(target)
        stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        stored[name] = metrics
        path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")


async def _setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, title="HomeIntent", data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _count_thermal_writes(monkeypatch: Any) -> list[Path]:
    writes: list[Path] = []
    original = thermal_tracker.ThermalExperienceTracker._write_document

    def _count(self: Any, document: dict[str, object]) -> None:
        writes.append(Path(str(self._state_path)))
        original(self, document)

    monkeypatch.setattr(thermal_tracker.ThermalExperienceTracker, "_write_document", _count)
    return writes


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    """Counters of the burst alone (Home Assistant changes states of its own
    during setup); maxima are kept as they are."""
    return {
        key: value if key == "max_queue_depth" else value - before.get(key, 0)
        for key, value in after.items()
    }


def _homeintent_workers() -> list[asyncio.Task[Any]]:
    return [
        task for task in asyncio.all_tasks()
        if not task.done() and task.get_name() == "HomeIntent situation events"
    ]


async def test_six_thousand_changes_without_a_yield_keep_critical_and_lossless(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    sensors = [f"sensor.last_{index}" for index in range(LOAD)]
    for sensor in sensors:
        hass.states.async_set(sensor, "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(SMOKE, "off", {"device_class": "smoke"})
    hass.states.async_set(MOISTURE, "off", {"device_class": "moisture"})
    hass.states.async_set(LIGHT, "off")
    options = dict(SETUP_OPTIONS)
    options["selected_entities"] = [*sensors, SMOKE, MOISTURE, LIGHT]
    # A house-wide consumer: every selected change is of interest.
    options["agent_event_categories"] = "safety"
    entry = await _setup(hass, options)
    data = entry.runtime_data
    runtime = data.situation_runtime
    store = data.monitor_goals
    reads_before = store.read_count
    writes = _count_thermal_writes(monkeypatch)

    evaluated: list[tuple[str, str]] = []
    process = event_runtime.SituationRuntime._async_process_state_changed

    async def _record(self: Any, raw_event: Any, *args: Any, **kwargs: Any) -> None:
        evaluated.append((raw_event.data["entity_id"], raw_event.data["new_state"].state))
        await process(self, raw_event, *args, **kwargs)

    monkeypatch.setattr(event_runtime.SituationRuntime, "_async_process_state_changed", _record)

    # An expected effect HomeIntent waits for (registered directly: the
    # monitor only observes, nothing is switched).
    data.effect_monitor.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    await hass.async_block_till_done()
    counters_before = runtime.metrics.as_dict()
    tasks_before = len(asyncio.all_tasks())
    started = time.monotonic()
    for sensor in sensors:  # no await, no loop turn
        hass.states.async_set(sensor, "21.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(LIGHT, "on")
    hass.states.async_set(LIGHT, "off")
    hass.states.async_set(SMOKE, "on", {"device_class": "smoke"})
    hass.states.async_set(MOISTURE, "on", {"device_class": "moisture"})
    burst_seconds = time.monotonic() - started
    tasks_after_burst = len(asyncio.all_tasks())
    assert len(_homeintent_workers()) == 1

    # The loop stays responsive while the worker drains the queue.
    gaps: list[float] = []

    async def _probe() -> None:
        while _homeintent_workers():
            before = time.monotonic()
            await asyncio.sleep(0.005)
            gaps.append(time.monotonic() - before)

    probe = hass.async_create_task(_probe())
    await hass.async_block_till_done(wait_background_tasks=True)
    await probe
    elapsed = time.monotonic() - started

    metrics = _delta(runtime.metrics.as_dict(), counters_before)
    pending = len(_homeintent_workers())
    _keep("sync_6000_with_consumers", {
        **metrics,
        "monitor_store_reads": store.read_count - reads_before,
        "elapsed_seconds": round(elapsed, 3),
        "burst_seconds": round(burst_seconds, 3),
        "max_loop_gap_seconds": round(max(gaps, default=0.0), 3),
        "pending_tasks": pending,
        "tasks_created_by_burst": tasks_after_burst - tasks_before,
    })
    assert tasks_after_burst - tasks_before < 50  # no task per event
    assert metrics["worker_starts"] <= 1
    assert metrics["received"] == LOAD + 4
    assert metrics["dropped_critical"] == 0
    assert metrics["dropped_lossless"] == 0
    assert (SMOKE, "on") in evaluated and (MOISTURE, "on") in evaluated
    assert [item for item in evaluated if item[0] == LIGHT] == [(LIGHT, "on"), (LIGHT, "off")]
    assert data.effect_monitor.pending == ()  # observed, not expired
    assert writes == []  # no thermal cycle, no thermal write
    assert store.read_count == reads_before  # no monitor read per event
    assert pending == 0
    assert max(gaps, default=0.0) < 1.0
    assert elapsed < 60

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _homeintent_workers() == []


async def test_default_configuration_without_consumers_queues_nothing(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    sensors = [f"sensor.ruhe_{index}" for index in range(LOAD)]
    for sensor in sensors:
        hass.states.async_set(sensor, "20.0")
    options = dict(SETUP_OPTIONS)
    options["selected_entities"] = sensors
    entry = await _setup(hass, options)
    runtime = entry.runtime_data.situation_runtime
    store = entry.runtime_data.monitor_goals
    reads_before = store.read_count
    writes = _count_thermal_writes(monkeypatch)
    builds: list[int] = []
    build = event_runtime.build_entity_snapshots
    monkeypatch.setattr(
        event_runtime, "build_entity_snapshots",
        lambda *args: builds.append(1) or build(*args),
    )
    # The V12 context exists but is disabled (default): not a consumer.
    assert entry.runtime_data.proactive_context is not None
    assert entry.runtime_data.proactive_context.enabled is False

    await hass.async_block_till_done()
    counters_before = runtime.metrics.as_dict()
    started = time.monotonic()
    for sensor in sensors:
        hass.states.async_set(sensor, "21.0")
    burst_seconds = time.monotonic() - started
    assert _homeintent_workers() == []
    await hass.async_block_till_done(wait_background_tasks=True)

    metrics = _delta(runtime.metrics.as_dict(), counters_before)
    _keep("sync_6000_without_consumers", {
        **metrics,
        "monitor_store_reads": store.read_count - reads_before,
        "burst_seconds": round(burst_seconds, 3),
        "pending_tasks": len(_homeintent_workers()),
    })
    assert metrics["received"] == LOAD
    assert metrics["filtered_no_interest"] == LOAD
    assert metrics["queued"] == 0
    assert metrics["processed"] == 0
    assert metrics["snapshot_builds"] == 0
    assert metrics["worker_starts"] == 0
    assert builds == []
    assert writes == []
    assert store.read_count == reads_before

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
