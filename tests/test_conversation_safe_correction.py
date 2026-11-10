from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402


LIGHTS = [
    EntitySnapshot(
        "light.living",
        "Wohnzimmerlicht",
        "light",
        "off",
        area_id="living",
        area_name="Wohnzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(
        "light.floor",
        "Stehlampe",
        "light",
        "off",
        area_id="living",
        area_name="Wohnzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
]


def _install_state_reports(entity, *, report_compensation: bool) -> None:
    """Model actual HA state evidence instead of a recorder-only service."""

    for item in LIGHTS:
        entity.hass.states._states[item.entity_id] = State(
            item.entity_id, item.state, dict(item.attributes)
        )

    async def report(_domain, service, data=None, **_kwargs):
        payload = data or {}
        entity_id = payload.get("entity_id")
        if not isinstance(entity_id, str):
            return
        if service == "turn_on":
            entity.hass.states._states[entity_id] = State(entity_id, "on")
        elif service == "turn_off" and report_compensation:
            entity.hass.states._states[entity_id] = State(entity_id, "off")

    entity.hass.services.async_call.side_effect = report


def test_correction_compensates_previous_action_before_retargeting(monkeypatch):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    _install_state_reports(entity, report_compensation=True)
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="safe-correction",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    asyncio.run(turn("Mach das Wohnzimmerlicht an."))
    corrected = asyncio.run(turn("Nein, ich meinte nur die Stehlampe."))

    calls = entity.hass.services.async_call.await_args_list
    assert len(calls) == 3
    assert calls[0].args[:2] == ("homeassistant", "turn_on")
    assert calls[0].args[2]["entity_id"] == "light.living"
    assert calls[1].args[:2] == ("light", "turn_off")
    assert calls[1].args[2]["entity_id"] == "light.living"
    assert calls[2].args[:2] == ("homeassistant", "turn_on")
    assert calls[2].args[2]["entity_id"] == "light.floor"
    assert "Stehlampe" in corrected.response.speech


def test_correction_stops_when_compensation_effect_is_not_observed(monkeypatch):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    _install_state_reports(entity, report_compensation=False)
    still_on = [
        EntitySnapshot(
            item.entity_id,
            item.friendly_name,
            item.domain,
            "on" if item.entity_id == "light.living" else item.state,
            area_id=item.area_id,
            area_name=item.area_name,
            capabilities=item.capabilities,
        )
        for item in LIGHTS
    ]
    snapshots = iter((LIGHTS, still_on, still_on, still_on))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: next(snapshots)
    )

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="failed-correction",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    asyncio.run(turn("Mach das Wohnzimmerlicht an."))
    stopped = asyncio.run(turn("Nein, ich meinte nur die Stehlampe."))
    assert len(entity.hass.services.async_call.await_args_list) == 2
    assert "sicher gestoppt" in stopped.response.speech
