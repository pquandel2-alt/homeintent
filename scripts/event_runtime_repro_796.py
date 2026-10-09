#!/usr/bin/env python3
"""Reproduce the four EventRuntime findings of the 7.9.6 review.

Runs against the stub Home Assistant and only uses what 7.9.5 already had,
so the same script shows the defect on 7.9.5 and the fix on 7.9.6:

1. ``critical``: 4096 ordinary sensor events (no loop turn in between),
   then a smoke detector ``off -> on``: is the smoke event evaluated?
2. ``snapshot``: the first snapshot build fails: are the queued events
   still evaluated?
3. ``no_consumers``: a selected event with no active consumer: is it
   queued / does a worker start?
4. ``monitor_reads``: 50 person transitions without monitor goals: how many
   monitor store file reads?

    python scripts/event_runtime_repro_796.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402
from homeintent import event_runtime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_SELECTED_ENTITIES,
)
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.monitor_goal import MonitorGoalRuntime, MonitorGoalStore  # noqa: E402
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402

T0 = datetime(2026, 10, 9, 6, tzinfo=timezone.utc)
SMOKE = "binary_sensor.rauchmelder"


class _Bus:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def async_listen(self, _event_type: str, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, event: Any) -> None:
        for listener in list(self.listeners):
            listener(event)


def _event(entity_id: str, old: State | None, new: State, offset: int) -> Any:
    return SimpleNamespace(
        data={"entity_id": entity_id, "old_state": old, "new_state": new},
        time_fired=T0 + timedelta(milliseconds=offset),
        context=SimpleNamespace(id=f"ctx-{offset}"),
    )


def _runtime(selected: list[str], categories: str, **fields: Any) -> Any:
    hass = HomeAssistant()
    hass.bus = _Bus()
    entry = ConfigEntry(options={
        CONF_SELECTED_ENTITIES: selected, CONF_AGENT_EVENT_CATEGORIES: categories,
    })
    runtime = event_runtime.SituationRuntime(hass, entry, HomeIntentRuntimeData(**fields))
    return hass, runtime


def _snapshots(hass: HomeAssistant, entity_ids: list[str]) -> Any:
    def build(*_args: Any) -> list[EntitySnapshot]:
        result = []
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            if state is not None:
                result.append(EntitySnapshot(
                    entity_id, entity_id, entity_id.split(".")[0], state.state,
                    device_class=state.attributes.get("device_class"),
                    attributes=state.attributes,
                ))
        return result

    return build


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(200):
        pending = [task for task in hass._tasks if not task.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _set(hass: HomeAssistant, entity_id: str, value: str, offset: int, **attributes: Any) -> None:
    old = hass.states.get(entity_id)
    new = State(entity_id, value, attributes)
    hass.states._states[entity_id] = new
    hass.bus.fire(_event(entity_id, old, new, offset))


def critical() -> dict[str, Any]:
    sensors = [f"sensor.s_{index}" for index in range(4096)]
    hass, runtime = _runtime([*sensors, SMOKE], "safety")
    event_runtime.build_entity_snapshots = _snapshots(hass, [*sensors, SMOKE])
    for index, sensor in enumerate(sensors):
        hass.states._states[sensor] = State(sensor, "20", {"device_class": "temperature"})
    hass.states._states[SMOKE] = State(SMOKE, "off", {"device_class": "smoke"})
    processed: list[str] = []
    original = runtime._async_process_state_changed

    async def _count(raw_event: Any, *args: Any, **kwargs: Any) -> None:
        processed.append(raw_event.data["entity_id"])
        await original(raw_event, *args, **kwargs)

    runtime._async_process_state_changed = _count

    async def scenario() -> int:
        stop = runtime.async_start()
        for index, sensor in enumerate(sensors):
            _set(hass, sensor, "21", index, device_class="temperature")
        _set(hass, SMOKE, "on", 5000, device_class="smoke")
        queued = len(runtime._pending or ())
        await _settle(hass)
        stop()
        return queued

    queued = asyncio.run(scenario())
    return {
        "queued_before_drain": queued,
        "dropped": runtime._dropped,
        "processed": len(processed),
        "smoke_processed": SMOKE in processed,
    }


def snapshot() -> dict[str, Any]:
    door = "binary_sensor.tuer"
    hass, runtime = _runtime([door], "opening_while_away")
    build = _snapshots(hass, [door])
    calls: list[int] = []

    def flaky(*args: Any) -> list[EntitySnapshot]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Registry nicht bereit")
        return build(*args)

    event_runtime.build_entity_snapshots = flaky
    hass.states._states[door] = State(door, "off", {"device_class": "door"})
    processed: list[str] = []

    async def _count(raw_event: Any, *args: Any, **kwargs: Any) -> None:
        processed.append(raw_event.data["new_state"].state)

    runtime._async_process_state_changed = _count

    async def scenario() -> None:
        stop = runtime.async_start()
        for offset, value in enumerate(("on", "off", "on")):
            _set(hass, door, value, offset, device_class="door")
        await _settle(hass)
        stop()

    asyncio.run(scenario())
    return {"events": 3, "processed": processed, "lost": 3 - len(processed)}


def no_consumers() -> dict[str, Any]:
    sensor = "sensor.temperatur"
    hass, runtime = _runtime([sensor], "")
    event_runtime.build_entity_snapshots = _snapshots(hass, [sensor])
    hass.states._states[sensor] = State(sensor, "20")

    async def scenario() -> tuple[int, int]:
        stop = runtime.async_start()
        _set(hass, sensor, "21", 1)
        queued = len(runtime._pending or ())
        workers = len(hass._tasks)
        await _settle(hass)
        stop()
        return queued, workers

    queued, workers = asyncio.run(scenario())
    return {"no_consumers_queued": queued, "no_consumers_workers": workers}


def monitor_reads() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        store = MonitorGoalStore(Path(directory) / "monitor_goals.json")
        reads: list[int] = []
        original = store._read

        def counting() -> Any:
            reads.append(1)
            return original()

        store._read = counting  # type: ignore[method-assign]

        async def refresh() -> list[EntitySnapshot]:
            return []

        async def deliver(*_args: Any) -> bool:
            return True

        monitor = MonitorGoalRuntime(
            store, SimpleNamespace(), SimpleNamespace(), refresh, deliver,
        )

        async def scenario() -> None:
            await store.async_load()
            for index in range(50):
                await monitor.async_process_person_transition(
                    "person.anna", "home" if index % 2 == 0 else "not_home",
                    "not_home" if index % 2 == 0 else "home",
                    occurred_at=T0 + timedelta(seconds=index),
                )

        asyncio.run(scenario())
        return {"person_transitions": 50, "store_reads_after_first_load": len(reads) - 1}


def main() -> int:
    original = event_runtime.build_entity_snapshots
    for name, scenario in (
        ("critical", critical), ("snapshot", snapshot),
        ("no_consumers", no_consumers), ("monitor_reads", monitor_reads),
    ):
        try:
            print(f"{name}: {scenario()}")
        finally:
            event_runtime.build_entity_snapshots = original
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
