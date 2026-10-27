"""Independent low-risk batches preflight and report every target."""

from __future__ import annotations

import asyncio

import _ha_stub

_ha_stub.install()

from homeassistant.core import HomeAssistant, State

from homeintent.batch_execution import BatchTargetStatus, batch_execution_speech
from homeintent.entities import EntitySnapshot
from homeintent.risk import RiskLevel
from homeintent.service_call import ServiceCallPlan
from homeintent.service_executor import (
    ConfirmedScope,
    async_execute_service_plan,
    confirmed_scope,
)


def _light(entity_id: str, name: str, capabilities=("TURN_OFF",)) -> EntitySnapshot:
    return EntitySnapshot(
        entity_id,
        name,
        "light",
        "on",
        capabilities=frozenset(capabilities),
    )


def test_unsupported_target_does_not_abort_independent_valid_lights():
    hass = HomeAssistant()
    entities = [
        _light("light.one", "Tischlicht"),
        _light("light.oven", "Backofenleuchte", ("TURN_ON",)),
        _light("light.three", "Stehlampe"),
    ]
    for entity in entities:
        hass.states._states[entity.entity_id] = State(entity.entity_id, "on")

    async def call(_domain, _service, data, **_kwargs):
        entity_id = data["entity_id"]
        hass.states._states[entity_id] = State(entity_id, "off")

    hass.services.async_call.side_effect = call
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan(
                "light", "turn_off", [entity.entity_id for entity in entities]
            ),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.executed
    assert result.batch_result is not None
    assert [item.status for item in result.batch_result.targets] == [
        BatchTargetStatus.SUCCESS,
        BatchTargetStatus.UNSUPPORTED,
        BatchTargetStatus.SUCCESS,
    ]
    assert hass.services.async_call.await_count == 2
    assert batch_execution_speech(result.batch_result) == (
        "Zwei Lichter wurden ausgeschaltet. "
        "Backofenleuchte unterstützt diese Aktion nicht."
    )


def test_homeassistant_turn_helper_uses_resolved_light_domains_for_batching():
    hass = HomeAssistant()
    entities = [
        _light("light.one", "Tischlicht"),
        _light("light.oven", "Backofenleuchte", ("TURN_ON",)),
    ]
    hass.states._states["light.one"] = State("light.one", "on")

    async def call(_domain, _service, data, **_kwargs):
        hass.states._states[data["entity_id"]] = State(data["entity_id"], "off")

    hass.services.async_call.side_effect = call
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan(
                "homeassistant", "turn_off", [entity.entity_id for entity in entities]
            ),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.batch_result is not None
    assert [item.status for item in result.batch_result.targets] == [
        BatchTargetStatus.SUCCESS,
        BatchTargetStatus.UNSUPPORTED,
    ]
    assert hass.services.async_call.await_count == 1
    assert batch_execution_speech(result.batch_result).startswith(
        "Ein Licht wurde ausgeschaltet."
    )


def test_one_runtime_failure_does_not_stop_later_prevalidated_target():
    hass = HomeAssistant()
    entities = [_light("light.one", "Erstes Licht"), _light("light.two", "Zweites Licht")]
    for entity in entities:
        hass.states._states[entity.entity_id] = State(entity.entity_id, "on")

    async def call(_domain, _service, data, **_kwargs):
        if data["entity_id"] == "light.one":
            raise RuntimeError("offline")
        hass.states._states[data["entity_id"]] = State(data["entity_id"], "off")

    hass.services.async_call.side_effect = call
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", ["light.one", "light.two"]),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.batch_result is not None
    assert [item.status for item in result.batch_result.targets] == [
        BatchTargetStatus.FAILED,
        BatchTargetStatus.SUCCESS,
    ]
    assert hass.services.async_call.await_count == 2


def test_accepted_call_without_observed_effect_is_not_reported_as_success():
    hass = HomeAssistant()
    entities = [_light("light.one", "Erstes Licht"), _light("light.two", "Zweites Licht")]
    for entity in entities:
        hass.states._states[entity.entity_id] = State(entity.entity_id, "on")

    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", ["light.one", "light.two"]),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.executed
    assert result.batch_result is not None
    assert all(
        item.status is BatchTargetStatus.UNVERIFIED
        for item in result.batch_result.targets
    )
    assert result.batch_result.succeeded_entity_ids == frozenset()
    assert batch_execution_speech(result.batch_result) == (
        "Erstes Licht und Zweites Licht konnten nicht verifiziert werden."
    )


def test_confirmation_never_applies_to_a_changed_batch_target_set():
    hass = HomeAssistant()
    entities = [_light("light.one", "Erstes Licht"), _light("light.two", "Zweites Licht")]
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", ["light.one", "light.two"]),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
            scope=ConfirmedScope(RiskLevel.LOW, frozenset({"light.one"})),
        )
    )

    assert not result.executed
    assert result.batch_result is not None
    assert all(
        item.status is BatchTargetStatus.NOT_CONFIRMED
        for item in result.batch_result.targets
    )
    hass.services.async_call.assert_not_awaited()


def test_generated_confirmation_rejects_a_reduced_batch_target_set():
    hass = HomeAssistant()
    entities = [_light("light.one", "Erstes Licht"), _light("light.two", "Zweites Licht")]
    shown = ServiceCallPlan("light", "turn_off", ["light.one", "light.two"])
    scope = confirmed_scope(hass, (shown,), entities, {})

    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", "light.one"),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=True,
            scope=scope,
        )
    )

    assert not result.executed
    hass.services.async_call.assert_not_awaited()


def test_unavailable_target_is_reported_without_becoming_a_service_target():
    hass = HomeAssistant()
    entities = [
        _light("light.one", "Erstes Licht"),
        EntitySnapshot(
            "light.two",
            "Zweites Licht",
            "light",
            "unavailable",
            capabilities=frozenset({"TURN_OFF"}),
        ),
    ]
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", ["light.one", "light.two"]),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.batch_result is not None
    assert result.batch_result.targets[1].status is BatchTargetStatus.UNAVAILABLE
    assert hass.services.async_call.await_count == 1


def test_non_exposed_target_is_unauthorized_and_never_called():
    hass = HomeAssistant()
    entities = [_light("light.one", "Freigegebenes Licht")]
    hass.states._states["light.one"] = State("light.one", "on")

    async def call(_domain, _service, data, **_kwargs):
        hass.states._states[data["entity_id"]] = State(data["entity_id"], "off")

    hass.services.async_call.side_effect = call
    result = asyncio.run(
        async_execute_service_plan(
            hass,
            ServiceCallPlan("light", "turn_off", ["light.one", "light.hidden"]),
            entities,
            {},
            is_admin=False,
            user_id=None,
            confirmed=False,
        )
    )

    assert result.batch_result is not None
    assert [item.status for item in result.batch_result.targets] == [
        BatchTargetStatus.SUCCESS,
        BatchTargetStatus.UNAUTHORIZED,
    ]
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.args[2]["entity_id"] == "light.one"
