"""7.9.4 P0 against a REAL Home Assistant: a state-change burst stays cheap.

Live finding (7.9.3, fresh install): after the restart the UI stayed
unreachable; the log was full of pending ``state_changed`` tasks from
``event_runtime.py``. With HomeIntent loaded, a burst of state changes must
create no task per event, write no file per event and leave Home
Assistant's executor free.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeintent import event_runtime, thermal_tracker
from custom_components.homeintent.const import DOMAIN

SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    "agent_auto_enabled": False,
    "documents_enabled": False,
}
BURST = 3000


@pytest.mark.parametrize("selected", [False, True], ids=["unselected", "selected"])
async def test_state_change_burst_creates_no_task_or_write_per_event(
    hass: HomeAssistant, monkeypatch: Any, selected: bool
) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    options = dict(SETUP_OPTIONS)
    if selected:
        # Every storm sensor is selected and the deliberately broad routine
        # consumer is active, so all events reach the worker.
        options["selected_entities"] = [f"sensor.storm_{index}" for index in range(BURST)]
        options["agent_event_categories"] = "routine_anomaly"
        options["routine_detection_enabled"] = True
    entry = MockConfigEntry(domain=DOMAIN, title="HomeIntent", data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    writes: list[Path] = []
    original_write = thermal_tracker.ThermalExperienceTracker._write_document

    def _count_write(self: Any, document: dict[str, object]) -> None:
        writes.append(Path(str(self._state_path)))
        original_write(self, document)

    monkeypatch.setattr(
        thermal_tracker.ThermalExperienceTracker, "_write_document", _count_write
    )

    processed: list[str] = []
    process = getattr(event_runtime.SituationRuntime, "_async_process_state_changed", None)

    async def _count_process(self: Any, raw_event: Any, *args: Any) -> None:
        processed.append(raw_event.data["entity_id"])
        assert process is not None
        await process(self, raw_event, *args)

    # raising=False: the same file runs against 7.9.3, which lacks the hook.
    monkeypatch.setattr(
        event_runtime.SituationRuntime, "_async_process_state_changed", _count_process,
        raising=False,
    )

    before = len(asyncio.all_tasks())
    peak = before
    started = time.monotonic()
    for index in range(BURST):
        # Like a restart: sensors come back from "unavailable" one by one.
        hass.states.async_set(f"sensor.storm_{index}", "unavailable")
        hass.states.async_set(f"sensor.storm_{index}", str(20 + index % 7))
        if index % 250 == 0:
            peak = max(peak, len(asyncio.all_tasks()))
            # Integrations set up in turns; the loop runs between them.
            await asyncio.sleep(0)
    peak = max(peak, len(asyncio.all_tasks()))
    await hass.async_block_till_done(wait_background_tasks=True)
    elapsed = time.monotonic() - started

    # 7.9.3: one HomeIntent task per event (2 x BURST), each with an
    # fsync'd write in the executor and a snapshot of every selected entity.
    assert writes == [], len(writes)
    assert elapsed < 30, elapsed
    assert peak - before < 50, peak - before

    runtime = entry.runtime_data.situation_runtime
    assert getattr(runtime, "_dropped", None) == 0
    pending = runtime._pending
    assert not pending
    # Unselected sensors are filtered in the callback; selected ones are
    # all evaluated, in order, by the single worker.
    assert len(processed) == (2 * BURST if selected else 0)
    assert processed[:4] == (
        ["sensor.storm_0", "sensor.storm_0", "sensor.storm_1", "sensor.storm_1"]
        if selected else []
    )

    # The executor is still free for Home Assistant's own work.
    probe_started = time.monotonic()
    assert await hass.async_add_executor_job(lambda: 42) == 42
    assert time.monotonic() - probe_started < 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
