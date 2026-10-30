"""7.13.2: every non-independent moving cover is confirmation-gated."""

from __future__ import annotations

import asyncio

import pytest

import _ha_stub

_ha_stub.install()

from homeintent.cover_safety import HA_COVER_DEVICE_CLASSES  # noqa: E402
from homeintent.execution_policy import PolicyOutcome  # noqa: E402
from homeintent.risk import RiskLevel, classify_service_plan  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.service_executor import (  # noqa: E402
    CHANGED_SINCE_CONFIRMATION,
    async_execute_service_plan,
    confirmed_scope,
)
from test_batch_settle_7131 import Behaviour, _hass  # noqa: E402
from test_cover_batch_7131 import _cover  # noqa: E402


INDEPENDENT = {"shutter", "blind", "shade", "curtain"}
CRITICAL = {"garage", "gate"}
NON_INDEPENDENT = (
    HA_COVER_DEVICE_CLASSES - INDEPENDENT
) | {"garage_door", None, "unknown_class"}
SERVICES = (
    ("open_cover", {}),
    ("close_cover", {}),
    ("set_cover_position", {"position": 45}),
)


@pytest.mark.parametrize("device_class", sorted(INDEPENDENT))
@pytest.mark.parametrize("service,data", SERVICES)
def test_standard_shading_covers_remain_medium(device_class, service, data):
    cover = _cover("cover.standard", "Standard", device_class=device_class)
    assert classify_service_plan(
        ServiceCallPlan("cover", service, cover.entity_id, data), [cover]
    ) is RiskLevel.MEDIUM


@pytest.mark.parametrize("device_class", sorted(CRITICAL | {"garage_door"}))
@pytest.mark.parametrize("service,data", SERVICES)
def test_garages_and_gates_remain_critical(device_class, service, data):
    cover = _cover("cover.access", "Zugang", device_class=device_class)
    assert classify_service_plan(
        ServiceCallPlan("cover", service, cover.entity_id, data), [cover]
    ) is RiskLevel.CRITICAL


@pytest.mark.parametrize("device_class", sorted(NON_INDEPENDENT, key=str))
@pytest.mark.parametrize("service,data", SERVICES)
def test_non_independent_moving_covers_never_write_without_confirmation(
    device_class, service, data
):
    covers = [
        _cover("cover.front", "Front", device_class=device_class),
        _cover("cover.back", "Back", device_class=device_class),
    ]
    plan = ServiceCallPlan("cover", service, [item.entity_id for item in covers], data)
    hass, _devices = _hass(covers, {})
    result = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {}, is_admin=True, user_id="admin", confirmed=False,
    ))
    assert classify_service_plan(plan, covers) >= RiskLevel.HIGH
    assert result.decision.outcome is PolicyOutcome.CONFIRM
    assert not result.executed and result.batch_result is None
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("device_class", sorted(NON_INDEPENDENT, key=str))
@pytest.mark.parametrize("service,data", SERVICES)
def test_confirmation_executes_only_the_exact_atomic_cover_plan(device_class, service, data):
    covers = [
        _cover("cover.front", "Front", device_class=device_class),
        _cover("cover.back", "Back", device_class=device_class),
    ]
    plan = ServiceCallPlan("cover", service, [item.entity_id for item in covers], data)
    hass, _devices = _hass(covers, {})
    hass.services.async_call.side_effect = None
    scope = confirmed_scope(hass, (plan,), covers, {})
    result = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {}, is_admin=True, user_id="admin", confirmed=True,
        scope=scope,
    ))
    assert result.executed and result.batch_result is None
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.args[:3] == (
        "cover", service, {"entity_id": ["cover.front", "cover.back"], **data},
    )


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("device_class", sorted(INDEPENDENT))
@pytest.mark.parametrize("service,data", SERVICES)
def test_independent_shading_matrix_keeps_per_target_execution(
    confirmed, device_class, service, data
):
    covers = [
        _cover("cover.front", "Front", device_class=device_class, state="open"),
        _cover("cover.back", "Back", device_class=device_class, state="open"),
    ]
    end_state = "open" if service != "close_cover" else "closed"
    attributes = {"current_position": data.get("position", 100 if end_state == "open" else 0)}
    hass, devices = _hass(covers, {
        item.entity_id: Behaviour(end_state, attributes=attributes) for item in covers
    })
    plan = ServiceCallPlan("cover", service, [item.entity_id for item in covers], data)
    scope = confirmed_scope(hass, (plan,), covers, {}) if confirmed else None
    result = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {"effect_wait_seconds": 0.1},
        is_admin=True, user_id="admin", confirmed=confirmed, scope=scope,
    ))
    assert result.executed and result.batch_result is not None
    assert result.batch_result.fully_successful
    assert devices.calls == [
        ("cover", service, "cover.front"),
        ("cover", service, "cover.back"),
    ]


