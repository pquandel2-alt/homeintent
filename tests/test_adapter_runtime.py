from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.adapters import FrigateMetadataAdapter, StructuredAdapterRuntime  # noqa: E402
from homeintent.const import CONF_HA_SOURCES_ENABLED  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


def test_opt_in_ha_sources_store_bounded_structured_state_only():
    hass = HomeAssistant()
    handlers = {}
    unsubscribed = []

    def listen(event_type, callback):
        handlers[event_type] = callback
        return lambda: unsubscribed.append(event_type)

    hass.bus = SimpleNamespace(async_listen=listen)
    evidence = []
    runtime = StructuredAdapterRuntime(
        hass,
        ConfigEntry(options={CONF_HA_SOURCES_ENABLED: True}),
        evidence.append,
    )

    async def scenario():
        stop = await runtime.async_start()
        # F2: HA runs listeners without the @callback marker in the executor.
        assert getattr(handlers["state_changed"], "_hass_callback", False) is True
        handlers["state_changed"](
            SimpleNamespace(
                data={
                    "entity_id": "weather.home",
                    "new_state": SimpleNamespace(
                        state="sunny",
                        attributes={"temperature": 18, "secret": "discarded"},
                    ),
                },
                time_fired=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )
        )
        stop()

    asyncio.run(scenario())
    assert len(evidence) == 1
    assert evidence[0].source == "weather"
    assert evidence[0].content == {"state": "sunny", "temperature": 18}
    assert unsubscribed == ["state_changed"]


def test_disabled_adapters_register_no_listeners():
    hass = HomeAssistant()
    calls = []
    hass.bus = SimpleNamespace(
        async_listen=lambda event_type, callback: calls.append(event_type)
    )
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), lambda value: None)
    stop = asyncio.run(runtime.async_start())
    stop()
    assert calls == []


def test_disabled_adapters_do_not_require_an_event_bus():
    hass = HomeAssistant()
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), lambda value: None)
    stop = asyncio.run(runtime.async_start())
    stop()


def test_adapter_rejects_irrelevant_states_and_normalizes_naive_time():
    hass = HomeAssistant()
    evidence = []
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), evidence.append)
    runtime._handle_state_changed(
        SimpleNamespace(
            data={
                "entity_id": "light.office",
                "new_state": SimpleNamespace(state="on", attributes={}),
            },
            time_fired=datetime(2026, 9, 2),
        )
    )
    runtime._handle_state_changed(
        SimpleNamespace(
            data={
                "entity_id": "timer.tea",
                "new_state": SimpleNamespace(
                    state="active", attributes={"remaining": "00:03:00"}
                ),
            },
            time_fired=datetime(2026, 9, 2),
        )
    )
    assert len(evidence) == 1
    assert evidence[0].observed_at.tzinfo is not None


def test_mqtt_payload_boundary_accepts_only_bounded_json_metadata():
    hass = HomeAssistant()
    evidence = []
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), evidence.append)
    runtime._handle_mqtt_message(SimpleNamespace(payload=b"not-json"))
    runtime._handle_mqtt_message(SimpleNamespace(payload="x" * 65_537))
    runtime._handle_mqtt_message(
        SimpleNamespace(
            payload='{"after":{"id":"p1","label":"person","camera":"door","score":2}}'
        )
    )
    assert len(evidence) == 1
    assert evidence[0].content["confidence"] == 1.0


def test_frigate_event_uses_source_timestamp_and_clamps_negative_score():
    evidence = FrigateMetadataAdapter().normalize(
        {
            "after": {
                "id": "cat-1",
                "label": "cat",
                "camera": "garden",
                "score": -1,
                "start_time": 1788307200,
            }
        }
    )
    assert evidence is not None
    assert evidence.observed_at == datetime.fromtimestamp(1788307200, timezone.utc)
    assert evidence.content["confidence"] == 0.0


def test_frigate_nested_event_remains_uncertain_metadata():
    hass = HomeAssistant()
    evidence = []
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), evidence.append)
    runtime._handle_frigate_event(
        SimpleNamespace(
            data={
                "after": {
                    "id": "event-1",
                    "label": "package",
                    "camera": "front_door",
                    "top_score": 0.72,
                    "start_time": 1788307200,
                }
            }
        )
    )
    assert len(evidence) == 1
    assert evidence[0].kind == "package"
    assert evidence[0].quality.value == "estimate"
    assert evidence[0].content["confidence"] == 0.72


def test_frigate_keeps_only_bounded_structured_zones_and_deduplicates():
    hass = HomeAssistant()
    evidence = []
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), evidence.append)
    payload = {
        "type": "update",
        "after": {
            "id": "person-1",
            "label": "person",
            "camera": "front",
            "score": 0.8,
            "entered_zones": ["door", "driveway"],
            "sub_label": "resident",
        },
    }

    runtime._store_frigate(payload)
    runtime._store_frigate(payload)

    assert len(evidence) == 1
    assert evidence[0].content["zones"] == ("door", "driveway")
    assert evidence[0].content["event_type"] == "update"
    assert "image" not in evidence[0].content


def test_energy_sensor_is_wrapped_as_direct_ha_source():
    hass = HomeAssistant()
    evidence = []
    runtime = StructuredAdapterRuntime(hass, ConfigEntry(), evidence.append)
    runtime._handle_state_changed(SimpleNamespace(
        data={
            "entity_id": "sensor.grid_energy",
            "new_state": SimpleNamespace(
                state="12.4",
                attributes={
                    "device_class": "energy",
                    "unit_of_measurement": "kWh",
                    "private": "discarded",
                },
            ),
        },
        time_fired=datetime(2026, 9, 2, tzinfo=timezone.utc),
    ))

    assert len(evidence) == 1
    assert evidence[0].source == "energy"
    assert evidence[0].content == {"state": "12.4"}
