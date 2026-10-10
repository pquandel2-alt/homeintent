"""7.9.7 EventRuntime priority, interest and retention regressions."""

from __future__ import annotations

import asyncio

from test_event_runtime_hardening_796 import LIGHT, _Harness, _settle

from homeintent import event_runtime
from homeintent.const import CONF_ROUTINE_DETECTION_ENABLED
from homeintent.effect_monitor import EffectMonitor
from homeintent.service_call import ServiceCallPlan


def test_expected_effect_displaces_full_category_queue_without_yield(monkeypatch):
    """A concrete consumer outranks a broad enabled category (review finding A)."""
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 64)
    switches = [f"switch.routine_{index}" for index in range(64)]
    effects = EffectMonitor()
    harness = _Harness(
        monkeypatch,
        [*switches, LIGHT],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        effect_monitor=effects,
    )
    for entity_id in switches:
        harness.house.seed(entity_id, "off")
    harness.house.seed(LIGHT, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in switches:
            harness.house.set(entity_id, "on")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        assert len(harness.pending_tasks()) == 1
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert effects.pending == ()
    assert harness.metrics.dropped_critical == 0
    assert harness.metrics.worker_starts == 1
