"""7.9.5 against a REAL Home Assistant: every event sees its own state.

7.9.4 evaluated a whole batch of queued state changes against one snapshot
taken after the batch. With Home Assistant's real state machine and event
bus, each evaluated event must see its entity exactly as the event set it
(state and attributes) and the rest of the house as it was at that moment,
while the burst still costs no task per event.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeintent import event_runtime
from custom_components.homeintent.const import DOMAIN

SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    "agent_auto_enabled": False,
    "documents_enabled": False,
}
SENSORS = 600
DOOR = "binary_sensor.haustuer"


async def test_burst_evaluates_each_event_against_the_house_at_that_moment(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    sensors = [f"sensor.flow_{index}" for index in range(SENSORS)]
    options = dict(SETUP_OPTIONS)
    options["selected_entities"] = [DOOR, *sensors]
    # 7.9.6: selected events are evaluated only for an active consumer;
    # 7.9.7: a category only for its own inputs - routine detection with its
    # category still observes every selected entity.
    options["agent_event_categories"] = "routine_anomaly"
    options["routine_detection_enabled"] = True
    entry = MockConfigEntry(domain=DOMAIN, title="HomeIntent", data={}, options=options)
    entry.add_to_hass(hass)
    hass.states.async_set(DOOR, "off", {"device_class": "door"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    seen: list[tuple[str, str, Any, str | None, str]] = []
    process = event_runtime.SituationRuntime._async_process_state_changed

    async def _record(self: Any, raw_event: Any, entities: Any, by_id: Any) -> None:
        entity_id = raw_event.data["entity_id"]
        entity = by_id[entity_id]
        door = by_id.get(DOOR)
        seen.append((
            entity_id,
            entity.state,
            entity.attributes.get("step"),
            entity.unit,
            door.state if door is not None else "missing",
        ))
        await process(self, raw_event, entities, by_id)

    monkeypatch.setattr(
        event_runtime.SituationRuntime, "_async_process_state_changed", _record
    )

    expected: list[tuple[str, str, Any, str | None, str]] = []
    door = "off"
    before = len(asyncio.all_tasks())
    peak = before
    # One synchronous burst: the worker only runs afterwards, so 7.9.4 saw
    # every event through the final snapshot.
    for index, entity_id in enumerate(sensors):
        hass.states.async_set(entity_id, "unavailable", {"step": 1})
        expected.append((entity_id, "unavailable", 1, None, door))
        hass.states.async_set(
            entity_id, str(index), {"step": 2, "unit_of_measurement": "W"}
        )
        expected.append((entity_id, str(index), 2, "W", door))
        if index % 100 == 0:
            door = "on" if door == "off" else "off"
            hass.states.async_set(DOOR, door, {"device_class": "door"})
            expected.append((DOOR, door, None, None, door))
    peak = max(peak, len(asyncio.all_tasks()))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert peak - before < 50, peak - before
    runtime = entry.runtime_data.situation_runtime
    assert runtime._dropped == 0
    assert len(seen) == len(expected)
    mismatches = [
        (index, got, want)
        for index, (got, want) in enumerate(zip(seen, expected))
        if got != want
    ]
    assert mismatches == [], mismatches[:5]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
