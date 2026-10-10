"""7.9.7: EventRuntime ranks, targeted interest and bounded bookkeeping.

Findings of the 7.9.6 review (reproduced by
``scripts/event_runtime_repro_797.py``):

1. ``LOSSLESS`` mixed directly watched events (expected effects, monitor
   goals, thermal cycles) with ordinary category events: an expected
   effect's state change was dropped behind 4096 ordinary switch changes,
   and a critical event displaced the oldest lossless entry - the expected
   effect - although ordinary category events were still queued.
2. Any configured agent event category made the whole house a consumer
   (``safety`` queued every switch and every temperature change).
3. An enabled V12 context queued what its own ``is_relevant_event()``
   called irrelevant.
4. After a fully drained burst ``latest``, ``gaps`` and ``carry_previous``
   kept thousands of events and states; ``gaps`` grew with every distinct
   entity dropped under overload.

Every burst below is synchronous: no ``await`` and no loop turn between the
events, so the worker cannot run before the whole burst is queued. The
harness (stub state machine, real ``SituationRuntime``) is the one of the
7.9.6 tests.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

from test_event_runtime_hardening_796 import (  # noqa: E402
    CLIMATE,
    DOOR,
    LIGHT,
    MOISTURE,
    PERSON,
    SMOKE,
    T0,
    TEMP,
    WINDOW,
    _agent,
    _Harness,
    _monitor,
    _person_goal,
    _settle,
    _signals,
    _store,
    _value_goal,
)

from homeintent import event_runtime  # noqa: E402
from homeintent.agent_config_validation import EVENT_CATEGORIES  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_PROACTIVE_CONTEXT_ENABLED,
    CONF_ROUTINE_DETECTION_ENABLED,
)
from homeintent.effect_monitor import EffectMonitor  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.event_interest import CATEGORY_INTEREST  # noqa: E402
from homeintent.event_priority import (  # noqa: E402
    EVICTION_ORDER,
    PRIORITY_RANK,
    EventPriority,
)
from homeintent.learning_policy import LearningMode  # noqa: E402
from homeintent.proactive_runtime import ProactiveRuntime  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeintent.thermal_tracker import ThermalExperienceTracker  # noqa: E402
from homeassistant.core import State  # noqa: E402

FULL = 4096
LOAD = 6000
ROUTINE_ON = {CONF_ROUTINE_DETECTION_ENABLED: True}
OUTDOOR = "sensor.aussentemperatur"


def _switches(count: int, prefix: str = "switch.last") -> list[str]:
    return [f"{prefix}_{index}" for index in range(count)]


def _lights(count: int) -> list[str]:
    return [f"light.last_{index}" for index in range(count)]


def _structures(runtime: Any) -> dict[str, int]:
    gen = runtime._gen
    return {
        "live": gen.live,
        "queue": len(gen.queue),
        "latest": len(gen.latest),
        "gaps": len(gen.gaps),
        "carry_previous": len(gen.carry_previous),
        "view_only": len(gen.view_only),
        "coalescible": len(gen.coalescible),
        "category": len(gen.category),
        "routine": len(gen.routine),
        "protected": len(gen.protected),
        "unprocessed_by_entity": len(gen.unprocessed_by_entity),
        "tombstones": gen.tombstones,
    }


EMPTY = {
    "live": 0, "queue": 0, "latest": 0, "gaps": 0, "carry_previous": 0,
    "view_only": 0, "coalescible": 0, "category": 0, "routine": 0, "protected": 0,
    "unprocessed_by_entity": 0, "tombstones": 0,
}
RETAINED_EMPTY = {
    "retained_entries": 0, "retained_index": 0, "retained_latest": 0,
    "retained_gaps": 0, "retained_carry": 0, "retained_tombstones": 0,
    "retained_unprocessed": 0, "history_degraded_active": 0,
}


def _retained(runtime: Any) -> dict[str, int]:
    retention = runtime.retention()
    return {key: retention[key] for key in RETAINED_EMPTY}


def _record_expiries(harness: _Harness) -> list[str]:
    """Deadlines reported as "effect missing" (the light did react)."""
    expired: list[str] = []

    async def _expired(effect: Any) -> None:
        expired.append(effect.entity_id)

    harness.runtime.async_handle_expected_effect_expired = _expired  # type: ignore[method-assign]
    return expired


# --------------------------------------------------------------------------
# Ranks
# --------------------------------------------------------------------------

def test_rank_order_and_eviction_order():
    assert [priority for priority in sorted(PRIORITY_RANK, key=PRIORITY_RANK.__getitem__)] == [
        EventPriority.COALESCIBLE, EventPriority.CATEGORY, EventPriority.ROUTINE,
        EventPriority.PROTECTED, EventPriority.CRITICAL,
    ]
    # Lowest first; a critical event is never a victim.
    assert EVICTION_ORDER == (
        EventPriority.COALESCIBLE, EventPriority.CATEGORY, EventPriority.ROUTINE,
        EventPriority.PROTECTED,
    )
    assert not hasattr(EventPriority, "LOSSLESS")


# --------------------------------------------------------------------------
# 9.1 4096 switches, then an expected effect
# --------------------------------------------------------------------------

def test_effect_after_4096_switches_under_safety_is_processed(monkeypatch):
    effects = EffectMonitor(timeout=timedelta(milliseconds=200))
    switches = _switches(FULL)
    harness = _Harness(monkeypatch, [*switches, LIGHT], categories="safety",
                       effect_monitor=effects)
    expired = _record_expiries(harness)
    for entity_id in [*switches, LIGHT]:
        harness.house.seed(entity_id, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for switch in switches:
            harness.house.set(switch, "on")
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        assert harness.runtime.has_pending(LIGHT)
        await _settle(harness.hass)
        await asyncio.sleep(0.3)  # past the effect's deadline
        await _settle(harness.hass)
        assert harness.pending_tasks() == []
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert effects.pending == ()
    assert expired == []  # no false "effect missing"
    assert metrics.filtered_no_interest == FULL
    assert metrics.filtered_by_category == FULL
    assert metrics.queued == 1
    assert metrics.dropped_protected == 0
    assert metrics.dropped_lossless == 0
    assert harness.pending_tasks() == []


def test_effect_after_a_full_queue_of_category_events_displaces_one(monkeypatch):
    # The same burst with real category events: 4096 lights switched on
    # while ``light_unoccupied`` is configured fill the queue.
    effects = EffectMonitor(timeout=timedelta(milliseconds=200))
    lights = _lights(FULL)
    harness = _Harness(monkeypatch, [*lights, LIGHT], categories="light_unoccupied",
                       effect_monitor=effects)
    expired = _record_expiries(harness)
    for entity_id in [*lights, LIGHT]:
        harness.house.seed(entity_id, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for light in lights:
            harness.house.set(light, "on")
        assert harness.runtime._gen.live == FULL
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")
        assert harness.runtime.has_pending(LIGHT)
        await _settle(harness.hass)
        await asyncio.sleep(0.3)
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert effects.pending == ()
    assert expired == []
    assert metrics.dropped_protected == 0
    # The oldest ordinary category event made room - nothing else.
    assert metrics.dropped_category == 1
    assert harness.evaluations_of(lights[0]) == []
    assert harness.evaluations_of(lights[1]) == [("on", "off")]
    assert metrics.worker_starts == 1
    assert harness.pending_tasks() == []


# --------------------------------------------------------------------------
# 9.2 expected effect queued first, then a critical event
# --------------------------------------------------------------------------

def test_critical_displaces_a_category_event_not_the_queued_effect(monkeypatch):
    effects = EffectMonitor()
    agent = _agent()
    lights = _lights(FULL - 1)
    harness = _Harness(monkeypatch, [*lights, LIGHT, SMOKE],
                       categories="safety,light_unoccupied",
                       effect_monitor=effects, proactive_agent=agent)
    for entity_id in [*lights, LIGHT]:
        harness.house.seed(entity_id, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")  # the oldest entry of the queue
        for light in lights:
            harness.house.set(light, "on")
        assert harness.runtime._gen.live == FULL
        harness.house.set(SMOKE, "on")
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert effects.pending == ()
    assert metrics.dropped_protected == 0
    assert metrics.dropped_critical == 0
    assert metrics.dropped_category == 1
    assert harness.evaluations_of(lights[0]) == []  # the victim
    assert any(payload["safety_critical"] for payload in _signals(agent))


def test_protected_is_displaced_only_by_critical_as_the_last_resort(monkeypatch, caplog):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 3)
    effects = EffectMonitor()
    harness = _Harness(monkeypatch, [LIGHT, "light.kueche", SMOKE, DOOR],
                       categories="opening_while_away", effect_monitor=effects)
    for entity_id in (LIGHT, "light.kueche", DOOR):
        harness.house.seed(entity_id, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", [LIGHT, "light.kueche"], {}))
        with caplog.at_level(logging.WARNING, logger=event_runtime.__name__):
            harness.house.set(LIGHT, "on")
            harness.house.set("light.kueche", "on")
            harness.house.set(DOOR, "on")
            # A category event never displaces a protected one.
            harness.house.set(DOOR, "off")
            assert harness.metrics.dropped_category == 1
            assert harness.metrics.dropped_protected == 0
            # A critical event takes the category entry first ...
            harness.house.set(SMOKE, "on")
            assert harness.metrics.dropped_category == 2
            assert harness.metrics.dropped_protected == 0
            # ... and only then, as the last resort, the oldest protected one.
            harness.house.set(SMOKE, "off")
        assert harness.metrics.dropped_protected == 1
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert harness.evaluations_of(LIGHT) == []
    assert harness.evaluations_of("light.kueche") == [("on", "off")]
    assert harness.evaluations_of(SMOKE) == [("on", "off"), ("off", "on")]
    assert harness.metrics.dropped_critical == 0
    errors = [record for record in caplog.records if "watched state change" in record.getMessage()]
    assert len(errors) == 1 and errors[0].levelno == logging.ERROR


def test_same_rank_never_displaces_and_order_stays_global(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
    harness = _Harness(monkeypatch, [DOOR, WINDOW, LIGHT, SMOKE],
                       categories="opening_while_away,light_unoccupied")
    for entity_id in (DOOR, WINDOW, LIGHT):
        harness.house.seed(entity_id, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(DOOR, "on")
        harness.house.set(LIGHT, "on")
        harness.house.set(WINDOW, "on")
        harness.house.set(DOOR, "off")
        harness.house.set(WINDOW, "off")  # full: rejected, not displacing
        harness.house.set(SMOKE, "on")  # displaces the oldest category entry
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [(entity, state) for entity, state, *_ in harness.evaluated] == [
        (LIGHT, "on"), (WINDOW, "on"), (DOOR, "off"), (SMOKE, "on"),
    ]
    assert harness.metrics.dropped_category == 2


# --------------------------------------------------------------------------
# 9.3 / 9.4 monitor goals behind a full queue of category events
# --------------------------------------------------------------------------

def _monitor_category_harness(
    monkeypatch: Any, tmp_path: Path, record: Any,
) -> tuple[_Harness, Any, list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    store = _store(tmp_path)
    monitor = _monitor(store)
    transitions: list[tuple[Any, ...]] = []
    values: list[tuple[Any, ...]] = []
    process_person = monitor.async_process_person_transition
    process_value = monitor.async_process_value_change

    async def _person(*args: Any, **kwargs: Any) -> Any:
        transitions.append(args)
        return await process_person(*args, **kwargs)

    async def _value(*args: Any, **kwargs: Any) -> Any:
        values.append(args)
        return await process_value(*args, **kwargs)

    monitor.async_process_person_transition = _person  # type: ignore[method-assign]
    monitor.async_process_value_change = _value  # type: ignore[method-assign]
    lights = _lights(FULL)
    harness = _Harness(monkeypatch, [*lights, PERSON, TEMP], categories="light_unoccupied",
                       monitor_goals=store, monitor_runtime=monitor)
    for light in lights:
        harness.house.seed(light, "off")
    harness.house.seed(PERSON, "home")
    harness.house.seed(TEMP, "20.0")

    async def _prepare() -> None:
        await store.async_save(record)

    asyncio.run(_prepare())
    return harness, store, transitions, values


def test_monitor_person_after_a_full_queue_keeps_both_transitions(monkeypatch, tmp_path):
    harness, store, transitions, _values = _monitor_category_harness(
        monkeypatch, tmp_path, _person_goal("goal_anna", PERSON),
    )
    reads = store.read_count

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        # A person is a category input of ``light_unoccupied`` as well; the
        # monitor goal ranks it higher.
        assert harness.runtime.interest.classify(
            PERSON, State(PERSON, "home"), State(PERSON, "not_home")
        ) is EventPriority.PROTECTED
        for light in _lights(FULL):
            harness.house.set(light, "on")
        harness.house.set(PERSON, "not_home")
        harness.house.set(PERSON, "home")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert transitions == [(PERSON, "home", "not_home"), (PERSON, "not_home", "home")]
    assert harness.evaluations_of(PERSON) == [("not_home", "home"), ("home", "not_home")]
    metrics = harness.metrics
    assert metrics.coalesced == 0
    assert metrics.dropped_protected == 0
    assert metrics.dropped_category == 2
    assert store.read_count == reads


def test_value_monitor_after_a_full_queue_gets_each_value_in_order(monkeypatch, tmp_path):
    harness, store, _transitions, values = _monitor_category_harness(
        monkeypatch, tmp_path, _value_goal("goal_temp", TEMP),
    )
    reads = store.read_count
    lights = _lights(FULL)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for light in lights[:FULL // 2]:
            harness.house.set(light, "on")
        harness.house.set(TEMP, "unavailable")
        for light in lights[FULL // 2:]:
            harness.house.set(light, "on")
        harness.house.set(TEMP, "25.0")
        harness.house.set(TEMP, "21.0")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [args for args in values if args[0] == TEMP] == [(TEMP, 25.0), (TEMP, 21.0)]
    assert harness.metrics.dropped_protected == 0
    assert harness.metrics.coalesced == 0
    assert store.read_count == reads  # no monitor read after the first load


# --------------------------------------------------------------------------
# 9.5 thermal cycle under category load
# --------------------------------------------------------------------------

def test_thermal_cycle_keeps_every_watched_event_under_category_load(monkeypatch, tmp_path):
    manager = SimpleNamespace(
        policy=SimpleNamespace(learning_mode=LearningMode.SILENT_LEARN),
        predictive_house=SimpleNamespace(thermal_model=lambda _area: SimpleNamespace(
            binding=SimpleNamespace(outdoor_temperature_entity_id=OUTDOOR),
            model_id="model_wohnzimmer",
        )),
        async_record_thermal_observation=_agent().async_signal,
        async_invalidate_thermal_model=_agent().async_signal,
    )
    tracker = ThermalExperienceTracker(manager, state_path=tmp_path / "thermal.json")
    observed: list[dict[str, str]] = []
    observe = tracker.async_observe_states

    async def _observe(entities: Any, *, occurred_at: Any) -> None:
        observed.append({item.entity_id: item.state for item in entities
                         if item.entity_id in (CLIMATE, TEMP, WINDOW, OUTDOOR)})
        await observe(entities, occurred_at=occurred_at)

    tracker.async_observe_states = _observe  # type: ignore[method-assign]
    lights = _lights(FULL)
    harness = _Harness(
        monkeypatch, [*lights, CLIMATE, TEMP, WINDOW, OUTDOOR],
        categories="light_unoccupied", thermal_tracker=tracker,
    )
    for light in lights:
        harness.house.seed(light, "off")
    harness.house.seed(CLIMATE, "heat", temperature=22.0)
    harness.house.seed(TEMP, "19.0", unit_of_measurement="°C")
    harness.house.seed(WINDOW, "off")
    harness.house.seed(OUTDOOR, "5.0", unit_of_measurement="°C")
    entities = (
        EntitySnapshot(CLIMATE, "Heizung", "climate", "heat", area_id="wohnzimmer",
                       attributes={"temperature": 22.0}),
        EntitySnapshot(TEMP, "Temperatur", "sensor", "19.0", area_id="wohnzimmer",
                       device_class="temperature", unit="°C"),
        EntitySnapshot(WINDOW, "Fenster", "binary_sensor", "off", area_id="wohnzimmer",
                       device_class="window"),
        EntitySnapshot(OUTDOOR, "Außen", "sensor", "5.0", device_class="temperature",
                       unit="°C"),
    )

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        tracker.observe_action(
            ServiceCallPlan("climate", "set_temperature", CLIMATE, {"temperature": 22.0}),
            entities, occurred_at=T0,
        )
        assert tracker.watched_entity_ids == {CLIMATE, TEMP, WINDOW, OUTDOOR}
        for light in lights:
            harness.house.set(light, "on")
        harness.house.set(TEMP, "19.5", unit_of_measurement="°C")
        harness.house.set(WINDOW, "on")
        harness.house.set(OUTDOOR, "4.5", unit_of_measurement="°C")
        harness.house.set(WINDOW, "off")
        # The climate reports progress (same mode and setpoint: the cycle
        # goes on; a new setpoint would end it as contaminated).
        harness.house.set(CLIMATE, "heat", temperature=22.0, current_temperature=19.5)
        harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert metrics.dropped_protected == 0
    assert harness.evaluations_of(TEMP) == [("19.5", "19.0"), ("20.0", "19.5")]
    assert harness.evaluations_of(WINDOW) == [("on", "off"), ("off", "on")]
    assert harness.evaluations_of(OUTDOOR) == [("4.5", "5.0")]
    assert harness.evaluations_of(CLIMATE) == [("heat", "heat")]
    # The tracker saw each watched change with the house of its moment.
    assert observed[-6:] == [
        {CLIMATE: "heat", TEMP: "19.5", WINDOW: "off", OUTDOOR: "5.0"},
        {CLIMATE: "heat", TEMP: "19.5", WINDOW: "on", OUTDOOR: "5.0"},
        {CLIMATE: "heat", TEMP: "19.5", WINDOW: "on", OUTDOOR: "4.5"},
        {CLIMATE: "heat", TEMP: "19.5", WINDOW: "off", OUTDOOR: "4.5"},
        {CLIMATE: "heat", TEMP: "19.5", WINDOW: "off", OUTDOOR: "4.5"},
        {CLIMATE: "heat", TEMP: "20.0", WINDOW: "off", OUTDOOR: "4.5"},
    ]
    assert tracker.active[0].window_opened


# --------------------------------------------------------------------------
# 9.6 category matrix
# --------------------------------------------------------------------------

SWITCH = "switch.kaffeemaschine"
MEDIA = "media_player.wohnzimmer"
COVER = "cover.garagentor"


def _st(entity_id: str, value: str, device_class: str | None = None) -> State:
    return State(entity_id, value, {"device_class": device_class} if device_class else {})


# Sample changes: name -> (entity_id, old_state, new_state).
SAMPLES: dict[str, tuple[str, Any, Any]] = {
    "switch_on": (SWITCH, _st(SWITCH, "off"), _st(SWITCH, "on")),
    "number": (TEMP, _st(TEMP, "20.1", "temperature"), _st(TEMP, "20.2", "temperature")),
    "media_playing": (MEDIA, _st(MEDIA, "idle"), _st(MEDIA, "playing")),
    "smoke": (SMOKE, _st(SMOKE, "off", "smoke"), _st(SMOKE, "on", "smoke")),
    "door_open": (DOOR, _st(DOOR, "off", "door"), _st(DOOR, "on", "door")),
    "window_close": (WINDOW, _st(WINDOW, "on", "window"), _st(WINDOW, "off", "window")),
    "garage_open": (COVER, _st(COVER, "closed", "garage_door"),
                    _st(COVER, "open", "garage_door")),
    "person_leaves": (PERSON, _st(PERSON, "home"), _st(PERSON, "not_home")),
    "climate_heat": (CLIMATE, _st(CLIMATE, "off"), _st(CLIMATE, "heat")),
    "light_on": (LIGHT, _st(LIGHT, "off"), _st(LIGHT, "on")),
    "light_dimmed": (LIGHT, _st(LIGHT, "on"), State(LIGHT, "on", {"brightness": 40})),
    "light_off": (LIGHT, _st(LIGHT, "on"), _st(LIGHT, "off")),
    "unavailable": (SWITCH, _st(SWITCH, "on"), _st(SWITCH, "unavailable")),
    "unknown": (SWITCH, _st(SWITCH, "on"), _st(SWITCH, "unknown")),
}
ALWAYS = {"smoke"}
EXPECTED: dict[str, set[str]] = {
    "safety": set(ALWAYS),
    "safety_alarm": set(ALWAYS),
    "device_unavailable": {*ALWAYS, "unavailable"},
    "opening_while_away": {*ALWAYS, "door_open", "window_close", "garage_open",
                           "person_leaves"},
    "window_heating": {*ALWAYS, "door_open", "window_close", "garage_open",
                       "climate_heat"},
    "light_unoccupied": {*ALWAYS, "light_on", "light_dimmed", "person_leaves"},
    "long_running_state": set(ALWAYS),
    "routine_anomaly": set(ALWAYS),
    "expected_effect_missing": set(ALWAYS),
}


def test_every_configurable_category_is_in_the_matrix():
    assert set(EXPECTED) == set(EVENT_CATEGORIES) == set(CATEGORY_INTEREST)


def _classify_all(monkeypatch: Any, categories: str, **options: Any) -> dict[str, Any]:
    harness = _Harness(monkeypatch, [], categories=categories, options=options or None)
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    return {name: classify(*sample) for name, sample in SAMPLES.items()}


@pytest.mark.parametrize("category", sorted(EXPECTED))
def test_category_needs_only_its_own_inputs(monkeypatch, category):
    ranks = _classify_all(monkeypatch, category)
    relevant = {name for name, rank in ranks.items() if rank is not None}
    assert relevant == EXPECTED[category]
    assert ranks["smoke"] is EventPriority.CRITICAL  # never filtered
    for name in relevant - ALWAYS:
        assert ranks[name] is EventPriority.CATEGORY, name


@pytest.mark.parametrize("category", sorted(EXPECTED))
def test_category_filters_irrelevant_events_in_the_callback(monkeypatch, category):
    entity_ids = sorted({sample[0] for sample in SAMPLES.values()})
    harness = _Harness(monkeypatch, entity_ids, categories=category)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id, old, new in SAMPLES.values():
            harness.hass.bus.fire(SimpleNamespace(
                data={"entity_id": entity_id, "old_state": old, "new_state": new},
                time_fired=T0, context=SimpleNamespace(id="ctx"),
            ))
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    metrics = harness.metrics
    expected = len(EXPECTED[category])
    assert metrics.queued == expected
    assert metrics.filtered_no_interest == len(SAMPLES) - expected
    assert metrics.filtered_by_category == len(SAMPLES) - expected


@pytest.mark.parametrize("category", sorted(EXPECTED))
def test_direct_protected_interest_overrides_the_category_filter(monkeypatch, category):
    effects = EffectMonitor()
    harness = _Harness(monkeypatch, [], categories=category, effect_monitor=effects)

    async def scenario() -> None:
        harness.runtime.async_start()
        classify = harness.runtime.interest.classify
        assert classify(*SAMPLES["switch_on"]) is None
        effects.register(ServiceCallPlan("switch", "turn_on", SWITCH, {}))
        assert classify(*SAMPLES["switch_on"]) is EventPriority.PROTECTED
        await effects.async_close()

    asyncio.run(scenario())


def test_several_categories_form_the_union(monkeypatch):
    categories = ["device_unavailable", "opening_while_away", "light_unoccupied"]
    ranks = _classify_all(monkeypatch, ",".join(categories))
    relevant = {name for name, rank in ranks.items() if rank is not None}
    assert relevant == set().union(*(EXPECTED[category] for category in categories))
    all_categories = _classify_all(monkeypatch, ",".join(sorted(EXPECTED)))
    assert {name for name, rank in all_categories.items() if rank is not None} == (
        set().union(*EXPECTED.values())
    )


def test_routine_detection_widens_only_where_its_statistics_are_read(monkeypatch):
    # Routine detection without a category runs nothing.
    assert {
        name for name, rank in _classify_all(monkeypatch, "", **ROUTINE_ON).items()
        if rank is not None
    } == ALWAYS
    # ``routine_anomaly`` alone: nothing without routine detection ...
    assert {
        name for name, rank in _classify_all(monkeypatch, "routine_anomaly").items()
        if rank is not None
    } == ALWAYS
    # ... every selected entity with it; plain numbers may be merged.
    ranks = _classify_all(monkeypatch, "routine_anomaly", **ROUTINE_ON)
    assert ranks["number"] is EventPriority.COALESCIBLE
    assert ranks["smoke"] is EventPriority.CRITICAL
    assert {
        name for name, rank in ranks.items() if rank is EventPriority.ROUTINE
    } == set(SAMPLES) - {"number", "smoke"}
    # ``device_unavailable``: the statistics explain any entity's situation.
    ranks = _classify_all(monkeypatch, "device_unavailable", **ROUTINE_ON)
    assert ranks["switch_on"] is EventPriority.ROUTINE
    # ``safety``: they explain only the safety devices' own situations; the
    # persons are context of every routine observation.
    ranks = _classify_all(monkeypatch, "safety", **ROUTINE_ON)
    assert {name for name, rank in ranks.items() if rank is not None} == {
        *ALWAYS, "person_leaves",
    }
    assert ranks["person_leaves"] is EventPriority.CATEGORY
    # ``light_unoccupied``: every edge of a light, not only activations.
    ranks = _classify_all(monkeypatch, "light_unoccupied", **ROUTINE_ON)
    assert ranks["light_off"] is EventPriority.CATEGORY
    assert ranks["switch_on"] is None


def test_category_option_change_is_picked_up_without_restart(monkeypatch):
    harness = _Harness(monkeypatch, [], categories="safety")
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    assert classify(*SAMPLES["door_open"]) is None
    harness.entry.options[CONF_AGENT_EVENT_CATEGORIES] = "opening_while_away"
    assert classify(*SAMPLES["door_open"]) is EventPriority.CATEGORY
    harness.entry.options[CONF_AGENT_EVENT_CATEGORIES] = ""
    assert classify(*SAMPLES["door_open"]) is None


# --------------------------------------------------------------------------
# 9.7 V12 relevance
# --------------------------------------------------------------------------

class _Proactive:
    """An enabled V12 context; only ``RELEVANT`` matters to its detectors."""

    RELEVANT = "binary_sensor.garagentor"
    enabled = True

    def __init__(self) -> None:
        self.observed: list[str] = []

    def is_relevant_event(self, entity_id: str, _device_class: str | None) -> bool:
        return entity_id == self.RELEVANT

    async def async_observe_state(self, entity: Any, *_args: Any) -> None:
        self.observed.append(entity.entity_id)


def _irrelevant_load() -> list[str]:
    third = LOAD // 3
    return [
        *_switches(third, "sensor.last"),
        *_switches(third, "binary_sensor.last"),
        *_switches(LOAD - 2 * third),
    ]


def test_irrelevant_v12_events_queue_nothing_and_start_no_worker(monkeypatch):
    proactive = _Proactive()
    load = _irrelevant_load()
    harness = _Harness(monkeypatch, [*load, proactive.RELEVANT, SMOKE],
                       proactive_context=proactive)
    for entity_id in load:
        harness.house.seed(entity_id, "20.0" if entity_id.startswith("sensor.") else "off")
    harness.house.seed(proactive.RELEVANT, "off")
    harness.house.seed(SMOKE, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for entity_id in load:
            harness.house.set(entity_id, "21.0" if entity_id.startswith("sensor.") else "on")
        metrics = harness.metrics
        assert metrics.received == LOAD
        assert metrics.filtered_no_interest == LOAD
        assert metrics.filtered_by_category == 0  # no category configured
        assert metrics.queued == 0
        assert metrics.worker_starts == 0
        assert harness.pending_tasks() == []
        await _settle(harness.hass)
        assert metrics.processed == 0
        assert metrics.snapshot_builds == 0
        harness.house.set(proactive.RELEVANT, "on")
        assert metrics.queued == 1
        await _settle(harness.hass)
        assert metrics.processed == 1
        # A safety device stays relevant whatever V12 says.
        harness.house.set(SMOKE, "on")
        await _settle(harness.hass)
        assert metrics.processed == 2
        stop()

    asyncio.run(scenario())
    assert proactive.observed == [proactive.RELEVANT, SMOKE]
    assert harness.builds == [1, 1]


def test_irrelevant_v12_event_still_queued_for_another_consumer(monkeypatch):
    proactive = _Proactive()
    effects = EffectMonitor()
    harness = _Harness(monkeypatch, [SWITCH, DOOR], categories="opening_while_away",
                       proactive_context=proactive, effect_monitor=effects)

    async def scenario() -> None:
        harness.runtime.async_start()
        classify = harness.runtime.interest.classify
        assert classify(*SAMPLES["switch_on"]) is None
        assert classify(*SAMPLES["door_open"]) is EventPriority.CATEGORY
        effects.register(ServiceCallPlan("switch", "turn_on", SWITCH, {}))
        assert classify(*SAMPLES["switch_on"]) is EventPriority.PROTECTED
        await effects.async_close()

    asyncio.run(scenario())


def test_v12_habit_triggers_and_detector_gate_stay_relevant(monkeypatch, tmp_path):
    harness = _Harness(monkeypatch, [SWITCH, "switch.radio", DOOR, PERSON],
                       options={CONF_PROACTIVE_CONTEXT_ENABLED: True})
    proactive = ProactiveRuntime(
        harness.hass, harness.entry, harness.data, str(tmp_path / "proactive.json")
    )
    harness.data.proactive_context = proactive
    assert proactive.enabled is True
    harness.runtime.async_start()
    classify = harness.runtime.interest.classify
    radio = (_st("switch.radio", "off"), _st("switch.radio", "on"))
    assert classify(*SAMPLES["switch_on"]) is None
    assert classify("switch.radio", *radio) is None
    # The detector's gate: persons, lights, entry sensors.
    assert classify(*SAMPLES["person_leaves"]) is EventPriority.ROUTINE
    assert classify(*SAMPLES["door_open"]) is EventPriority.ROUTINE
    # A habit trigger is relevant through ``is_relevant_event`` as well.
    proactive._habit_triggers = frozenset({SWITCH})
    assert classify(*SAMPLES["switch_on"]) is EventPriority.ROUTINE
    assert classify("switch.radio", *radio) is None


# --------------------------------------------------------------------------
# 9.8 ``expected_effect_missing`` without an effect
# --------------------------------------------------------------------------

def test_expected_effect_category_needs_only_pending_effects(monkeypatch):
    effects = EffectMonitor()
    lights = _lights(LOAD)
    harness = _Harness(monkeypatch, [*lights, LIGHT], categories="expected_effect_missing",
                       effect_monitor=effects)
    for entity_id in [*lights, LIGHT]:
        harness.house.seed(entity_id, "off")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        for light in lights:
            harness.house.set(light, "on")
        metrics = harness.metrics
        assert metrics.filtered_no_interest == LOAD
        assert metrics.filtered_by_category == LOAD
        assert metrics.queued == 0
        assert metrics.worker_starts == 0
        await _settle(harness.hass)
        assert metrics.snapshot_builds == 0
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(lights[0], "off")
        harness.house.set(LIGHT, "on")
        assert metrics.queued == 1
        await _settle(harness.hass)
        stop()
        await effects.async_close()

    asyncio.run(scenario())
    assert [entity for entity, *_ in harness.evaluated] == [LIGHT]
    assert effects.pending == ()


# --------------------------------------------------------------------------
# 9.9 / 9.10 retained memory after a drained burst
# --------------------------------------------------------------------------

def _broad(monkeypatch: Any, entity_ids: list[str], **fields: Any) -> _Harness:
    return _Harness(monkeypatch, entity_ids, categories="routine_anomaly",
                    options=dict(ROUTINE_ON), **fields)


def test_memory_after_a_6000_event_storm_is_released_without_unload(monkeypatch):
    switches = _switches(LOAD)
    harness = _broad(monkeypatch, switches)
    for switch in switches:
        harness.house.seed(switch, "off")

    async def scenario() -> None:
        harness.runtime.async_start()  # never stopped in this test
        for switch in switches:
            harness.house.set(switch, "on")
        before = _structures(harness.runtime)
        assert before["live"] == FULL
        assert before["latest"] == FULL
        assert before["gaps"] == LOAD - FULL
        await _settle(harness.hass)
        assert harness.pending_tasks() == []

    asyncio.run(scenario())
    assert _structures(harness.runtime) == EMPTY
    assert _retained(harness.runtime) == RETAINED_EMPTY
    metrics = harness.metrics
    assert metrics.processed == FULL
    assert metrics.dropped_routine == LOAD - FULL
    assert metrics.cleanup_runs == 1
    assert metrics.max_retained_entries >= FULL + FULL + (LOAD - FULL)


def test_memory_after_coalescible_numbers_releases_carried_states(monkeypatch):
    # 7.9.6: 5000 distinct sensors left ``latest`` 4096, ``gaps`` 904 and
    # ``carry_previous`` 904 behind after the drain.
    sensors = _switches(5000, "sensor.zahl")
    harness = _broad(monkeypatch, sensors)
    for sensor in sensors:
        harness.house.seed(sensor, "20.0")

    async def scenario() -> None:
        harness.runtime.async_start()
        for sensor in sensors:
            harness.house.set(sensor, "21.0")
        before = _structures(harness.runtime)
        assert before["carry_previous"] == 5000 - FULL
        assert before["gaps"] == 5000 - FULL
        await _settle(harness.hass)

        assert _structures(harness.runtime) == EMPTY
        assert harness.metrics.dropped_coalescible == 5000 - FULL
        # Nothing left over influences the next change of a dropped sensor.
        harness.house.set(sensors[-1], "22.0")
        await _settle(harness.hass)

    asyncio.run(scenario())
    assert harness.evaluations_of(sensors[-1]) == [("22.0", "21.0")]


def test_memory_stays_flat_over_ten_bursts(monkeypatch):
    switches = _switches(LOAD)
    harness = _broad(monkeypatch, switches)
    for switch in switches:
        harness.house.seed(switch, "off")
    # A lighter recorder than the harness's (which copies the whole view for
    # every event): the real evaluation, plus the first switch's moments.
    first: list[tuple[str, str | None, str]] = []
    evaluate = event_runtime.SituationRuntime._async_process_state_changed.__get__(
        harness.runtime
    )

    async def _record(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
        data = raw_event.data
        if data["entity_id"] == switches[0]:
            first.append((
                data["new_state"].state, getattr(data["old_state"], "state", None),
                by_id[switches[1]].state,
            ))
        await evaluate(raw_event, entities, by_id, **kwargs)

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", _record)
    processed: list[int] = []
    cleanups: list[int] = []
    workers: list[int] = []

    async def scenario() -> None:
        harness.runtime.async_start()
        for burst in range(10):
            value = "on" if burst % 2 == 0 else "off"
            started = harness.metrics.worker_starts
            for switch in switches:
                harness.house.set(switch, value)
            workers.append(harness.metrics.worker_starts - started)
            await _settle(harness.hass)
            assert harness.pending_tasks() == []
            assert _structures(harness.runtime) == EMPTY, burst
            assert _retained(harness.runtime) == RETAINED_EMPTY, burst
            processed.append(harness.metrics.processed)
            cleanups.append(harness.metrics.cleanup_runs)

    asyncio.run(scenario())
    assert workers == [1] * 10
    assert processed == [FULL * (burst + 1) for burst in range(10)]
    assert cleanups == list(range(1, 11))
    # No earlier burst leaks into a later one: every evaluation of the first
    # switch reports its own previous state, and the second switch as it was
    # at that moment (not yet switched in this burst).
    assert first == [
        ("on", "off", "off") if burst % 2 == 0 else ("off", "on", "on")
        for burst in range(10)
    ]


def test_release_never_deletes_an_event_that_arrives_during_the_drain(monkeypatch):
    harness = _broad(monkeypatch, [DOOR, WINDOW])
    harness.house.seed(DOOR, "off")
    harness.house.seed(WINDOW, "off")
    process = harness.runtime._async_process_state_changed
    fired = False

    async def _during(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
        nonlocal fired
        await process(raw_event, entities, by_id, **kwargs)
        if not fired:
            fired = True
            harness.house.set(WINDOW, "on")  # arrives while the worker runs

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", _during)

    async def scenario() -> None:
        harness.runtime.async_start()
        harness.house.set(DOOR, "on")
        await _settle(harness.hass)

    asyncio.run(scenario())
    assert [(entity, state) for entity, state, *_ in harness.evaluated] == [
        (DOOR, "on"), (WINDOW, "on"),
    ]
    assert harness.metrics.worker_starts == 1
    assert harness.metrics.cleanup_runs == 1
    assert _structures(harness.runtime) == EMPTY


# --------------------------------------------------------------------------
# 9.11 hard bookkeeping bound
# --------------------------------------------------------------------------

def test_bookkeeping_stays_bounded_for_50000_distinct_changes(monkeypatch, caplog):
    count = 50_000
    effects = EffectMonitor()
    switches = _switches(count)
    early = "light.kueche"
    harness = _broad(monkeypatch, [early, *switches, LIGHT, SMOKE], effect_monitor=effects)
    for entity_id in [early, *switches, LIGHT]:
        harness.house.seed(entity_id, "off")
    harness.house.seed(SMOKE, "off")
    degraded: list[bool] = []
    process = harness.runtime._async_process_state_changed

    async def _flag(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
        degraded.append(bool(kwargs.get("degraded")))
        await process(raw_event, entities, by_id, **kwargs)

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", _flag)
    peak: dict[str, int] = {}

    async def scenario() -> None:
        harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", [early, LIGHT], {}))
        with caplog.at_level(logging.WARNING, logger=event_runtime.__name__):
            harness.house.set(early, "on")  # protected, queued first
            for index, switch in enumerate(switches):
                harness.house.set(switch, "on")
                if index % 5000 == 4999:
                    for key, value in _structures(harness.runtime).items():
                        peak[key] = max(peak.get(key, 0), value)
            harness.house.set(LIGHT, "on")  # protected, at the end
            harness.house.set(SMOKE, "on")  # critical, at the end
            for key, value in _structures(harness.runtime).items():
                peak[key] = max(peak.get(key, 0), value)
            await _settle(harness.hass)
        await effects.async_close()

    asyncio.run(scenario())
    metrics = harness.metrics
    assert peak["live"] == event_runtime.MAX_PENDING_EVENTS
    assert peak["queue"] <= event_runtime.MAX_PENDING_EVENTS + event_runtime.MAX_TOMBSTONES
    assert peak["gaps"] <= event_runtime.MAX_GAPS
    assert peak["carry_previous"] <= event_runtime.MAX_CARRIED
    assert peak["latest"] <= event_runtime.MAX_PENDING_EVENTS + event_runtime.MAX_BATCH_EVENTS
    assert metrics.max_retained_entries <= event_runtime.MAX_RETAINED_ENTRIES
    # Degraded history is explicit: counted, warned about once.
    assert metrics.history_degraded > 0
    assert metrics.history_degraded_batches > 0
    warnings = [record for record in caplog.records
                if "gave up the exact house history" in record.getMessage()]
    assert len(warnings) == 1
    # Protected and critical events at both ends are evaluated.
    assert harness.evaluations_of(early) == [("on", "off")]
    assert harness.evaluations_of(LIGHT) == [("on", "off")]
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert effects.pending == ()
    assert metrics.dropped_protected == 0
    assert metrics.dropped_critical == 0
    # The early event sees no future state: every switch it sees is "off"
    # (rewound from the queue or a gap); the rest is left out.
    first = harness.evaluated[0]
    assert first[0] == early
    assert all(first[4][switch] == "off" for switch in switches if switch in first[4])
    # Given up without a gap: left out. Queued only after such a change: its
    # earlier state is not known exactly either - left out until it changes.
    assert sum(1 for switch in switches if switch in first[4]) < count
    assert LIGHT not in first[4] and SMOKE not in first[4]
    assert degraded[0] is True
    # The events after the last given-up change see the full house again.
    last = harness.evaluated[-1]
    assert last[0] == SMOKE and degraded[-1] is False
    assert last[4][switches[-1]] == "on"
    assert _structures(harness.runtime) == EMPTY


def test_bounded_carry_for_50000_distinct_numbers(monkeypatch):
    count = 50_000
    sensors = _switches(count, "sensor.zahl")
    harness = _broad(monkeypatch, sensors)
    for sensor in sensors:
        harness.house.seed(sensor, "1.0")
    peak = {"carry_previous": 0, "gaps": 0}

    async def scenario() -> None:
        harness.runtime.async_start()
        for sensor in sensors:
            harness.house.set(sensor, "2.0")
        sizes = _structures(harness.runtime)
        peak.update(carry_previous=sizes["carry_previous"], gaps=sizes["gaps"])
        await _settle(harness.hass)

    asyncio.run(scenario())
    assert peak["carry_previous"] == event_runtime.MAX_CARRIED
    assert peak["gaps"] == event_runtime.MAX_GAPS
    assert harness.metrics.dropped_coalescible == count - FULL
    assert harness.metrics.max_retained_entries <= event_runtime.MAX_RETAINED_ENTRIES
    assert _structures(harness.runtime) == EMPTY


def test_degraded_history_never_rewinds_to_a_given_up_later_state(monkeypatch):
    # The kitchen light's change at seq 6 is given up without a gap; its
    # next change (protected, seq 7) carries it as ``old_state``. For the
    # hall light's event (seq 1) that state is the future: the kitchen light
    # is left out until its own change instead of being rewound to it.
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
    monkeypatch.setattr(event_runtime, "MAX_GAPS", 1)
    effects = EffectMonitor()
    kitchen = "light.kueche"
    load = _switches(4)
    harness = _broad(monkeypatch, [LIGHT, kitchen, *load], effect_monitor=effects)
    harness.house.seed(LIGHT, "off")
    harness.house.seed(kitchen, "off")
    for switch in load:
        harness.house.seed(switch, "off")

    async def scenario() -> None:
        harness.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        harness.house.set(LIGHT, "on")  # seq 1, protected
        for switch in load[:3]:
            harness.house.set(switch, "on")  # seq 2-4: the queue is full
        harness.house.set(load[3], "on")  # seq 5: rejected, the one gap
        harness.house.set(kitchen, "on")  # seq 6: rejected, no gap left
        assert harness.metrics.history_degraded == 1
        effects.register(ServiceCallPlan("light", "turn_off", kitchen, {}))
        harness.house.set(kitchen, "off")  # seq 7: protected, old_state "on"
        await _settle(harness.hass)
        await effects.async_close()

    asyncio.run(scenario())
    first = harness.evaluated[0]
    assert first[:2] == (LIGHT, "on")
    assert first[4].get(kitchen) != "on"  # never the given-up later state
    assert kitchen not in first[4]
    assert first[4][load[3]] == "off"  # rewound by its gap
    assert harness.evaluations_of(kitchen) == [("off", "on")]
    kitchen_view = next(view for entity, _s, _p, _t, view in harness.evaluated
                        if entity == kitchen)
    assert kitchen_view[kitchen] == "off"  # its own change: exact again
    assert harness.metrics.dropped_protected == 0
    assert _structures(harness.runtime) == EMPTY


def test_degraded_history_reports_presence_unknown_not_nobody_home(monkeypatch):
    # Missing persons in a degraded view must not read as "nobody home": an
    # opening at night would otherwise raise ``opening_while_away``.
    monkeypatch.setattr(event_runtime, "MAX_PENDING_EVENTS", 4)
    monkeypatch.setattr(event_runtime, "MAX_GAPS", 2)
    agent = _agent()
    switches = _switches(8)
    harness = _Harness(monkeypatch, [DOOR, PERSON, *switches],
                       categories="opening_while_away,routine_anomaly",
                       options=dict(ROUTINE_ON), proactive_agent=agent)
    harness.house.seed(DOOR, "off")
    harness.house.seed(PERSON, "home")
    for switch in switches:
        harness.house.seed(switch, "off")
    night = T0.replace(hour=23)
    monkeypatch.setattr(harness.house, "offset", 0)

    async def scenario() -> None:
        harness.runtime.async_start()
        old = harness.hass.states.get(DOOR)
        new = State(DOOR, "on", {"device_class": "door"})
        harness.hass.states._states[DOOR] = new
        harness.hass.bus.fire(SimpleNamespace(
            data={"entity_id": DOOR, "old_state": old, "new_state": new},
            time_fired=night, context=SimpleNamespace(id="ctx-door"),
        ))
        for switch in switches:
            harness.house.set(switch, "on")
        await _settle(harness.hass)

    asyncio.run(scenario())
    assert harness.metrics.history_degraded > 0
    door = harness.evaluated[0]
    assert door[:2] == (DOOR, "on")
    assert PERSON not in door[4]  # left out, not shown from the future
    assert not [p for p in _signals(agent) if p["rule_id"] == "situation:opening_while_away"]


# --------------------------------------------------------------------------
# 9.12 unload under overload
# --------------------------------------------------------------------------

def test_unload_during_overload_releases_everything_and_reload_is_clean(monkeypatch):
    monkeypatch.setattr(event_runtime, "SNAPSHOT_RETRY_BASE_SECONDS", 0.01)
    switches = _switches(LOAD)
    harness = _broad(monkeypatch, [*switches, SMOKE, MOISTURE])
    for switch in switches:
        harness.house.seed(switch, "off")
    harness.house.seed(SMOKE, "off")
    harness.house.seed(MOISTURE, "off")
    build = harness.build
    failing = True

    def _flaky(*args: Any) -> list[EntitySnapshot]:
        if failing:
            raise RuntimeError("Registry nicht bereit")
        return build(*args)

    monkeypatch.setattr(event_runtime, "build_entity_snapshots", _flaky)

    async def scenario() -> None:
        nonlocal failing
        stop = harness.runtime.async_start()
        old_gen = harness.runtime._gen
        for switch in switches:
            harness.house.set(switch, "on")
        sizes = _structures(harness.runtime)
        assert sizes["live"] == FULL and sizes["gaps"] > 0 and sizes["latest"] == FULL
        for _ in range(5):
            await asyncio.sleep(0.02)  # the worker is in its retry backoff
        assert harness.metrics.snapshot_failures >= 1
        assert len(harness.pending_tasks()) == 1
        old_metrics = harness.metrics
        stop()
        assert _structures(harness.runtime) == EMPTY  # released at unload
        await _settle(harness.hass)
        assert harness.pending_tasks() == []
        assert old_metrics.processed == 0  # nothing after the unload
        failing = False
        restarted = harness.runtime.async_start()
        assert harness.runtime._gen is not old_gen
        assert harness.metrics.received == 0
        harness.house.set(SMOKE, "on")
        harness.house.set(MOISTURE, "on")
        assert len(harness.pending_tasks()) == 1
        await _settle(harness.hass)
        assert harness.metrics.worker_starts == 1
        assert harness.metrics.processed == 2
        assert _structures(harness.runtime) == EMPTY
        # The old generation keeps nothing either.
        assert old_gen.live == 0 and not old_gen.queue and not old_gen.gaps
        assert not old_gen.latest and not old_gen.carry_previous
        restarted()

    asyncio.run(scenario())
    assert harness.evaluations_of(SMOKE) == [("on", "off")]
    assert harness.pending_tasks() == []
