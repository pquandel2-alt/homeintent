"""7.9.9 transactional batches, supervised recovery and long-running timers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from test_event_runtime_hardening_796 import (
    DOOR,
    LIGHT,
    SMOKE,
    T0,
    _Harness,
    _agent,
    _settle,
)

from homeassistant.core import State
from homeintent import event_runtime
from homeintent.const import (
    CONF_AGENT_LONG_RUNNING_MINUTES,
    CONF_ROUTINE_DETECTION_ENABLED,
)
from homeintent.event_priority import EventPriority
from homeintent.long_running import LONG_RUNNING_DOMAINS, activity_marker


def _routine(monkeypatch, entity_ids):
    return _Harness(
        monkeypatch,
        list(entity_ids),
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
    )


def test_leased_event_is_requeued_after_one_view_failure(monkeypatch):
    harness = _routine(monkeypatch, ["switch.lease"])
    harness.house.seed("switch.lease", "off")
    original = event_runtime._HouseView.for_batch.__func__
    calls = 0

    def fail_once(cls, entities, batch, gen, *, with_queue=True):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("one view failure")
        return original(cls, entities, batch, gen, with_queue=with_queue)

    monkeypatch.setattr(event_runtime._HouseView, "for_batch", classmethod(fail_once))

    async def scenario():
        stop = harness.runtime.async_start()
        harness.house.set("switch.lease", "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert harness.evaluations_of("switch.lease") == [("on", "off")]
    assert calls == 2
    assert harness.metrics.batch_failures == 1
    assert harness.metrics.batch_requeued_events == 1
    assert harness.metrics.worker_starts == 2
    assert harness.metrics.worker_restarts == 1
    assert harness.metrics.processed == 1
    assert harness.metrics.retained_total == 0
    assert harness.metrics.retained_leased == 0


def test_view_retry_keeps_fifo_and_priorities(monkeypatch):
    effects = SimpleNamespace(
        watched_entity_ids=frozenset({LIGHT}),
        add_watch_listener=lambda _listener: (lambda: None),
        set_expired_handler=lambda _handler: None,
        set_pending_probe=lambda _probe: None,
        observe=lambda *_args: None,
    )
    harness = _Harness(
        monkeypatch,
        [DOOR, LIGHT, SMOKE],
        categories="opening_while_away",
        effect_monitor=effects,
    )
    for entity_id in (DOOR, LIGHT, SMOKE):
        harness.house.seed(entity_id, "off")
    original = event_runtime._HouseView.for_batch.__func__
    failed = False

    def fail_once(cls, entities, batch, gen, *, with_queue=True):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("view setup")
        return original(cls, entities, batch, gen, with_queue=with_queue)

    monkeypatch.setattr(event_runtime._HouseView, "for_batch", classmethod(fail_once))

    async def scenario():
        stop = harness.runtime.async_start()
        harness.house.set(DOOR, "on")
        harness.house.set(LIGHT, "on")
        harness.house.set(SMOKE, "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [entity for entity, *_ in harness.evaluated] == [DOOR, LIGHT, SMOKE]
    classify = harness.runtime.interest.classify
    assert classify(
        DOOR,
        State(DOOR, "off", {"device_class": "door"}),
        State(DOOR, "on", {"device_class": "door"}),
    ) is EventPriority.CATEGORY
    assert classify(LIGHT, State(LIGHT, "off"), State(LIGHT, "on")) is EventPriority.PROTECTED
    assert classify(
        SMOKE,
        State(SMOKE, "off", {"device_class": "smoke"}),
        State(SMOKE, "on", {"device_class": "smoke"}),
    ) is EventPriority.CRITICAL


def test_permanent_worker_failure_is_bounded_and_recovers(monkeypatch):
    harness = _routine(monkeypatch, ["switch.supervised"])
    harness.house.seed("switch.supervised", "off")
    original = harness.runtime._async_drain
    releases = asyncio.Event()
    delays: list[tuple[float, bool]] = []
    calls = 0

    async def fail(_gen):
        nonlocal calls
        calls += 1
        raise RuntimeError("same permanent failure")

    async def controlled_backoff(_gen, delay, *, wakeable):
        delays.append((delay, wakeable))
        await releases.wait()
        releases.clear()

    monkeypatch.setattr(harness.runtime, "_async_drain", fail)
    monkeypatch.setattr(harness.runtime, "_async_worker_backoff", controlled_backoff)

    async def wait_for_calls(expected):
        for _ in range(100):
            if calls >= expected:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"worker made only {calls} calls")

    async def scenario():
        stop = harness.runtime.async_start()
        harness.house.set("switch.supervised", "on")
        await wait_for_calls(1)
        for expected in range(2, event_runtime.WORKER_CIRCUIT_FAILURES + 1):
            releases.set()
            await wait_for_calls(expected)
        assert harness.metrics.worker_circuit_breaks == 1
        assert harness.metrics.worker_suspended == 1
        assert len(harness.hass._tasks) == 1
        monkeypatch.setattr(harness.runtime, "_async_drain", original)
        releases.set()
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert calls == event_runtime.WORKER_CIRCUIT_FAILURES
    assert harness.metrics.worker_starts == event_runtime.WORKER_CIRCUIT_FAILURES + 1
    assert harness.metrics.worker_restarts == event_runtime.WORKER_CIRCUIT_FAILURES
    assert harness.metrics.worker_backoffs == event_runtime.WORKER_CIRCUIT_FAILURES
    assert harness.metrics.worker_recoveries == 1
    assert harness.metrics.processed == 1
    assert harness.metrics.retained_total == 0
    assert delays[-1][1] is True


def test_failure_after_partial_batch_requeues_only_the_remainder(monkeypatch):
    ids = ["switch.partial_1", "switch.partial_2", "switch.partial_3"]
    harness = _routine(monkeypatch, ids)
    for entity_id in ids:
        harness.house.seed(entity_id, "off")
    original = event_runtime._HouseView.advance
    calls = 0
    failed = False

    def fail_second(view, entry):
        nonlocal calls, failed
        calls += 1
        if calls == 2 and not failed:
            failed = True
            raise RuntimeError("advance failed after one commit")
        return original(view, entry)

    monkeypatch.setattr(event_runtime._HouseView, "advance", fail_second)

    async def scenario():
        stop = harness.runtime.async_start()
        for entity_id in ids:
            harness.house.set(entity_id, "on")
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert [entity for entity, *_ in harness.evaluated] == ids
    assert harness.metrics.batch_requeued_events == 2
    assert harness.metrics.processed == 3
    assert harness.metrics.retained_total == 0


def test_unload_discards_leased_work_and_new_generation_stays_isolated(monkeypatch):
    harness = _routine(monkeypatch, ["switch.old", "switch.new"])
    harness.house.seed("switch.old", "off")
    harness.house.seed("switch.new", "off")
    original = harness.runtime._async_process_state_changed
    entered = asyncio.Event()
    blocker = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await blocker.wait()
        await original(*args, **kwargs)

    monkeypatch.setattr(harness.runtime, "_async_process_state_changed", blocked)

    async def scenario():
        stop_old = harness.runtime.async_start()
        old_gen = harness.runtime._gen
        harness.house.set("switch.old", "on")
        await entered.wait()
        assert len(old_gen.leased) == 1
        stop_old()
        await asyncio.sleep(0)
        assert old_gen.live == 0
        assert not old_gen.leased
        assert old_gen.metrics.retained_total == 0

        monkeypatch.setattr(harness.runtime, "_async_process_state_changed", original)
        stop_new = harness.runtime.async_start()
        harness.house.set("switch.new", "on")
        await _settle(harness.hass)
        stop_new()

    asyncio.run(scenario())
    assert harness.evaluations_of("switch.old") == []
    assert harness.evaluations_of("switch.new") == [("on", "off")]


def test_disabled_long_running_category_filters_one_hundred_events(monkeypatch):
    ids = [f"switch.disabled_{index}" for index in range(100)]
    harness = _Harness(
        monkeypatch,
        ids,
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 0},
    )
    for entity_id in ids:
        harness.house.seed(entity_id, "off")

    async def scenario():
        stop = harness.runtime.async_start()
        for entity_id in ids:
            harness.house.set(entity_id, "on")
        await asyncio.sleep(0)
        stop()

    asyncio.run(scenario())
    assert harness.metrics.queued == 0
    assert harness.metrics.worker_starts == 0
    assert harness.metrics.filtered_no_interest == 100


def test_long_running_interest_cache_tracks_in_place_threshold_updates(monkeypatch):
    harness = _Harness(
        monkeypatch,
        ["switch.cache"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 0},
    )
    old = State("switch.cache", "off")
    new = State("switch.cache", "on")

    assert harness.runtime.interest.classify("switch.cache", old, new) is None
    harness.entry.options[CONF_AGENT_LONG_RUNNING_MINUTES] = 1
    assert (
        harness.runtime.interest.classify("switch.cache", old, new)
        is EventPriority.CATEGORY
    )
    harness.entry.options[CONF_AGENT_LONG_RUNNING_MINUTES] = "invalid"
    assert harness.runtime.interest.classify("switch.cache", old, new) is None


@pytest.mark.parametrize(
    ("entity_id", "state", "attributes", "expected"),
    [
        ("climate.room", "heat", {"hvac_action": "heating"}, "hvac_action:heating"),
        ("cover.blind", "opening", {}, "state:opening"),
        ("fan.room", "on", {}, "state:on"),
        ("humidifier.room", "on", {}, "state:on"),
        ("light.room", "on", {}, "state:on"),
        ("media_player.room", "playing", {}, "state:playing"),
        ("switch.pump", "on", {}, "state:on"),
        ("vacuum.robot", "cleaning", {}, "state:cleaning"),
        ("vacuum.robot", "returning", {}, "state:returning"),
    ],
)
def test_domain_activity_predicates(entity_id, state, attributes, expected):
    assert activity_marker(State(entity_id, state, attributes)) == expected
    assert entity_id.partition(".")[0] in LONG_RUNNING_DOMAINS


@pytest.mark.parametrize(
    ("entity_id", "state", "attributes"),
    [
        ("climate.room", "heat", {}),
        ("climate.room", "heat", {"hvac_action": "idle"}),
        ("cover.blind", "open", {}),
        ("media_player.room", "paused", {}),
        ("vacuum.robot", "docked", {}),
        ("water_heater.boiler", "heat_pump", {"current_operation": "heat_pump"}),
        ("switch.pump", "unknown", {}),
        ("light.room", "unavailable", {}),
    ],
)
def test_domain_activity_predicates_reject_modes_and_inactive_states(
    entity_id, state, attributes
):
    assert activity_marker(State(entity_id, state, attributes)) is None


def test_climate_hvac_action_attribute_change_starts_real_activity(monkeypatch):
    clock = [T0]
    monkeypatch.setattr(event_runtime.dt_util, "utcnow", lambda: clock[0])
    agent = _agent()
    harness = _Harness(
        monkeypatch,
        ["climate.room"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 60},
        proactive_agent=agent,
    )
    harness.house.seed("climate.room", "heat", hvac_action="idle")

    async def scenario():
        stop = harness.runtime.async_start()
        harness.house.set("climate.room", "heat", hvac_action="heating")
        await _settle(harness.hass)
        assert agent.async_signal.await_count == 0
        harness.house.offset = 61 * 60 * 1000
        clock[0] = T0 + timedelta(minutes=61)
        harness.house.set(
            "climate.room", "heat", hvac_action="heating", temperature=22
        )
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert agent.async_signal.await_count == 1


class _Timer:
    def __init__(self, callback, args):
        self.callback = callback
        self.args = args
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        assert not self.cancelled
        self.callback(*self.args)


def test_long_running_scheduler_has_a_hard_capacity(monkeypatch):
    monkeypatch.setattr(event_runtime, "MAX_LONG_RUNNING_TIMERS", 2)
    timers: list[_Timer] = []

    def schedule(_delay, callback, *args):
        timer = _Timer(callback, args)
        timers.append(timer)
        return timer

    monkeypatch.setattr(event_runtime, "_schedule_later", schedule)
    monkeypatch.setattr(event_runtime.dt_util, "utcnow", lambda: T0)
    ids = ["switch.capacity_1", "switch.capacity_2", "switch.capacity_3"]
    harness = _Harness(
        monkeypatch,
        ids,
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 1},
    )
    for entity_id in ids:
        harness.house.seed(entity_id, "on")
        harness.hass.states.get(entity_id).last_changed = T0

    async def scenario():
        stop = harness.runtime.async_start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(harness.runtime._long_running) == 2
        assert len(harness.runtime._long_running_heap) <= 2
        assert harness.metrics.long_running_capacity_rejected == 1
        stop()

    asyncio.run(scenario())


def test_long_running_threshold_fires_without_another_state_change(monkeypatch):
    now = [T0]
    timers: list[_Timer] = []

    def schedule(_delay, callback, *args):
        timer = _Timer(callback, args)
        timers.append(timer)
        return timer

    monkeypatch.setattr(event_runtime, "_schedule_later", schedule)
    monkeypatch.setattr(event_runtime.dt_util, "utcnow", lambda: now[0])
    agent = _agent()
    harness = _Harness(
        monkeypatch,
        ["switch.timer"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 1},
        proactive_agent=agent,
    )
    harness.house.seed("switch.timer", "on")
    state = harness.hass.states.get("switch.timer")
    state.last_changed = T0

    async def scenario():
        stop = harness.runtime.async_start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        active = next(timer for timer in reversed(timers) if not timer.cancelled)
        now[0] = T0 + timedelta(minutes=1)
        active.fire()
        await _settle(harness.hass)
        stop()

    asyncio.run(scenario())
    assert agent.async_signal.await_count == 1
    assert harness.metrics.long_running_due == 1
    assert harness.metrics.long_running_enqueued == 1
    assert harness.metrics.retained_long_running == 0
    assert harness.metrics.retained_long_running_heap == 0


def test_long_running_cancel_reschedule_restart_and_unload(monkeypatch):
    now = [T0]
    timers: list[_Timer] = []

    def schedule(_delay, callback, *args):
        timer = _Timer(callback, args)
        timers.append(timer)
        return timer

    monkeypatch.setattr(event_runtime, "_schedule_later", schedule)
    monkeypatch.setattr(event_runtime.dt_util, "utcnow", lambda: now[0])
    harness = _Harness(
        monkeypatch,
        ["switch.lifecycle"],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 1},
    )
    harness.house.seed("switch.lifecycle", "off")

    async def scenario():
        stop = harness.runtime.async_start()
        await asyncio.sleep(0)
        harness.house.set("switch.lifecycle", "on")
        await _settle(harness.hass)
        first = harness.runtime._long_running["switch.lifecycle"]
        harness.entry.options[CONF_AGENT_LONG_RUNNING_MINUTES] = 2
        harness.house.set("switch.lifecycle", "on", detail="attribute")
        await _settle(harness.hass)
        second = harness.runtime._long_running["switch.lifecycle"]
        assert second.started_at == first.started_at
        assert second.due_at - second.started_at == timedelta(minutes=2)
        harness.house.set("switch.lifecycle", "off")
        await _settle(harness.hass)
        assert "switch.lifecycle" not in harness.runtime._long_running
        stop()
        assert all(timer.cancelled for timer in timers)

        state = harness.hass.states.get("switch.lifecycle")
        state.state = "on"
        state.last_changed = T0
        restart = harness.runtime.async_start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "switch.lifecycle" in harness.runtime._long_running
        restart()
        assert not harness.runtime._long_running

    asyncio.run(scenario())
    assert harness.metrics.retained_long_running == 0
    assert harness.metrics.retained_long_running_heap == 0
