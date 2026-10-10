"""7.9.7 EventRuntime priority, interest and retention regressions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_event_runtime_hardening_796 import (
    DOOR,
    CLIMATE,
    LIGHT,
    PERSON,
    SMOKE,
    T0,
    TEMP,
    WINDOW,
    _Harness,
    _legacy_house_consumer,
    _settle,
)

from homeintent import event_runtime
from homeintent.const import CONF_ROUTINE_DETECTION_ENABLED
from homeintent.effect_monitor import EffectMonitor
from homeintent.entities import EntitySnapshot
from homeintent.event_priority import EventPriority
from homeintent.learning_policy import LearningMode
from homeintent.service_call import ServiceCallPlan
from homeintent.thermal_tracker import ThermalExperienceTracker
from homeassistant.core import State


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


@pytest.mark.parametrize(
    ("category", "entity_id", "old", "new", "device_class"),
    [
        ("safety", SMOKE, "off", "on", "smoke"),
        ("device_unavailable", "switch.router", "on", "unavailable", None),
        ("opening_while_away", DOOR, "off", "on", "door"),
        ("window_heating", DOOR, "off", "on", "door"),
        ("window_heating", "climate.room", "heat", "off", None),
        ("light_unoccupied", LIGHT, "off", "on", None),
        ("light_unoccupied", PERSON, "home", "not_home", None),
        ("long_running_state", "switch.pump", "off", "on", None),
    ],
)
def test_category_interest_matrix_accepts_only_its_inputs(
    monkeypatch, category, entity_id, old, new, device_class
):
    unrelated = "sensor.unrelated"
    harness = _Harness(monkeypatch, [entity_id, unrelated], categories=category)
    harness.runtime.async_start()
    attrs = {"device_class": device_class} if device_class else {}
    priority = harness.runtime.interest.classify(
        entity_id, State(entity_id, old, attrs), State(entity_id, new, attrs)
    )
    expected = EventPriority.CRITICAL if category == "safety" else EventPriority.CATEGORY
    assert priority is expected
    assert harness.runtime.interest.classify(
        unrelated, State(unrelated, "off"), State(unrelated, "on")
    ) is None


def test_routine_and_expected_effect_rows_are_explicit(monkeypatch):
    effects = EffectMonitor()
    harness = _Harness(
        monkeypatch,
        ["switch.routine", LIGHT],
        categories="routine_anomaly,expected_effect_missing",
        options={CONF_ROUTINE_DETECTION_ENABLED: False},
        effect_monitor=effects,
    )

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        switch = (State("switch.routine", "off"), State("switch.routine", "on"))
        light = (State(LIGHT, "off"), State(LIGHT, "on"))
        # Neither a disabled routine detector nor the timeout-output category
        # subscribes house-wide.
        assert harness.runtime.interest.classify("switch.routine", *switch) is None
        assert harness.runtime.interest.classify(LIGHT, *light) is None
        harness.entry.options[CONF_ROUTINE_DETECTION_ENABLED] = True
        assert (
            harness.runtime.interest.classify("switch.routine", *switch)
            is EventPriority.CATEGORY
        )
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        assert harness.runtime.interest.classify(LIGHT, *light) is EventPriority.PROTECTED
        effects.observe(LIGHT, "on")
        assert harness.runtime.interest.classify(LIGHT, *light) is EventPriority.CATEGORY
        stop()
        await effects.async_close()

    asyncio.run(scenario())


def test_category_combinations_remain_union_not_house_wide(monkeypatch):
    harness = _Harness(
        monkeypatch,
        [DOOR, LIGHT, "switch.router", "sensor.unrelated", "binary_sensor.unrelated"],
        categories="opening_while_away,light_unoccupied,device_unavailable",
    )
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    assert classify(
        DOOR,
        State(DOOR, "off", {"device_class": "door"}),
        State(DOOR, "on", {"device_class": "door"}),
    ) is EventPriority.CATEGORY
    assert classify(LIGHT, State(LIGHT, "off"), State(LIGHT, "on")) is EventPriority.CATEGORY
    assert classify(
        "switch.router", State("switch.router", "on"),
        State("switch.router", "unavailable"),
    ) is EventPriority.CATEGORY
    for entity_id in ("sensor.unrelated", "binary_sensor.unrelated"):
        assert classify(entity_id, State(entity_id, "off"), State(entity_id, "on")) is None


def test_negative_proactive_filter_queues_nothing_for_6000_events(monkeypatch):
    entity_ids = [f"sensor.irrelevant_{index}" for index in range(6000)]
    proactive = SimpleNamespace(
        enabled=True,
        is_relevant_event=lambda *_args: False,
        async_observe_state=AsyncMock(),
    )
    harness = _Harness(monkeypatch, entity_ids, proactive_context=proactive)
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in entity_ids:
            harness.house.set(entity_id, "1")
        assert harness.pending_tasks() == []
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert metrics.received == metrics.filtered_by_interest == 6000
    assert metrics.queued == metrics.retained_live == metrics.retained_total == 0
    assert metrics.worker_starts == 0


def test_queued_protected_event_survives_later_category_and_critical(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
    switches = [f"switch.category_{index}" for index in range(6)]
    effects = EffectMonitor()
    harness = _Harness(
        monkeypatch,
        [*switches, LIGHT, SMOKE],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        effect_monitor=effects,
    )
    for entity_id in switches:
        harness.house.seed(entity_id, "off")
    harness.house.seed(LIGHT, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in switches[:4]:
            harness.house.set(entity_id, "on")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        harness.house.set(switches[4], "on")
        harness.house.set(SMOKE, "on")
        harness.house.set(switches[5], "on")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert effects.pending == ()
    assert harness.metrics.dropped_protected == 0
    assert harness.metrics.dropped_category >= 4


def test_active_thermal_watcher_displaces_full_category_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 32)
    manager = SimpleNamespace(
        policy=SimpleNamespace(learning_mode=LearningMode.SILENT_LEARN),
        predictive_house=SimpleNamespace(thermal_model=lambda _area: None),
        async_record_thermal_observation=AsyncMock(),
        async_invalidate_thermal_model=AsyncMock(),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    switches = [f"switch.thermal_load_{index}" for index in range(32)]
    harness = _Harness(
        monkeypatch,
        [*switches, CLIMATE, TEMP, WINDOW],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        thermal_tracker=tracker,
    )
    for entity_id in switches:
        harness.house.seed(entity_id, "off")
    harness.house.seed(CLIMATE, "heat", temperature=22.0)
    harness.house.seed(TEMP, "19.0", unit_of_measurement="°C")
    harness.house.seed(WINDOW, "off")
    entities = (
        EntitySnapshot(
            CLIMATE, "Heizung", "climate", "heat", area_id="wohnzimmer",
            attributes={"temperature": 22.0},
        ),
        EntitySnapshot(
            TEMP, "Temperatur", "sensor", "19.0", area_id="wohnzimmer",
            device_class="temperature", unit="°C",
        ),
        EntitySnapshot(
            WINDOW, "Fenster", "binary_sensor", "off", area_id="wohnzimmer",
            device_class="window",
        ),
    )
    observed_temperatures: list[str] = []
    observe = tracker.async_observe_states

    async def record(states, **kwargs):
        observed_temperatures.extend(
            item.state for item in states if item.entity_id == TEMP
        )
        await observe(states, **kwargs)

    tracker.async_observe_states = record  # type: ignore[method-assign]

    async def scenario() -> None:
        tracker.observe_action(
            ServiceCallPlan("climate", "set_temperature", CLIMATE, {"temperature": 22.0}),
            entities,
            occurred_at=T0,
        )
        stop = harness.runtime.async_start()
        for entity_id in switches:
            harness.house.set(entity_id, "on")
        assert harness.runtime.interest.classify(
            TEMP, State(TEMP, "19.0"), State(TEMP, "20.0")
        ) is EventPriority.PROTECTED
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert "20.0" in observed_temperatures
    assert harness.metrics.dropped_protected == 0
    assert harness.metrics.dropped_category == 1


def test_snapshot_retry_with_full_queue_keeps_protected_effect(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 16)
    switches = [f"switch.retry_{index}" for index in range(16)]
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
    build = harness.build
    attempts = 0

    def flaky(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("registry unavailable")
        return build(*args)

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", flaky)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in switches:
            harness.house.set(entity_id, "on")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert attempts >= 2
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert effects.pending == ()
    assert harness.metrics.snapshot_failures == 1
    assert harness.metrics.dropped_protected == 0


def _assert_empty_generation(harness: _Harness) -> None:
    gen = harness.runtime._gen
    assert gen.live == gen.tombstones == 0
    assert not gen.queue
    assert not gen.latest
    assert not gen.gaps
    assert not gen.carry_previous
    assert not gen.unprocessed_by_entity
    assert not gen.coalescible
    assert not gen.view_only
    assert not gen.category
    assert not gen.protected
    assert harness.metrics.retained_total == 0


def test_complete_drain_releases_6000_unique_event_and_state_objects(monkeypatch):
    entity_ids = [f"sensor.retention_{index}" for index in range(6000)]
    harness = _Harness(
        monkeypatch, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in entity_ids:
            harness.house.set(entity_id, "1")
        assert harness.runtime._gen.live == event_runtime.MAX_PENDING_EVENTS
        await _settle(harness.hass)
        _assert_empty_generation(harness)
        stop()

    asyncio.run(scenario())
    assert harness.metrics.max_retained_total <= event_runtime.MAX_TRANSIENT_RECORDS


def test_history_bookkeeping_degrades_safely_and_is_bounded(monkeypatch, caplog):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 16)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 8)
    entity_ids = [f"sensor.overload_{index}" for index in range(200)]
    harness = _Harness(
        monkeypatch, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in entity_ids:
            harness.house.set(entity_id, "1")
        assert len(harness.runtime._gen.gaps) <= 8
        assert len(harness.runtime._gen.carry_previous) <= 8
        await _settle(harness.hass)
        _assert_empty_generation(harness)
        stop()

    asyncio.run(scenario())
    assert harness.metrics.history_degraded == 1
    assert harness.metrics.history_degraded_events > 0
    assert harness.metrics.history_degraded_batches > 0


def test_ten_bursts_return_to_the_same_empty_baseline(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 32)
    sensors = [f"sensor.burst_{index}" for index in range(40)]
    switches = [f"switch.burst_{index}" for index in range(40)]
    effects = EffectMonitor()
    harness = _Harness(
        monkeypatch,
        [*sensors, *switches, LIGHT, SMOKE],
        proactive_context=_legacy_house_consumer(),
        effect_monitor=effects,
    )
    for entity_id in (*sensors, *switches):
        harness.house.seed(entity_id, "0" if entity_id.startswith("sensor.") else "off")
    harness.house.seed(LIGHT, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        light_on = False
        smoke_on = False
        for burst in range(10):
            for entity_id in sensors:
                harness.house.set(entity_id, str(burst + 1))
            for entity_id in switches:
                harness.house.set(entity_id, "on" if burst % 2 == 0 else "off")
            light_on = not light_on
            effects.register(ServiceCallPlan(
                "light", "turn_on" if light_on else "turn_off", LIGHT, {}
            ))
            harness.house.set(LIGHT, "on" if light_on else "off")
            smoke_on = not smoke_on
            harness.house.set(SMOKE, "on" if smoke_on else "off")
            await _settle(harness.hass)
            _assert_empty_generation(harness)
            assert effects.pending == ()
        assert harness.metrics.worker_starts == 10
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert harness.metrics.dropped_protected == harness.metrics.dropped_critical == 0


def test_50000_unique_events_keep_total_runtime_retention_bounded(monkeypatch):
    entity_ids = [f"sensor.extreme_{index}" for index in range(50_000)]
    harness = _Harness(
        monkeypatch, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in entity_ids:
            harness.house.set(entity_id, "1")
        assert harness.metrics.retained_total <= event_runtime.MAX_TRANSIENT_RECORDS
        assert len(harness.runtime._gen.gaps) <= event_runtime.MAX_HISTORY_ENTITIES
        assert len(harness.runtime._gen.carry_previous) <= event_runtime.MAX_HISTORY_ENTITIES
        await _settle(harness.hass)
        _assert_empty_generation(harness)
        stop()

    asyncio.run(scenario())
    assert harness.metrics.history_degraded == 1
    assert harness.metrics.max_queue_depth == event_runtime.MAX_PENDING_EVENTS
    assert harness.metrics.max_retained_total <= event_runtime.MAX_TRANSIENT_RECORDS
