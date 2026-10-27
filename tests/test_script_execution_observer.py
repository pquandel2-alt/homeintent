"""Truthful, bounded script outcome observation through public HA contracts."""

from __future__ import annotations

import asyncio

import _ha_stub

_ha_stub.install()

from homeassistant.core import Context, HomeAssistant, State

from homeintent.script_execution_observer import (
    ScriptExecutionStatus,
    async_run_and_observe_script,
    script_execution_speech,
)


def _hass(*, direct: bool = True) -> HomeAssistant:
    hass = HomeAssistant()
    if direct:
        hass.services.async_register("script", "test", lambda _call: None)
    return hass


def _service_runner(hass: HomeAssistant):
    async def run(domain, service, data, context):
        return await hass.services.async_call(
            domain, service, dict(data), blocking=True, context=context
        )

    return run


def _run(hass: HomeAssistant, wait: float = 0.05):
    return asyncio.run(
        async_run_and_observe_script(
            hass,
            "script.test",
            {},
            context=Context(user_id="admin"),
            wait_seconds=wait,
            service_runner=_service_runner(hass),
        )
    )


def test_immediately_successful_direct_script_is_completed():
    result = _run(_hass())

    assert result.status is ScriptExecutionStatus.COMPLETED
    assert script_execution_speech("Test", result) == "Test wurde ausgeführt."


def test_immediately_failing_script_is_failed_not_completed():
    hass = _hass()
    hass.services.async_call.side_effect = RuntimeError("step failed")

    result = _run(hass)

    assert result.status is ScriptExecutionStatus.FAILED
    assert not result.accepted
    assert script_execution_speech("Test", result) == "Test ist fehlgeschlagen."


def test_long_running_script_is_reported_running_and_finishes_without_orphan():
    async def scenario():
        hass = _hass()
        release = asyncio.Event()
        hass.states._states["script.test"] = State(
            "script.test", "on", {"mode": "parallel", "current": 1}
        )

        async def call(*_args, **_kwargs):
            await release.wait()

        hass.services.async_call.side_effect = call
        result = await async_run_and_observe_script(
            hass,
            "script.test",
            {},
            context=Context(),
            wait_seconds=0.001,
            service_runner=_service_runner(hass),
        )
        assert result.status is ScriptExecutionStatus.RUNNING
        release.set()
        await asyncio.gather(*hass._tasks)
        return result

    result = asyncio.run(scenario())
    assert script_execution_speech("Test", result) == (
        "Test wurde gestartet und läuft noch."
    )


def test_timeout_without_reliable_state_is_explicitly_unverified():
    async def scenario():
        hass = _hass()
        release = asyncio.Event()

        async def call(*_args, **_kwargs):
            await release.wait()

        hass.services.async_call.side_effect = call
        result = await async_run_and_observe_script(
            hass,
            "script.test",
            {},
            context=Context(),
            wait_seconds=0.001,
            service_runner=_service_runner(hass),
        )
        release.set()
        await asyncio.gather(*hass._tasks)
        return result

    result = asyncio.run(scenario())
    assert result.status is ScriptExecutionStatus.TIMED_OUT
    assert "nicht verifizieren" in script_execution_speech("Test", result)


def test_background_turn_on_acceptance_never_claims_completion():
    result = _run(_hass(direct=False))

    assert result.status is ScriptExecutionStatus.ACCEPTED
    assert "nicht verifizieren" in script_execution_speech("Test", result)


def test_single_mode_script_already_active_is_rejected_without_service_call():
    hass = _hass()
    hass.states._states["script.test"] = State(
        "script.test", "on", {"mode": "single", "current": 1}
    )

    result = _run(hass)

    assert result.status is ScriptExecutionStatus.REJECTED
    hass.services.async_call.assert_not_awaited()


def test_parallel_script_runs_are_observed_independently():
    async def scenario():
        hass = _hass()
        hass.states._states["script.test"] = State(
            "script.test", "off", {"mode": "parallel", "current": 0}
        )
        results = await asyncio.gather(
            *(
                async_run_and_observe_script(
                    hass,
                    "script.test",
                    {"run": index},
                    context=Context(),
                    wait_seconds=0.05,
                    service_runner=_service_runner(hass),
                )
                for index in range(4)
            )
        )
        return results, hass.services.async_call.await_count

    results, call_count = asyncio.run(scenario())
    assert {result.status for result in results} == {
        ScriptExecutionStatus.COMPLETED
    }
    assert call_count == 4


def test_failure_after_a_completed_inner_step_is_still_failed():
    hass = _hass()
    completed_steps: list[str] = []

    async def call(*_args, **_kwargs):
        completed_steps.append("light.turn_on")
        raise RuntimeError("later step failed")

    hass.services.async_call.side_effect = call
    result = _run(hass)

    assert completed_steps == ["light.turn_on"]
    assert result.status is ScriptExecutionStatus.FAILED
