"""Real Home Assistant regressions for the 7.1.2 live-test findings.

Each of these failures was invisible to the stub suite (``tests/``) because
the stub does not reproduce the real thread model, response formats or
service schemas:

- F2: V12 timer callbacks must run in the event loop (``@callback``).
- F3: ``recorder.get_statistics`` answers ``{"statistics": {id: rows}}``
  (``test_live_recorder.py``: the recorder must start before ``hass``).
- F4: ``light.turn_on`` rejects ``kelvin`` since Home Assistant 2026.x.
- F8: a notify entity stays ``unknown`` until its first message.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.homeintent.const import DOMAIN
from custom_components.homeintent.engine import NluEngine
from custom_components.homeintent.entities import EntitySnapshot
from custom_components.homeintent.service_call import ServiceCallPlan
from custom_components.homeintent.service_executor import async_execute_service_plan

SETUP_OPTIONS: dict[str, Any] = {
    "memory_enabled": False,
    "agent_auto_enabled": False,
    "documents_enabled": False,
}


async def _setup_homeintent(hass: HomeAssistant) -> MockConfigEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, title="HomeIntent", data={}, options=SETUP_OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_f2_v12_timer_fires_in_the_event_loop(hass: HomeAssistant) -> None:
    entry = await _setup_homeintent(hass)
    runtime = entry.runtime_data.proactive_context
    fired = asyncio.Event()

    async def _check() -> None:
        fired.set()

    runtime.schedule("regression", dt_util.utcnow() + timedelta(seconds=5), _check)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()

    assert fired.is_set()
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "sentence",
    [
        "Stelle das Wohnzimmer Deckenlicht auf warmweiß",
        "Stelle das Wohnzimmer Deckenlicht auf kaltweiß",
    ],
)
async def test_f4_color_temperature_data_passes_the_real_light_schema(
    hass: HomeAssistant, sentence: str
) -> None:
    assert await async_setup_component(hass, "light", {})
    await hass.async_block_till_done()
    light = EntitySnapshot(
        "light.wohnzimmer_deckenlicht", "Wohnzimmer Deckenlicht", "light", "off",
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS", "COLOR_TEMPERATURE"}),
    )
    result = NluEngine().match(sentence, [light])
    assert result is not None and result.plan is not None
    assert "color_temp_kelvin" in result.plan.data

    # Schema validation happens before entity lookup: an invalid key raises.
    await hass.services.async_call(
        "light", "turn_on",
        {**result.plan.data, "entity_id": light.entity_id},
        blocking=True,
    )


async def test_f8_notify_entity_in_initial_unknown_state_is_usable(hass: HomeAssistant) -> None:
    calls: list[ServiceCall] = []

    async def _send(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("notify", "send_message", _send)
    hass.states.async_set("notify.handy_anna", "unknown", {"friendly_name": "Handy Anna"})
    state = hass.states.get("notify.handy_anna")
    assert state is not None and state.state == "unknown"
    target = EntitySnapshot("notify.handy_anna", "Handy Anna", "notify", state.state)

    result = await async_execute_service_plan(
        hass,
        ServiceCallPlan("notify", "send_message", target.entity_id, {"message": "Essen ist fertig"}),
        [target],
        {},
        is_admin=True,
        user_id=None,
        confirmed=True,
    )

    assert result.executed, result.reason
    assert len(calls) == 1
    assert calls[0].data["message"] == "Essen ist fertig"