@pytest.mark.parametrize("device_class", sorted(NON_INDEPENDENT, key=str))
def test_confirmation_rejects_changed_service_value_and_target_set(device_class):
    covers = [
        _cover("cover.front", "Front", device_class=device_class),
        _cover("cover.back", "Back", device_class=device_class),
        _cover("cover.extra", "Extra", device_class=device_class),
    ]
    shown = ServiceCallPlan(
        "cover", "set_cover_position", ["cover.front", "cover.back"], {"position": 40}
    )
    scope_hass, _devices = _hass(covers, {})
    scope = confirmed_scope(scope_hass, (shown,), covers, {})
    for changed in (
        ServiceCallPlan("cover", "set_cover_position", ["cover.front", "cover.back"], {"position": 60}),
        ServiceCallPlan("cover", "open_cover", ["cover.front", "cover.back"]),
        ServiceCallPlan("cover", "set_cover_position", ["cover.front", "cover.extra"], {"position": 40}),
    ):
        hass, _devices = _hass(covers, {})
        result = asyncio.run(async_execute_service_plan(
            hass, changed, covers, {}, is_admin=True, user_id="admin", confirmed=True,
            scope=scope,
        ))
        assert not result.executed and result.error == CHANGED_SINCE_CONFIRMATION
        hass.services.async_call.assert_not_awaited()


def test_mixed_standard_and_door_cover_is_atomic_and_confirmed():
    covers = [
        _cover("cover.shutter", "Rollladen", device_class="shutter"),
        _cover("cover.door", "Tür", device_class="door"),
    ]
    plan = ServiceCallPlan("cover", "close_cover", [item.entity_id for item in covers])
    hass, _devices = _hass(covers, {})
    result = asyncio.run(async_execute_service_plan(
        hass, plan, covers, {}, is_admin=True, user_id="admin", confirmed=False,
    ))
    assert result.decision.risk is RiskLevel.HIGH
    assert result.batch_result is None and not result.executed
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
@pytest.mark.parametrize("device_class", sorted(NON_INDEPENDENT, key=str))
def test_confirmed_atomic_cover_set_fails_closed_when_one_target_is_unavailable(
    state, device_class
):
    shown = [
        _cover("cover.front", "Front", device_class=device_class),
        _cover("cover.back", "Back", device_class=device_class),
    ]
    plan = ServiceCallPlan("cover", "open_cover", [item.entity_id for item in shown])
    scope_hass, _devices = _hass(shown, {})
    scope = confirmed_scope(scope_hass, (plan,), shown, {})
    fresh = [
        shown[0],
        _cover("cover.back", "Back", device_class=device_class, state=state),
    ]
    hass, _devices = _hass(fresh, {})
    result = asyncio.run(async_execute_service_plan(
        hass, plan, fresh, {}, is_admin=True, user_id="admin", confirmed=True,
        scope=scope,
    ))
    assert not result.executed
    hass.services.async_call.assert_not_awaited()


def test_confirmed_atomic_cover_set_fails_closed_when_target_is_no_longer_exposed():
    shown = [
        _cover("cover.front", "Front", device_class="door"),
        _cover("cover.back", "Back", device_class="door"),
    ]
    plan = ServiceCallPlan("cover", "open_cover", [item.entity_id for item in shown])
    scope_hass, _devices = _hass(shown, {})
    scope = confirmed_scope(scope_hass, (plan,), shown, {})
    hass, _devices = _hass(shown[:1], {})
    result = asyncio.run(async_execute_service_plan(
        hass, plan, shown[:1], {}, is_admin=True, user_id="admin", confirmed=True,
        scope=scope,
    ))
    assert not result.executed
    hass.services.async_call.assert_not_awaited()
