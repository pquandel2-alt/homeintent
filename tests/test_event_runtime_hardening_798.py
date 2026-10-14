"""7.9.8 EventRuntime degradation and consumer-correctness regressions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_event_runtime_hardening_796 import (
    CLIMATE,
    LIGHT,
    PERSON,
    TEMP,
    T0,
    WINDOW,
    _Harness,
    _agent,
    _nobody_goal,
    _settle,
    _store,
)

from homeintent import event_runtime
from homeintent.const import (
    CONF_AGENT_LONG_RUNNING_MINUTES,
    CONF_ROUTINE_DETECTION_ENABLED,
)
from homeintent.effect_monitor import EffectMonitor
from homeintent.entities import EntitySnapshot
from homeintent.event_priority import EventPriority
from homeintent.learning_policy import LearningMode
from homeintent.monitor_goal import MonitorGoalRuntime
from homeintent.service_call import ServiceCallPlan
from homeintent.thermal_tracker import ThermalExperienceTracker
from homeassistant.core import State


def _thermal_tracker(tmp_path):
    manager = SimpleNamespace(
        policy=SimpleNamespace(learning_mode=LearningMode.SILENT_LEARN),
        predictive_house=SimpleNamespace(thermal_model=lambda _area: None),
        async_record_thermal_observation=AsyncMock(),
        async_invalidate_thermal_model=AsyncMock(),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    snapshots = (
        EntitySnapshot(
            CLIMATE,
            "Heizung",
            "climate",
            "heat",
            area_id="wohnzimmer",
            attributes={"temperature": 22.0},
        ),
        EntitySnapshot(
            TEMP,
            "Temperatur",
            "sensor",
            "19.0",
            area_id="wohnzimmer",
            device_class="temperature",
            unit="°C",
        ),
        EntitySnapshot(
            WINDOW,
            "Fenster",
            "binary_sensor",
            "off",
            area_id="wohnzimmer",
            device_class="window",
        ),
    )
    tracker.observe_action(
        ServiceCallPlan(
            "climate", "set_temperature", CLIMATE, {"temperature": 22.0}
        ),
        snapshots,
        occurred_at=T0,
    )
    return tracker


def _seed_thermal(harness):
    harness.house.seed(CLIMATE, "heat", temperature=22.0)
    harness.house.seed(TEMP, "19.0", unit_of_measurement="°C")
    harness.house.seed(WINDOW, "off")


def test_thermal_protected_reaches_consumer_during_history_degradation(
    monkeypatch, tmp_path
):
    """Reproduce the 7.9.7 silent thermal loss behind a degraded history."""
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 8)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 2)
    manager = SimpleNamespace(
        policy=SimpleNamespace(learning_mode=LearningMode.SILENT_LEARN),
        predictive_house=SimpleNamespace(thermal_model=lambda _area: None),
        async_record_thermal_observation=AsyncMock(),
        async_invalidate_thermal_model=AsyncMock(),
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    queued = [f"switch.queued_{index}" for index in range(8)]
    rejected = [f"switch.rejected_{index}" for index in range(4)]
    harness = _Harness(
        monkeypatch,
        [*queued, *rejected, CLIMATE, TEMP, WINDOW],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        thermal_tracker=tracker,
    )
    for entity_id in (*queued, *rejected):
        harness.house.seed(entity_id, "off")
    harness.house.seed(CLIMATE, "heat", temperature=22.0)
    harness.house.seed(TEMP, "19.0", unit_of_measurement="°C")
    harness.house.seed(WINDOW, "off")
    entities = (
        EntitySnapshot(
            CLIMATE,
            "Heizung",
            "climate",
            "heat",
            area_id="wohnzimmer",
            attributes={"temperature": 22.0},
        ),
        EntitySnapshot(
            TEMP,
            "Temperatur",
            "sensor",
            "19.0",
            area_id="wohnzimmer",
            device_class="temperature",
            unit="°C",
        ),
        EntitySnapshot(
            WINDOW,
            "Fenster",
            "binary_sensor",
            "off",
            area_id="wohnzimmer",
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
            ServiceCallPlan(
                "climate", "set_temperature", CLIMATE, {"temperature": 22.0}
            ),
            entities,
            occurred_at=T0,
        )
        stop = harness.runtime.async_start()
        for entity_id in queued:
            harness.house.set(entity_id, "on")
        for entity_id in rejected:
            harness.house.set(entity_id, "on")
        assert harness.metrics.history_degraded == 1
        assert harness.runtime.interest.classify(
            TEMP,
            State(TEMP, "19.0", {"device_class": "temperature"}),
            State(TEMP, "20.0", {"device_class": "temperature"}),
        ) is EventPriority.PROTECTED
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of(TEMP) == [("20.0", "19.0")]
    assert observed_temperatures == ["20.0"]
    assert harness.metrics.dropped_protected == 0
    assert harness.metrics.retained_total == 0


def test_thermal_history_view_contains_full_watch_set_without_future_states(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 8)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    tracker = _thermal_tracker(tmp_path)
    ordinary = [f"switch.history_{index}" for index in range(12)]
    harness = _Harness(
        monkeypatch,
        [*ordinary, CLIMATE, TEMP, WINDOW],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        thermal_tracker=tracker,
    )
    for entity_id in ordinary:
        harness.house.seed(entity_id, "off")
    _seed_thermal(harness)
    observed: list[dict[str, str]] = []
    original = tracker.async_observe_states

    async def record(states, **kwargs):
        observed.append({item.entity_id: item.state for item in states})
        await original(states, **kwargs)

    tracker.async_observe_states = record  # type: ignore[method-assign]

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in ordinary:
            harness.house.set(entity_id, "on")
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        harness.house.set(WINDOW, "on")
        harness.house.set(CLIMATE, "cool", temperature=22.0)
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert observed == [
        {CLIMATE: "heat", TEMP: "20.0", WINDOW: "off"},
        {CLIMATE: "heat", TEMP: "20.0", WINDOW: "on"},
        {CLIMATE: "cool", TEMP: "20.0", WINDOW: "on"},
    ]
    assert all(set(view) == {CLIMATE, TEMP, WINDOW} for view in observed)
    assert harness.metrics.delivered_thermal == 3
    assert harness.metrics.suppressed_thermal == 0


def test_unsafe_thermal_history_is_counted_and_never_invented(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 3)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    tracker = _thermal_tracker(tmp_path)
    harness = _Harness(
        monkeypatch,
        [CLIMATE, TEMP, WINDOW],
        thermal_tracker=tracker,
    )
    _seed_thermal(harness)
    observed = AsyncMock()
    tracker.async_observe_states = observed  # type: ignore[method-assign]

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        harness.house.set(WINDOW, "on")
        harness.house.set(CLIMATE, "cool", temperature=22.0)
        # Same-priority future changes cannot displace the retained FIFO
        # thermal work. Their distinct rejected histories exceed the bound.
        harness.house.set(TEMP, "21.0", unit_of_measurement="°C")
        harness.house.set(WINDOW, "off")
        assert harness.metrics.history_degraded == 1
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    observed.assert_not_awaited()
    assert harness.metrics.dropped_protected == 2
    assert harness.metrics.suppressed_thermal == 3
    assert harness.metrics.consumer_suppressed_history >= 1
    assert "priority=protected" in caplog.text


def test_effect_and_monitor_consumers_survive_irrelevant_history_degradation(
    monkeypatch
):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 6)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    effects = EffectMonitor()

    class Store:
        loaded = True
        watched_value_entity_ids = frozenset({TEMP})
        watched_person_entity_ids = frozenset({PERSON})
        watched_nobody_home_person_ids = frozenset()
        nobody_home_uses_household = True

        def add_change_listener(self, _listener):
            return lambda: None

        def person_transition_needs_house_context(self, _entity_id):
            return False

        def person_transition_needs_snapshot(self, _entity_id):
            return False

    monitor = SimpleNamespace(
        store=Store(),
        async_process_person_transition=AsyncMock(return_value=()),
        async_process_value_change=AsyncMock(return_value=()),
    )
    ordinary = [
        "sensor.unwatched",
        *(f"switch.monitor_load_{index}" for index in range(9)),
    ]
    harness = _Harness(
        monkeypatch,
        [*ordinary, LIGHT, TEMP, PERSON, "person.ben"],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        effect_monitor=effects,
        monitor_runtime=monitor,
    )
    for entity_id in ordinary:
        harness.house.seed(entity_id, "off")
    harness.house.seed(LIGHT, "off")
    harness.house.seed(TEMP, "19.0")
    harness.house.seed(PERSON, "home")
    harness.house.seed("person.ben", "not_home")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in ordinary:
            harness.house.set(entity_id, "on")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        harness.house.set(TEMP, "20.0")
        harness.house.set(PERSON, "not_home")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert effects.pending == ()
    monitor.async_process_value_change.assert_awaited_once()
    monitor.async_process_person_transition.assert_awaited_once()
    person_view = monitor.async_process_person_transition.await_args.kwargs["entities"]
    person_states = {item.entity_id: item.state for item in person_view}
    assert person_states[PERSON] == "not_home"
    assert person_states["person.ben"] == "not_home"
    assert person_states[TEMP] == "20.0"
    assert harness.metrics.delivered_effect == 1
    assert harness.metrics.delivered_monitor == 2
    assert harness.metrics.suppressed_monitor == 0


def test_nobody_home_uses_historical_household_view(monkeypatch, tmp_path):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 6)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    store = _store(tmp_path)
    run_store = SimpleNamespace(
        async_seen_idempotency_key=AsyncMock(return_value=False),
        async_append=AsyncMock(),
    )
    users = SimpleNamespace(
        household=SimpleNamespace(person_entity_ids=(PERSON, "person.ben"))
    )
    monitor = MonitorGoalRuntime(
        store,
        run_store,
        users,
        AsyncMock(return_value=[]),
        AsyncMock(return_value=True),
    )
    ordinary = [f"switch.nobody_load_{index}" for index in range(10)]
    harness = _Harness(
        monkeypatch,
        [*ordinary, PERSON, "person.ben"],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        monitor_goals=store,
        monitor_runtime=monitor,
    )
    for entity_id in ordinary:
        harness.house.seed(entity_id, "off")
    harness.house.seed(PERSON, "home")
    harness.house.seed("person.ben", "home")

    async def scenario() -> None:
        await store.async_save(_nobody_goal("goal_household"))
        stop = harness.runtime.async_start()
        for entity_id in ordinary:
            harness.house.set(entity_id, "on")
        harness.house.set(PERSON, "not_home")
        harness.house.set("person.ben", "not_home")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    # Anna's earlier transition still sees Ben at home; only Ben's later
    # transition completes the nobody-home goal. A future-state leak would
    # have produced two completed runs.
    assert run_store.async_append.await_count == 1
    assert harness.metrics.delivered_monitor == 2
    assert harness.metrics.suppressed_monitor == 0


def test_nobody_home_suppresses_an_unsafe_household_history(monkeypatch, tmp_path):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 3)
    monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    store = _store(tmp_path)
    run_store = SimpleNamespace(
        async_seen_idempotency_key=AsyncMock(return_value=False),
        async_append=AsyncMock(),
    )
    household_ids = (PERSON, "person.ben")
    users = SimpleNamespace(
        household=SimpleNamespace(person_entity_ids=household_ids)
    )
    monitor = MonitorGoalRuntime(
        store,
        run_store,
        users,
        AsyncMock(return_value=[]),
        AsyncMock(return_value=True),
    )
    fillers = ["person.fill_one", "person.fill_two", "person.fill_three"]
    harness = _Harness(
        monkeypatch,
        [*household_ids, *fillers],
        monitor_goals=store,
        monitor_runtime=monitor,
    )
    for entity_id in (*household_ids, *fillers):
        harness.house.seed(entity_id, "home")

    async def scenario() -> None:
        await store.async_save(_nobody_goal("goal_unsafe_household"))
        stop = harness.runtime.async_start()
        # Anna's transition and two irrelevant persons fill the protected
        # queue. Ben's later transition is rejected; a second distinct reject
        # exceeds the tiny history bound and removes an exact rewind for Ben.
        harness.house.set(PERSON, "not_home")
        harness.house.set(fillers[0], "not_home")
        harness.house.set(fillers[1], "not_home")
        harness.house.set("person.ben", "not_home")
        harness.house.set(fillers[2], "not_home")
        assert harness.metrics.history_degraded == 1
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    # Ben was still home when Anna left. The fresh snapshot contains Ben's
    # later rejected state, so delivery would be a future-state leak.
    run_store.async_append.assert_not_awaited()
    assert harness.metrics.suppressed_monitor == 3
    assert harness.metrics.consumer_suppressed_history >= 3


def test_proactive_delivery_and_suppression_are_disjoint_in_history_mode(
    monkeypatch
):
    async def run(requires_house: bool):
        monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
        monkeypatch.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
        observe = AsyncMock()
        proactive = SimpleNamespace(
            enabled=True,
            requires_complete_house_view=requires_house,
            requires_registry_data=False,
            is_relevant_event=lambda *_args: True,
            async_observe_state=observe,
        )
        entity_ids = [f"switch.proactive_{index}" for index in range(8)]
        harness = _Harness(monkeypatch, entity_ids, proactive_context=proactive)
        for entity_id in entity_ids:
            harness.house.seed(entity_id, "off")
        stop = harness.runtime.async_start()
        for entity_id in entity_ids:
            harness.house.set(entity_id, "on")
        await _settle(harness.hass)
        stop()
        return harness, observe

    safe, safe_observe = asyncio.run(run(False))
    assert safe_observe.await_count == safe.metrics.delivered_proactive > 0
    assert safe.metrics.suppressed_proactive == 0
    blocked, blocked_observe = asyncio.run(run(True))
    blocked_observe.assert_not_awaited()
    assert blocked.metrics.delivered_proactive == 0
    assert blocked.metrics.suppressed_proactive > 0
    assert blocked.metrics.consumer_suppressed_history > 0


def test_registry_degradation_separates_safe_delivery_from_suppression(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(event_runtime, "SNAPSHOT_RETRY_BASE_SECONDS", 0.001)
    tracker = _thermal_tracker(tmp_path)
    effects = EffectMonitor()
    proactive = SimpleNamespace(
        enabled=True,
        requires_complete_house_view=True,
        requires_registry_data=True,
        is_relevant_event=lambda *_args: True,
        async_observe_state=AsyncMock(),
    )
    harness = _Harness(
        monkeypatch,
        [LIGHT, TEMP, CLIMATE, WINDOW, "switch.proactive"],
        effect_monitor=effects,
        thermal_tracker=tracker,
        proactive_context=proactive,
    )
    _seed_thermal(harness)
    harness.house.seed(LIGHT, "off")
    harness.house.seed("switch.proactive", "off")

    def broken(*_args):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", broken)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        harness.house.set("switch.proactive", "on")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert effects.pending == ()
    assert harness.metrics.registry_degraded_batches == 1
    assert harness.metrics.delivered_effect == 1
    assert harness.metrics.suppressed_thermal == 1
    assert harness.metrics.suppressed_proactive == 3
    assert harness.metrics.consumer_suppressed_registry >= 2
    assert harness.metrics.discarded_registry == 1
    assert harness.metrics.dequeued == 3
    assert harness.metrics.processed == 2


def test_filter_metrics_are_mutually_exclusive_and_reconcile_received(monkeypatch):
    cases = []

    def record(harness, entity_id):
        async def scenario() -> None:
            stop = harness.runtime.async_start()
            harness.house.seed(entity_id, "off")
            harness.house.set(entity_id, "on")
            stop()
            await _settle(harness.hass)

        asyncio.run(scenario())
        cases.append(harness.metrics)

    record(_Harness(monkeypatch, ["switch.none"]), "switch.none")
    record(
        _Harness(monkeypatch, ["sensor.category"], categories="opening_while_away"),
        "sensor.category",
    )
    negative = SimpleNamespace(
        enabled=True,
        is_relevant_event=lambda *_args: False,
        async_observe_state=AsyncMock(),
    )
    record(
        _Harness(monkeypatch, ["switch.proactive"], proactive_context=negative),
        "switch.proactive",
    )
    record(
        _Harness(
            monkeypatch,
            ["sensor.both"],
            categories="opening_while_away",
            proactive_context=negative,
        ),
        "sensor.both",
    )
    effects = SimpleNamespace(
        watched_entity_ids=frozenset({LIGHT}),
        add_watch_listener=lambda _listener: (lambda: None),
        set_expired_handler=lambda _handler: None,
        set_pending_probe=lambda _probe: None,
        observe=lambda *_args: None,
    )
    protected = _Harness(monkeypatch, [LIGHT], effect_monitor=effects)
    record(protected, LIGHT)
    unselected = _Harness(monkeypatch, ["switch.selected"])
    record(unselected, "switch.other")

    assert [m.filtered_no_consumer for m in cases] == [1, 0, 0, 0, 0, 0]
    assert [m.filtered_by_category for m in cases] == [0, 1, 0, 1, 0, 0]
    assert [m.filtered_by_proactive for m in cases] == [0, 0, 1, 0, 0, 0]
    assert [m.filtered_unselected for m in cases] == [0, 0, 0, 0, 0, 1]
    assert cases[4].queued == 1
    for metrics in cases:
        assert metrics.received == (
            metrics.filtered_unselected
            + metrics.filtered_no_interest
            + metrics.queued
            + metrics.rejected_queue_full
        )


def test_unexpected_worker_exit_restarts_once_with_pending_work(monkeypatch):
    harness = _Harness(
        monkeypatch,
        ["switch.restart"],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
    )
    harness.house.seed("switch.restart", "off")
    original = harness.runtime._async_drain
    calls = 0

    async def fail_once(gen):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker failed")
        await original(gen)

    monkeypatch.setattr(harness.runtime, "_async_drain", fail_once)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set("switch.restart", "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert calls == 2
    assert harness.metrics.worker_starts == 2
    assert harness.metrics.worker_restarts == 1
    assert harness.metrics.processed == 1


def test_event_during_worker_finalization_gets_one_recovery_worker(monkeypatch):
    entity_ids = ["switch.finalize_first", "switch.finalize_second"]
    harness = _Harness(
        monkeypatch,
        entity_ids,
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "off")
    original_cleanup = event_runtime._Generation.stable_cleanup
    injected = False

    def cleanup_with_event(gen):
        nonlocal injected
        original_cleanup(gen)
        if not injected:
            injected = True
            # The current task has seen live == 0 but its done callback has
            # not run yet. The callback must recover this newly queued work.
            harness.house.set(entity_ids[1], "on")

    monkeypatch.setattr(
        event_runtime._Generation, "stable_cleanup", cleanup_with_event
    )

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(entity_ids[0], "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert injected
    assert harness.metrics.processed == 2
    assert harness.metrics.worker_starts == 2
    assert harness.metrics.worker_restarts == 1
    assert harness.metrics.retained_total == 0


def test_event_evaluation_exception_is_counted_without_worker_restart(monkeypatch):
    harness = _Harness(
        monkeypatch,
        ["switch.evaluation_error"],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
    )
    harness.house.seed("switch.evaluation_error", "off")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("event evaluation failed")

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", broken)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set("switch.evaluation_error", "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.metrics.dequeued == 1
    assert harness.metrics.processed == 1
    assert harness.metrics.evaluation_failures == 1
    assert harness.metrics.worker_starts == 1
    assert harness.metrics.worker_restarts == 0
    assert harness.metrics.retained_total == 0


def test_long_running_state_end_to_end_threshold_and_deduplication(monkeypatch):
    agent = _agent()
    harness = _Harness(
        monkeypatch,
        ["switch.pump"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 60},
        proactive_agent=agent,
    )
    harness.house.seed("switch.pump", "on")

    def fire_attribute_update(detail: str) -> None:
        entity_id = "switch.pump"
        old = harness.hass.states.get(entity_id)
        new = State(entity_id, "on", {"detail": detail})
        new.last_changed = T0
        harness.hass.states._states[entity_id] = new
        harness.house.offset += 1
        harness.hass.bus.fire(
            SimpleNamespace(
                data={"entity_id": entity_id, "old_state": old, "new_state": new},
                time_fired=T0 + timedelta(milliseconds=harness.house.offset),
                context=SimpleNamespace(id=f"ctx-{harness.house.offset}"),
            )
        )

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.offset = 30 * 60 * 1000
        fire_attribute_update("below")
        await _settle(harness.hass)
        assert agent.async_signal.await_count == 0
        harness.house.offset = 61 * 60 * 1000
        fire_attribute_update("above")
        await _settle(harness.hass)
        fire_attribute_update("another attribute")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert agent.async_signal.await_count == 1
    assert agent.async_signal.await_args.args[0]["rule_id"] == (
        "situation:long_running_state"
    )


def test_long_running_disabled_unknown_and_unsupported_never_signal(monkeypatch):
    agent = _agent()
    harness = _Harness(
        monkeypatch,
        ["switch.disabled", "switch.unknown", "sensor.unsupported"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 0},
        proactive_agent=agent,
    )
    for entity_id in ("switch.disabled", "switch.unknown", "sensor.unsupported"):
        harness.house.seed(entity_id, "on")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.offset = 24 * 60 * 60 * 1000
        harness.house.set("switch.disabled", "on")
        harness.entry.options[CONF_AGENT_LONG_RUNNING_MINUTES] = 1
        harness.house.set("switch.unknown", "unknown")
        harness.house.set("sensor.unsupported", "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    agent.async_signal.assert_not_awaited()

    no_category_agent = _agent()
    no_category = _Harness(
        monkeypatch,
        ["switch.no_category"],
        categories="",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 1},
        proactive_agent=no_category_agent,
    )
    no_category.house.seed("switch.no_category", "on")

    async def category_disabled() -> None:
        stop = no_category.runtime.async_start()
        no_category.house.offset = 24 * 60 * 60 * 1000
        no_category.house.set("switch.no_category", "on")
        await _settle(no_category.hass)
        stop()

    asyncio.run(category_disabled())
    no_category_agent.async_signal.assert_not_awaited()
