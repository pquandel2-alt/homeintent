"""7.9.7 against a REAL Home Assistant: ranks, targeted interest, memory.

7.9.6 kept every non-numeric event of a configured category "lossless" in
one rank with expected effects and monitor goals: behind 4096 ordinary
category events an expected effect's state change was dropped, and a
configured ``safety`` category queued every switch and sensor of the house.
Here Home Assistant's real state machine, event bus and task tracking run
the bursts - all synchronous, no ``await`` and no loop turn in between.

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

from custom_components.homeintent import event_runtime
from custom_components.homeintent.const import DOMAIN
from custom_components.homeintent.goal_model import (
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalTrigger,
)
from custom_components.homeintent.monitor_goal import MonitorRecord
from custom_components.homeintent.service_call import ServiceCallPlan

SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    "agent_auto_enabled": False,
    "documents_enabled": False,
}
FULL = 4096
LOAD = 6000
SMOKE = "binary_sensor.rauchmelder"
MOISTURE = "binary_sensor.wassermelder"
LIGHT = "light.flur"
PERSON = "person.anna"
EMPTY = {
    "retained_entries": 0, "retained_index": 0, "retained_latest": 0,
    "retained_gaps": 0, "retained_carry": 0, "retained_tombstones": 0,
    "retained_unprocessed": 0, "history_degraded_active": 0,
}


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


def _delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    """Counters of the burst alone; maxima are kept as they are."""
    return {
        key: value if key.startswith("max_") else value - before.get(key, 0)
        for key, value in after.items()
    }


def _homeintent_workers() -> list[asyncio.Task[Any]]:
    return [
        task for task in asyncio.all_tasks()
        if not task.done() and task.get_name() == "HomeIntent situation events"
    ]


def _person_goal() -> MonitorRecord:
    return MonitorRecord(GoalModel(
        GoalKind.MONITOR_AND_NOTIFY, goal_id="goal_anna", lifecycle=GoalLifecycle.MONITOR,
        provenance=GoalProvenance("wenn", "admin"),
        trigger=GoalTrigger("person_leaves_zone", person_entity_id=PERSON, zone_id="home"),
        recipient_person_ids=(PERSON,),
    ))


async def test_protected_and_critical_survive_4096_category_events_without_a_yield(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    lights = [f"light.last_{index}" for index in range(FULL)]
    for light in lights:
        hass.states.async_set(light, "off")
    hass.states.async_set(LIGHT, "off")
    hass.states.async_set(PERSON, "home")
    hass.states.async_set(SMOKE, "off", {"device_class": "smoke"})
    hass.states.async_set(MOISTURE, "off", {"device_class": "moisture"})
    options = dict(SETUP_OPTIONS)
    options["selected_entities"] = [*lights, LIGHT, PERSON, SMOKE, MOISTURE]
    # Ordinary, non-numeric category events: lights switched on while
    # ``light_unoccupied`` is configured.
    options["agent_event_categories"] = "safety,light_unoccupied"
    entry = await _setup(hass, options)
    data = entry.runtime_data
    runtime = data.situation_runtime
    store = data.monitor_goals
    await store.async_save(_person_goal())
    reads_before = store.read_count

    transitions: list[tuple[str, str]] = []
    process_person = data.monitor_runtime.async_process_person_transition

    async def _person(entity_id: str, old: str, new: str, **kwargs: Any) -> Any:
        transitions.append((old, new))
        return await process_person(entity_id, old, new, **kwargs)

    monkeypatch.setattr(data.monitor_runtime, "async_process_person_transition", _person)
    evaluated: list[tuple[str, str]] = []
    process = event_runtime.SituationRuntime._async_process_state_changed

    async def _record(self: Any, raw_event: Any, *args: Any, **kwargs: Any) -> None:
        evaluated.append((raw_event.data["entity_id"], raw_event.data["new_state"].state))
        await process(self, raw_event, *args, **kwargs)

    monkeypatch.setattr(event_runtime.SituationRuntime, "_async_process_state_changed", _record)
    expired: list[str] = []

    async def _expired(effect: Any) -> None:
        expired.append(effect.entity_id)

    data.effect_monitor.set_expired_handler(_expired)
    data.effect_monitor.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    await hass.async_block_till_done()
    counters_before = runtime.metrics.as_dict()
    tasks_before = len(asyncio.all_tasks())
    started = time.monotonic()
    for light in lights:  # no await, no loop turn
        hass.states.async_set(light, "on")
    queue_after_load = runtime.retention()["retained_entries"]
    hass.states.async_set(LIGHT, "on")
    hass.states.async_set(PERSON, "not_home")
    hass.states.async_set(PERSON, "home")
    hass.states.async_set(SMOKE, "on", {"device_class": "smoke"})
    hass.states.async_set(MOISTURE, "on", {"device_class": "moisture"})
    burst_seconds = time.monotonic() - started
    tasks_after_burst = len(asyncio.all_tasks())
    workers_after_burst = len(_homeintent_workers())
    await hass.async_block_till_done(wait_background_tasks=True)
    elapsed = time.monotonic() - started

    metrics = _delta(runtime.metrics.as_dict(), counters_before)
    retained = runtime.retention()  # before the unload
    pending = len(_homeintent_workers())
    _keep("ha_4096_category_then_protected_and_critical", {
        **metrics, **retained,
        "queue_after_load": queue_after_load,
        "monitor_store_reads": store.read_count - reads_before,
        "elapsed_seconds": round(elapsed, 3),
        "burst_seconds": round(burst_seconds, 3),
        "pending_tasks": pending,
        "tasks_created_by_burst": tasks_after_burst - tasks_before,
    })
    assert queue_after_load == FULL
    assert workers_after_burst == 1
    assert tasks_after_burst - tasks_before < 50
    assert metrics["worker_starts"] <= 1
    assert (LIGHT, "on") in evaluated
    assert data.effect_monitor.pending == ()  # observed, not expired
    assert expired == []
    assert transitions == [("home", "not_home"), ("not_home", "home")]
    assert (SMOKE, "on") in evaluated and (MOISTURE, "on") in evaluated
    assert metrics["dropped_protected"] == 0
    assert metrics["dropped_critical"] == 0
    assert metrics["dropped_category"] == 5  # one ordinary event per later one
    assert metrics["coalesced"] == 0
    assert store.read_count == reads_before
    assert pending == 0
    assert {key: retained[key] for key in EMPTY} == EMPTY

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _homeintent_workers() == []


async def test_safety_category_queues_no_ordinary_switch_or_sensor(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    switches = [f"switch.last_{index}" for index in range(LOAD // 2)]
    sensors = [f"sensor.last_{index}" for index in range(LOAD - LOAD // 2)]
    for switch in switches:
        hass.states.async_set(switch, "off")
    for sensor in sensors:
        hass.states.async_set(sensor, "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(SMOKE, "off", {"device_class": "smoke"})
    options = dict(SETUP_OPTIONS)
    options["selected_entities"] = [*switches, *sensors, SMOKE]
    options["agent_event_categories"] = "safety"
    entry = await _setup(hass, options)
    runtime = entry.runtime_data.situation_runtime
    builds: list[int] = []
    build = event_runtime.build_entity_snapshots
    monkeypatch.setattr(
        event_runtime, "build_entity_snapshots",
        lambda *args: builds.append(1) or build(*args),
    )

    await hass.async_block_till_done()
    counters_before = runtime.metrics.as_dict()
    started = time.monotonic()
    for switch in switches:  # no await, no loop turn
        hass.states.async_set(switch, "on")
    for sensor in sensors:
        hass.states.async_set(sensor, "21.0", {"unit_of_measurement": "°C"})
    burst_seconds = time.monotonic() - started
    ordinary = _delta(runtime.metrics.as_dict(), counters_before)
    workers_after_ordinary = len(_homeintent_workers())
    await hass.async_block_till_done(wait_background_tasks=True)
    builds_after_ordinary = len(builds)

    hass.states.async_set(SMOKE, "on", {"device_class": "smoke"})
    await hass.async_block_till_done(wait_background_tasks=True)
    metrics = _delta(runtime.metrics.as_dict(), counters_before)
    retained = runtime.retention()
    _keep("ha_safety_category_6000_ordinary", {
        **metrics, **retained,
        "burst_seconds": round(burst_seconds, 3),
        "snapshot_builds_for_ordinary_events": builds_after_ordinary,
        "pending_tasks": len(_homeintent_workers()),
    })
    assert ordinary["received"] == LOAD
    assert ordinary["filtered_no_interest"] == LOAD
    assert ordinary["filtered_by_category"] == LOAD
    assert ordinary["queued"] == 0
    assert ordinary["worker_starts"] == 0
    assert workers_after_ordinary == 0
    assert builds_after_ordinary == 0
    # The smoke detector stays relevant.
    assert metrics["queued"] == 1
    assert metrics["processed"] == 1
    assert metrics["dropped_critical"] == 0
    assert builds == [1]
    assert {key: retained[key] for key in EMPTY} == EMPTY

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
