#!/usr/bin/env python3
"""Reproduce the EventRuntime findings of the 7.9.7 review.

Runs against the stub Home Assistant and only uses interfaces 7.9.6 already
had (metrics missing there are reported as ``None``), so the same script
shows the defects on 7.9.6 and the fix on 7.9.7. Every burst is
synchronous - no ``await`` and no loop turn between the events - so the
worker cannot run before the whole burst is queued.

Cases (structured metrics per case):

``lossless_effect_after_switch_flood``
    Category ``safety``, 4096 switches ``off -> on``, then an expected effect
    on ``light.flur`` and ``light.flur: off -> on``. Variant
    ``category_flood``: the 4096 events are real category events (lights
    under ``light_unoccupied``) so the queue really is full.
``effect_before_critical``
    The expected-effect event is queued first, ordinary category events
    fill the queue, then a smoke detector fires.
``monitor_person_after_flood``
    A monitor goal on ``person.anna``; a full queue of category events, then
    ``home -> not_home -> home``.
``category_safety_filter``
    Category ``safety``, 6000 ordinary switch and sensor changes.
``expected_effect_category_without_effect``
    Category ``expected_effect_missing`` without an active effect.
``proactive_irrelevant_filter``
    Enabled V12 context whose ``is_relevant_event`` says ``False``.
``memory_after_6000``
    6000 distinct entities, drained, then the retained structures.
``memory_after_10_bursts``
    Ten bursts of 6000, the retained structures after each drain.
``bounded_bookkeeping_50000``
    50,000 distinct low-priority changes while the worker cannot run, then
    an expected effect and a smoke detector.

    python scripts/event_runtime_repro_797.py [--json]
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

import _ha_stub  # noqa: E402

_ha_stub.install()

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, State  # noqa: E402
from homeintent import event_runtime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_ROUTINE_DETECTION_ENABLED,
    CONF_SELECTED_ENTITIES,
)
from homeintent.effect_monitor import EffectMonitor  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.goal_model import (  # noqa: E402
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalTrigger,
)
from homeintent.monitor_goal import (  # noqa: E402
    MonitorGoalRuntime,
    MonitorGoalStore,
    MonitorRecord,
)
from homeintent.runtime_data import HomeIntentRuntimeData  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402

T0 = datetime(2026, 10, 9, 6, tzinfo=timezone.utc)
SMOKE = "binary_sensor.rauchmelder"
LIGHT = "light.flur"
PERSON = "person.anna"
FULL = 4096
LOAD = 6000

_METRICS = (
    "received", "filtered_unselected", "filtered_no_interest", "filtered_by_category",
    "queued", "processed", "coalesced", "dropped_coalescible", "dropped_category",
    "dropped_routine", "dropped_lossless", "dropped_protected", "dropped_critical",
    "snapshot_builds", "snapshot_failures", "snapshot_retries", "worker_starts",
    "max_queue_depth", "history_degraded", "max_retained_entries", "cleanup_runs",
)
_STRUCTURES = (
    "queue", "latest", "gaps", "carry_previous", "view_only", "coalescible",
    "lossless", "category", "routine", "protected", "unprocessed_by_entity",
)


class _Bus:
    def __init__(self) -> None:
        self.listeners: list[Any] = []

    def async_listen(self, _event_type: str, listener: Any) -> Any:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def fire(self, event: Any) -> None:
        for listener in list(self.listeners):
            listener(event)


class _Run:
    """One SituationRuntime on the stub state machine."""

    def __init__(self, selected: list[str], categories: str, *,
                 options: dict[str, Any] | None = None, **fields: Any) -> None:
        self.hass = HomeAssistant()
        self.hass.bus = _Bus()
        self.entry = ConfigEntry(options={
            CONF_SELECTED_ENTITIES: selected, CONF_AGENT_EVENT_CATEGORIES: categories,
            **(options or {}),
        })
        self.data = HomeIntentRuntimeData(**fields)
        self.runtime = event_runtime.SituationRuntime(self.hass, self.entry, self.data)
        self.selected = selected
        self.offset = 0
        self.processed: list[tuple[str, str, dict[str, str]]] = []
        self.expired: list[str] = []
        event_runtime.build_entity_snapshots = self._build

        async def _expired(effect: Any) -> None:
            # A deadline reported as "effect missing" (the report itself
            # needs a V12 context or the category; only the fact counts).
            self.expired.append(effect.entity_id)

        self.runtime.async_handle_expected_effect_expired = _expired
        original = self.runtime._async_process_state_changed

        async def _record(raw_event: Any, entities: Any, by_id: Any, **kwargs: Any) -> None:
            data = raw_event.data
            self.processed.append((
                data["entity_id"], data["new_state"].state,
                {item.entity_id: item.state for item in entities},
            ))
            await original(raw_event, entities, by_id, **kwargs)

        self.runtime._async_process_state_changed = _record

    def _build(self, *_args: Any) -> list[EntitySnapshot]:
        result = []
        for entity_id in self.selected:
            state = self.hass.states.get(entity_id)
            if state is not None:
                result.append(EntitySnapshot(
                    entity_id, entity_id, entity_id.split(".")[0], state.state,
                    device_class=state.attributes.get("device_class"),
                    attributes=state.attributes,
                ))
        return result

    def seed(self, entity_id: str, value: str, **attributes: Any) -> None:
        self.hass.states._states[entity_id] = State(entity_id, value, attributes)

    def set(self, entity_id: str, value: str, **attributes: Any) -> None:
        old = self.hass.states.get(entity_id)
        new = State(entity_id, value, attributes)
        self.hass.states._states[entity_id] = new
        self.offset += 1
        self.hass.bus.fire(SimpleNamespace(
            data={"entity_id": entity_id, "old_state": old, "new_state": new},
            time_fired=T0 + timedelta(milliseconds=self.offset),
            context=SimpleNamespace(id=f"ctx-{self.offset}"),
        ))

    async def settle(self) -> None:
        for _ in range(500):
            pending = [task for task in self.hass._tasks if not task.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def pending_tasks(self) -> int:
        return sum(1 for task in self.hass._tasks if not task.done())

    def metrics(self) -> dict[str, Any]:
        values = self.runtime.metrics.as_dict()
        result = {name: values.get(name) for name in _METRICS}
        retention = getattr(self.runtime, "retention", None)
        if callable(retention):
            result.update(retention())
        return result

    def structures(self) -> dict[str, Any]:
        gen = self.runtime._gen
        sizes: dict[str, Any] = {"live": gen.live, "tombstones": gen.tombstones}
        for name in _STRUCTURES:
            value = getattr(gen, name, None)
            sizes[name] = None if value is None else len(value)
        by_priority = getattr(gen, "by_priority", None)
        if isinstance(by_priority, dict):
            for priority, order in by_priority.items():
                sizes[f"deque_{priority}"] = len(order)
        sizes["retained_total"] = sum(
            sizes[name] or 0 for name in ("queue", "latest", "gaps", "carry_previous")
        )
        return sizes

    def effect_processed(self, entity_id: str = LIGHT) -> bool:
        return any(entity == entity_id for entity, *_ in self.processed)


def _lights(count: int) -> list[str]:
    return [f"light.last_{index}" for index in range(count)]


def _flood(run: _Run, entity_ids: list[str], value: str = "on") -> None:
    for entity_id in entity_ids:
        run.set(entity_id, value)


def lossless_effect_after_switch_flood() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for variant, categories, domain in (
        ("switch_flood_safety", "safety", "switch"),
        ("category_flood", "light_unoccupied", "light"),
    ):
        load = [f"{domain}.last_{index}" for index in range(FULL)]
        effects = EffectMonitor(timeout=timedelta(milliseconds=200))
        run = _Run([*load, LIGHT], categories, effect_monitor=effects)
        for entity_id in [*load, LIGHT]:
            run.seed(entity_id, "off")

        async def scenario() -> dict[str, Any]:
            stop = run.runtime.async_start()
            _flood(run, load)
            effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
            run.set(LIGHT, "on")
            burst = {
                "queue_depth": run.runtime._gen.live,
                "light_has_pending_event": run.runtime.has_pending(LIGHT),
            }
            await run.settle()
            await asyncio.sleep(0.3)
            await run.settle()
            burst["effect_still_pending"] = len(effects.pending)
            # The light did reach "on": an expiry is a false "missing" report.
            burst["false_effect_timeout"] = LIGHT in run.expired
            burst["pending_tasks"] = run.pending_tasks()
            stop()
            await effects.async_close()
            return burst

        burst = asyncio.run(scenario())
        result[variant] = {
            **burst, "effect_event_processed": run.effect_processed(), **run.metrics(),
        }
    return result


def effect_before_critical() -> dict[str, Any]:
    load = _lights(FULL - 1)
    effects = EffectMonitor()
    run = _Run([*load, LIGHT, SMOKE], "light_unoccupied", effect_monitor=effects)
    for entity_id in [*load, LIGHT]:
        run.seed(entity_id, "off")
    run.seed(SMOKE, "off", device_class="smoke")

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        run.set(LIGHT, "on")
        _flood(run, load)
        run.set(SMOKE, "on", device_class="smoke")
        depth = run.runtime._gen.live
        await run.settle()
        pending = run.pending_tasks()
        stop()
        await effects.async_close()
        return {"queue_depth": depth, "pending_tasks": pending}

    burst = asyncio.run(scenario())
    return {
        **burst,
        "effect_event_processed": run.effect_processed(),
        "smoke_processed": run.effect_processed(SMOKE),
        **run.metrics(),
    }


def _person_goal() -> MonitorRecord:
    return MonitorRecord(GoalModel(
        GoalKind.MONITOR_AND_NOTIFY, goal_id="goal_anna", lifecycle=GoalLifecycle.MONITOR,
        provenance=GoalProvenance("wenn", "admin"),
        trigger=GoalTrigger("person_leaves_zone", person_entity_id=PERSON, zone_id="home"),
        recipient_person_ids=(PERSON,),
    ))


def monitor_person_after_flood() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        store = MonitorGoalStore(Path(directory) / "monitor_goals.json")

        async def _refresh() -> list[EntitySnapshot]:
            return []

        async def _deliver(*_args: Any) -> bool:
            return True

        async def _history(*_args: Any) -> list[tuple[datetime, float]]:
            return []

        monitor = MonitorGoalRuntime(
            store, SimpleNamespace(
                async_seen_idempotency_key=AsyncMock(return_value=False),
                async_append=AsyncMock(),
            ),
            SimpleNamespace(), _refresh, _deliver, read_history=_history,
        )
        transitions: list[tuple[str, str]] = []
        process = monitor.async_process_person_transition

        async def _person(entity_id: str, old: str, new: str, **kwargs: Any) -> Any:
            transitions.append((old, new))
            return await process(entity_id, old, new, **kwargs)

        monitor.async_process_person_transition = _person  # type: ignore[method-assign]
        load = _lights(FULL)
        run = _Run([*load, PERSON], "light_unoccupied",
                   monitor_goals=store, monitor_runtime=monitor)
        for entity_id in load:
            run.seed(entity_id, "off")
        run.seed(PERSON, "home")

        async def scenario() -> dict[str, Any]:
            await store.async_save(_person_goal())
            reads = store.read_count
            stop = run.runtime.async_start()
            _flood(run, load)
            run.set(PERSON, "not_home")
            run.set(PERSON, "home")
            await run.settle()
            stop()
            return {"store_reads_during_burst": store.read_count - reads}

        burst = asyncio.run(scenario())
    return {
        **burst,
        "transitions": transitions,
        "both_transitions_in_order": transitions == [("home", "not_home"), ("not_home", "home")],
        **run.metrics(),
    }


def category_safety_filter() -> dict[str, Any]:
    switches = [f"switch.last_{index}" for index in range(LOAD // 2)]
    sensors = [f"sensor.last_{index}" for index in range(LOAD // 2)]
    run = _Run([*switches, *sensors, SMOKE], "safety")
    for entity_id in switches:
        run.seed(entity_id, "off")
    for entity_id in sensors:
        run.seed(entity_id, "20.0", device_class="temperature")
    run.seed(SMOKE, "off", device_class="smoke")

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        _flood(run, switches)
        for entity_id in sensors:
            run.set(entity_id, "21.0", device_class="temperature")
        burst = {"queue_after_ordinary_events": run.runtime._gen.live,
                 "workers_after_ordinary_events": run.runtime.metrics.worker_starts}
        run.set(SMOKE, "on", device_class="smoke")
        await run.settle()
        stop()
        return burst

    burst = asyncio.run(scenario())
    return {**burst, "smoke_processed": run.effect_processed(SMOKE), **run.metrics()}


def expected_effect_category_without_effect() -> dict[str, Any]:
    load = _lights(LOAD)
    effects = EffectMonitor()
    run = _Run([*load, LIGHT], "expected_effect_missing", effect_monitor=effects)
    for entity_id in [*load, LIGHT]:
        run.seed(entity_id, "off")

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        _flood(run, load)
        before = run.metrics()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        run.set("light.last_0", "off")
        run.set(LIGHT, "on")
        await run.settle()
        stop()
        await effects.async_close()
        return {"without_effect": before}

    burst = asyncio.run(scenario())
    return {
        **burst,
        "with_effect": run.metrics(),
        "effect_event_processed": run.effect_processed(),
        "unrelated_processed_after_registration": run.effect_processed("light.last_0"),
    }


class _Proactive:
    """Enabled V12 context; only ``RELEVANT`` matters to its detectors."""

    RELEVANT = "binary_sensor.garagentor"
    enabled = True

    def __init__(self) -> None:
        self.observed: list[str] = []

    def is_relevant_event(self, entity_id: str, _device_class: str | None) -> bool:
        return entity_id == self.RELEVANT

    async def async_observe_state(self, entity: Any, *_args: Any) -> None:
        self.observed.append(entity.entity_id)


def proactive_irrelevant_filter() -> dict[str, Any]:
    load = [
        *[f"sensor.last_{index}" for index in range(LOAD // 3)],
        *[f"binary_sensor.last_{index}" for index in range(LOAD // 3)],
        *[f"switch.last_{index}" for index in range(LOAD - 2 * (LOAD // 3))],
    ]
    proactive = _Proactive()
    run = _Run([*load, proactive.RELEVANT], "", proactive_context=proactive)
    for entity_id in load:
        run.seed(entity_id, "20.0" if entity_id.startswith("sensor.") else "off")
    run.seed(proactive.RELEVANT, "off")

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        for entity_id in load:
            run.set(entity_id, "21.0" if entity_id.startswith("sensor.") else "on")
        irrelevant = {**run.metrics(), "pending_tasks": run.pending_tasks()}
        await run.settle()
        run.set(proactive.RELEVANT, "on")
        await run.settle()
        stop()
        return {"irrelevant": irrelevant}

    burst = asyncio.run(scenario())
    return {**burst, "after_relevant": run.metrics(), "observed": proactive.observed[-3:]}


def _broad_run(count: int) -> tuple[_Run, list[str]]:
    # Routine detection with its category: every selected entity matters.
    load = [f"switch.last_{index}" for index in range(count)]
    run = _Run(load, "routine_anomaly", options={CONF_ROUTINE_DETECTION_ENABLED: True})
    for entity_id in load:
        run.seed(entity_id, "off")
    return run, load


def memory_after_6000() -> dict[str, Any]:
    run, load = _broad_run(LOAD)

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        _flood(run, load)
        before = run.structures()
        await run.settle()
        after = run.structures()
        pending = run.pending_tasks()
        result = {"before_drain": before, "after_drain": after, "pending_tasks": pending}
        stop()
        return result

    return {**asyncio.run(scenario()), **run.metrics()}


def memory_after_10_bursts() -> dict[str, Any]:
    run, load = _broad_run(LOAD)
    after: list[dict[str, Any]] = []
    workers: list[int] = []

    async def scenario() -> int:
        stop = run.runtime.async_start()
        for burst in range(10):
            started = run.runtime.metrics.worker_starts
            _flood(run, load, "on" if burst % 2 == 0 else "off")
            workers.append(run.runtime.metrics.worker_starts - started)
            await run.settle()
            after.append(run.structures())
        pending = run.pending_tasks()
        stop()
        return pending

    pending = asyncio.run(scenario())
    return {
        "retained_total_after_each_burst": [sizes["retained_total"] for sizes in after],
        "gaps_after_each_burst": [sizes["gaps"] for sizes in after],
        "latest_after_each_burst": [sizes["latest"] for sizes in after],
        "workers_per_burst": workers,
        "pending_tasks": pending,
        **run.metrics(),
    }


def bounded_bookkeeping_50000() -> dict[str, Any]:
    count = 50_000
    load = [f"switch.last_{index}" for index in range(count)]
    effects = EffectMonitor()
    run = _Run([*load, LIGHT, SMOKE], "routine_anomaly",
               options={CONF_ROUTINE_DETECTION_ENABLED: True}, effect_monitor=effects)
    for entity_id in [*load, LIGHT]:
        run.seed(entity_id, "off")
    run.seed(SMOKE, "off", device_class="smoke")

    async def scenario() -> dict[str, Any]:
        stop = run.runtime.async_start()
        effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
        run.set(LIGHT, "on")
        peak = 0
        for index, entity_id in enumerate(load):
            run.set(entity_id, "on")
            if index % 1000 == 999:
                peak = max(peak, run.structures()["retained_total"])
        run.set(SMOKE, "on", device_class="smoke")
        before = run.structures()
        peak = max(peak, before["retained_total"])
        await run.settle()
        after = run.structures()
        pending = run.pending_tasks()
        stop()
        await effects.async_close()
        return {
            "before_drain": before, "after_drain": after,
            "peak_retained_total": peak, "pending_tasks": pending,
        }

    burst = asyncio.run(scenario())
    effect_view = next(
        (view for entity, _state, view in run.processed if entity == LIGHT), {}
    )
    leaked = sum(1 for entity_id in load if effect_view.get(entity_id) == "on")
    return {
        **burst,
        "effect_event_processed": run.effect_processed(),
        "smoke_processed": run.effect_processed(SMOKE),
        "future_states_in_effect_view": leaked,
        **run.metrics(),
    }


CASES: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("lossless_effect_after_switch_flood", lossless_effect_after_switch_flood),
    ("effect_before_critical", effect_before_critical),
    ("monitor_person_after_flood", monitor_person_after_flood),
    ("category_safety_filter", category_safety_filter),
    ("expected_effect_category_without_effect", expected_effect_category_without_effect),
    ("proactive_irrelevant_filter", proactive_irrelevant_filter),
    ("memory_after_6000", memory_after_6000),
    ("memory_after_10_bursts", memory_after_10_bursts),
    ("bounded_bookkeeping_50000", bounded_bookkeeping_50000),
)


def main(argv: list[str]) -> int:
    original = event_runtime.build_entity_snapshots
    results: dict[str, Any] = {}
    for name, scenario in CASES:
        started = time.perf_counter()
        try:
            result = scenario()
        finally:
            event_runtime.build_entity_snapshots = original
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        results[name] = result
        if "--json" not in argv:
            print(f"{name}: {json.dumps(result, sort_keys=True, default=str)}")
    if "--json" in argv:
        print(json.dumps(results, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
