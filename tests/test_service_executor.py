from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import async_execute_service_plan  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


def test_service_executor_never_allows_data_to_replace_validated_target():
    hass = HomeAssistant()
    entity = EntitySnapshot("switch.allowed", "Freigegeben", "switch", "on")
    plan = ServiceCallPlan(
        "homeassistant",
        "turn_off",
        entity.entity_id,
        {"entity_id": "switch.hidden"},
    )

    result = asyncio.run(
        async_execute_service_plan(
            hass,
            plan,
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )

    assert result.executed is False
    assert "validierte Ziel" in (result.error or "")
    hass.services.async_call.assert_not_awaited()


def test_service_executor_keeps_the_validated_target_authoritative():
    hass = HomeAssistant()
    entity = EntitySnapshot("switch.allowed", "Freigegeben", "switch", "on")
    plan = ServiceCallPlan("homeassistant", "turn_off", entity.entity_id)

    result = asyncio.run(
        async_execute_service_plan(
            hass,
            plan,
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )

    assert result.executed is True
    hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_off", {"entity_id": "switch.allowed"}, blocking=True
    )


def test_service_executor_rejects_unavailable_fresh_snapshot():
    hass = HomeAssistant()
    entity = EntitySnapshot("switch.allowed", "Freigegeben", "switch", "unavailable")
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("homeassistant", "turn_off", entity.entity_id),
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )
    assert not result.executed
    hass.services.async_call.assert_not_awaited()


def test_service_executor_rechecks_capability_when_snapshot_reports_it():
    hass = HomeAssistant()
    entity = EntitySnapshot(
        "switch.allowed", "Freigegeben", "switch", "on",
        capabilities=frozenset({"TURN_ON"}),
    )
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("homeassistant", "turn_off", entity.entity_id),
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )
    assert not result.executed
    assert "nicht mehr" in (result.error or "")
    hass.services.async_call.assert_not_awaited()


def test_brightness_rechecks_turn_on_and_brightness_capabilities():
    hass = HomeAssistant()
    entity = EntitySnapshot(
        "light.allowed", "Lampe", "light", "off",
        capabilities=frozenset({"TURN_ON"}),
    )
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan(
                "light", "turn_on", entity.entity_id, {"brightness_pct": 30}
            ),
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )
    assert not result.executed
    hass.services.async_call.assert_not_awaited()


def _execute(entity: EntitySnapshot, plan: ServiceCallPlan):
    hass = HomeAssistant()
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            plan,
            [entity],
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
        )
    )
    return hass, result


def test_positionable_cover_can_still_be_opened_and_closed():
    # derive_capabilities() models only POSITION for covers; open/close are
    # the baseline cover contract and must not be rejected as unsupported.
    for service, state in (("open_cover", "closed"), ("close_cover", "open")):
        entity = EntitySnapshot(
            "cover.rollladen", "Rollladen", "cover", state,
            capabilities=frozenset({"POSITION"}),
        )
        hass, result = _execute(
            entity, ServiceCallPlan("cover", service, entity.entity_id)
        )
        assert result.executed is True, service
        hass.services.async_call.assert_awaited_once_with(
            "cover", service, {"entity_id": "cover.rollladen"}, blocking=True
        )


def test_valve_without_reported_open_capability_is_still_rejected():
    entity = EntitySnapshot(
        "valve.garten", "Gartenventil", "valve", "closed",
        capabilities=frozenset({"CLOSE"}),
    )
    hass, result = _execute(
        entity, ServiceCallPlan("valve", "open_valve", entity.entity_id)
    )
    assert result.executed is False
    hass.services.async_call.assert_not_awaited()


def test_never_activated_scene_or_button_can_be_triggered():
    # Scenes and buttons report their last activation time; "unknown" is their
    # normal state until the first activation and says nothing about health.
    for entity, plan in (
        (
            EntitySnapshot("scene.abend", "Abend", "scene", "unknown"),
            ServiceCallPlan("scene", "turn_on", "scene.abend"),
        ),
        (
            EntitySnapshot("button.klingel", "Klingel", "button", "unknown"),
            ServiceCallPlan("button", "press", "button.klingel"),
        ),
    ):
        hass, result = _execute(entity, plan)
        assert result.executed is True, entity.entity_id


def test_notify_entity_before_its_first_message_can_be_used():
    # F8: a notify entity's state is the time of its last message and stays
    # "unknown" until the first one - it is not an unreliable device.
    for entity, plan in (
        (
            EntitySnapshot("notify.handy_anna", "Handy Anna", "notify", "unknown"),
            ServiceCallPlan("notify", "send_message", "notify.handy_anna", {"message": "Essen"}),
        ),
        (
            EntitySnapshot("script.gute_nacht", "Gute Nacht", "script", "unknown"),
            ServiceCallPlan("script", "turn_on", "script.gute_nacht"),
        ),
    ):
        hass, result = _execute(entity, plan)
        assert result.executed is True, entity.entity_id


def test_stateful_target_with_unknown_state_is_still_rejected():
    entity = EntitySnapshot("switch.pumpe", "Pumpe", "switch", "unknown")
    hass, result = _execute(
        entity, ServiceCallPlan("homeassistant", "turn_on", entity.entity_id)
    )
    assert result.executed is False
    hass.services.async_call.assert_not_awaited()


def test_unavailable_scene_is_still_rejected():
    entity = EntitySnapshot("scene.abend", "Abend", "scene", "unavailable")
    hass, result = _execute(
        entity, ServiceCallPlan("scene", "turn_on", entity.entity_id)
    )
    assert result.executed is False
