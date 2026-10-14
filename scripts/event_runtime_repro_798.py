#!/usr/bin/env python3
"""Compact 7.9.8 EventRuntime degradation/correctness reproduction."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import sys
import tempfile
import warnings
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from event_runtime_repro_797 import _Patcher  # noqa: E402
from test_event_runtime_hardening_796 import (  # noqa: E402
    CLIMATE,
    LIGHT,
    PERSON,
    TEMP,
    T0,
    WINDOW,
    _Harness,
    _agent,
    _legacy_house_consumer,
    _settle,
)
from test_event_runtime_hardening_798 import _seed_thermal, _thermal_tracker  # noqa: E402

from homeintent import event_runtime  # noqa: E402
from homeintent.const import (  # noqa: E402
    CONF_AGENT_LONG_RUNNING_MINUTES,
    CONF_ROUTINE_DETECTION_ENABLED,
)
from homeintent.effect_monitor import EffectMonitor  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeassistant.core import State  # noqa: E402


def _result(harness: _Harness, **extra: Any) -> dict[str, Any]:
    metrics = harness.metrics
    return {
        "thermal_protected_delivered": metrics.delivered_thermal,
        "thermal_protected_suppressed": metrics.suppressed_thermal,
        "proactive_delivered": metrics.delivered_proactive,
        "proactive_suppressed": metrics.suppressed_proactive,
        "monitor_delivered": metrics.delivered_monitor,
        "effect_delivered": metrics.delivered_effect,
        "history_suppressed": metrics.consumer_suppressed_history,
        "registry_suppressed": metrics.consumer_suppressed_registry,
        "worker_starts": metrics.worker_starts,
        "worker_restarts": metrics.worker_restarts,
        "retained_total": metrics.retained_total,
        "pending_after_drain": len(harness.runtime._pending or ()),
        "runtime_warnings": 0,
        **extra,
    }


def _history_harness(
    patcher: _Patcher, *, fields: dict[str, Any], count: int = 12
) -> tuple[_Harness, list[str]]:
    patcher.setattr(event_runtime, "MAX_PENDING_EVENTS", 8)
    patcher.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    ordinary = [f"switch.history_{index}" for index in range(count)]
    harness = _Harness(
        patcher,
        [*ordinary, LIGHT, CLIMATE, TEMP, WINDOW, PERSON, "person.ben"],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        **fields,
    )
    for entity_id in ordinary:
        harness.house.seed(entity_id, "off")
    return harness, ordinary


async def _thermal_history(path: Path) -> dict[str, Any]:
    patcher = _Patcher()
    tracker = _thermal_tracker(path)
    harness, ordinary = _history_harness(patcher, fields={"thermal_tracker": tracker})
    _seed_thermal(harness)
    seen: list[str] = []
    original = tracker.async_observe_states

    async def observe(states, **kwargs):
        seen.extend(item.state for item in states if item.entity_id == TEMP)
        await original(states, **kwargs)

    tracker.async_observe_states = observe  # type: ignore[method-assign]
    stop = harness.runtime.async_start()
    for entity_id in ordinary:
        harness.house.set(entity_id, "on")
    harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
    await _settle(harness.hass)
    result = _result(
        harness,
        history_degraded=harness.metrics.history_degraded,
        temperature_seen=seen,
        dropped_protected=harness.metrics.dropped_protected,
    )
    stop()
    patcher.undo()
    return result


async def _unsafe_thermal(path: Path) -> dict[str, Any]:
    patcher = _Patcher()
    patcher.setattr(event_runtime, "MAX_PENDING_EVENTS", 3)
    patcher.setattr(event_runtime, "MAX_HISTORY_ENTITIES", 1)
    tracker = _thermal_tracker(path)
    harness = _Harness(patcher, [CLIMATE, TEMP, WINDOW], thermal_tracker=tracker)
    _seed_thermal(harness)
    tracker.async_observe_states = AsyncMock()  # type: ignore[method-assign]
    stop = harness.runtime.async_start()
    harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
    harness.house.set(WINDOW, "on")
    harness.house.set(CLIMATE, "cool", temperature=22.0)
    harness.house.set(TEMP, "21.0", unit_of_measurement="°C")
    harness.house.set(WINDOW, "off")
    await _settle(harness.hass)
    result = _result(
        harness,
        history_degraded=harness.metrics.history_degraded,
        dropped_protected=harness.metrics.dropped_protected,
    )
    stop()
    patcher.undo()
    return result


async def _effect_history() -> dict[str, Any]:
    patcher = _Patcher()
    effects = EffectMonitor()
    harness, ordinary = _history_harness(patcher, fields={"effect_monitor": effects})
    harness.house.seed(LIGHT, "off")
    stop = harness.runtime.async_start()
    for entity_id in ordinary:
        harness.house.set(entity_id, "on")
    effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    harness.house.set(LIGHT, "on")
    await _settle(harness.hass)
    result = _result(harness, effect_pending=len(effects.pending))
    stop()
    await effects.async_close()
    patcher.undo()
    return result


class _MonitorStore:
    loaded = True
    watched_value_entity_ids = frozenset({TEMP})
    watched_person_entity_ids = frozenset({PERSON})
    watched_nobody_home_person_ids = frozenset({PERSON, "person.ben"})
    nobody_home_uses_household = False

    def add_change_listener(self, _listener):
        return lambda: None

    def person_transition_needs_house_context(self, _entity_id):
        return False

    def person_transition_needs_snapshot(self, _entity_id):
        return False


async def _monitor_history() -> dict[str, Any]:
    patcher = _Patcher()
    monitor = SimpleNamespace(
        store=_MonitorStore(),
        async_process_person_transition=AsyncMock(return_value=()),
        async_process_value_change=AsyncMock(return_value=()),
    )
    harness, ordinary = _history_harness(patcher, fields={"monitor_runtime": monitor})
    harness.house.seed(PERSON, "home")
    harness.house.seed("person.ben", "not_home")
    harness.house.seed(TEMP, "19.0")
    stop = harness.runtime.async_start()
    for entity_id in ordinary:
        harness.house.set(entity_id, "on")
    harness.house.set(TEMP, "20.0")
    harness.house.set(PERSON, "not_home")
    await _settle(harness.hass)
    result = _result(
        harness,
        person_calls=monitor.async_process_person_transition.await_count,
        value_calls=monitor.async_process_value_change.await_count,
    )
    stop()
    patcher.undo()
    return result


async def _proactive_history() -> dict[str, Any]:
    patcher = _Patcher()
    proactive = SimpleNamespace(
        enabled=True,
        requires_complete_house_view=True,
        requires_registry_data=True,
        is_relevant_event=lambda *_args: True,
        async_observe_state=AsyncMock(),
    )
    harness, ordinary = _history_harness(patcher, fields={"proactive_context": proactive})
    stop = harness.runtime.async_start()
    for entity_id in ordinary:
        harness.house.set(entity_id, "on")
    await _settle(harness.hass)
    result = _result(harness, observe_calls=proactive.async_observe_state.await_count)
    stop()
    patcher.undo()
    return result


async def _registry_failure(path: Path) -> dict[str, Any]:
    patcher = _Patcher()
    patcher.setattr(event_runtime, "SNAPSHOT_RETRY_BASE_SECONDS", 0.001)
    effects = EffectMonitor()
    tracker = _thermal_tracker(path)
    proactive = SimpleNamespace(
        enabled=True,
        requires_complete_house_view=True,
        requires_registry_data=True,
        is_relevant_event=lambda *_args: True,
        async_observe_state=AsyncMock(),
    )
    harness = _Harness(
        patcher,
        [LIGHT, CLIMATE, TEMP, WINDOW, "switch.registry"],
        effect_monitor=effects,
        thermal_tracker=tracker,
        proactive_context=proactive,
    )
    _seed_thermal(harness)
    harness.house.seed(LIGHT, "off")
    harness.house.seed("switch.registry", "off")

    def broken(*_args):
        raise RuntimeError("registry unavailable")

    patcher.setattr(event_runtime, "build_entity_snapshots", broken)
    stop = harness.runtime.async_start()
    effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    harness.house.set(LIGHT, "on")
    harness.house.set(TEMP, "20.0", unit_of_measurement="°C")
    harness.house.set("switch.registry", "on")
    await _settle(harness.hass)
    result = _result(
        harness,
        registry_degraded_batches=harness.metrics.registry_degraded_batches,
        effect_pending=len(effects.pending),
    )
    stop()
    await effects.async_close()
    patcher.undo()
    return result


async def _long_running() -> dict[str, Any]:
    patcher = _Patcher()
    agent = _agent()
    entity_id = "switch.pump"
    harness = _Harness(
        patcher,
        [entity_id],
        categories="long_running_state",
        options={CONF_AGENT_LONG_RUNNING_MINUTES: 60},
        proactive_agent=agent,
    )
    harness.house.seed(entity_id, "on")
    stop = harness.runtime.async_start()
    old = harness.hass.states.get(entity_id)
    new = State(entity_id, "on", {"detail": "attribute update"})
    new.last_changed = T0
    harness.hass.states._states[entity_id] = new
    harness.hass.bus.fire(SimpleNamespace(
        data={"entity_id": entity_id, "old_state": old, "new_state": new},
        time_fired=T0 + timedelta(minutes=61),
        context=SimpleNamespace(id="ctx-long-running"),
    ))
    await _settle(harness.hass)
    result = _result(harness, signals=agent.async_signal.await_count)
    stop()
    patcher.undo()
    return result


async def _unique_50000() -> dict[str, Any]:
    patcher = _Patcher()
    entity_ids = [f"sensor.extreme_{index}" for index in range(50_000)]
    harness = _Harness(
        patcher, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    for entity_id in entity_ids:
        harness.house.set(entity_id, "1")
    await _settle(harness.hass)
    result = _result(
        harness,
        history_degraded=harness.metrics.history_degraded,
        queue_peak=harness.metrics.max_queue_depth,
        max_retained_total=harness.metrics.max_retained_total,
    )
    stop()
    patcher.undo()
    return result


async def _unload_pending() -> dict[str, Any]:
    patcher = _Patcher()
    entity_ids = [f"sensor.unload_{index}" for index in range(6000)]
    harness = _Harness(
        patcher, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    for entity_id in entity_ids:
        harness.house.set(entity_id, "1")
    pending_before = len(harness.runtime._pending or ())
    stop()
    await _settle(harness.hass)
    result = _result(
        harness,
        pending_before_unload=pending_before,
        unfinished_tasks=sum(1 for task in harness.hass._tasks if not task.done()),
    )
    patcher.undo()
    return result


async def _main(root: Path) -> dict[str, Any]:
    scenarios = {
        "thermal_protected_history": await _thermal_history(root / "thermal-safe"),
        "thermal_unsafe_history": await _unsafe_thermal(root / "thermal-unsafe"),
        "expected_effect_history": await _effect_history(),
        "monitor_history": await _monitor_history(),
        "proactive_history": await _proactive_history(),
        "registry_failure": await _registry_failure(root / "registry"),
        "long_running_state_e2e": await _long_running(),
        "unique_50000": await _unique_50000(),
        "unload_pending": await _unload_pending(),
    }
    gc.collect()
    keys = (
        "thermal_protected_delivered",
        "thermal_protected_suppressed",
        "proactive_delivered",
        "proactive_suppressed",
        "monitor_delivered",
        "effect_delivered",
        "history_suppressed",
        "registry_suppressed",
        "worker_starts",
        "worker_restarts",
        "retained_total",
        "pending_after_drain",
        "runtime_warnings",
    )
    summary = {key: sum(item[key] for item in scenarios.values()) for key in keys}
    return {"version": "7.9.8", "scenarios": scenarios, "summary": summary}


def main() -> int:
    logging.getLogger("homeintent.event_runtime").setLevel(logging.CRITICAL + 1)
    with tempfile.TemporaryDirectory(prefix="homeintent-repro-798-") as temporary:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            report = asyncio.run(_main(Path(temporary)))
    runtime_warnings = sum(
        issubclass(item.category, RuntimeWarning) for item in caught
    )
    report["summary"]["runtime_warnings"] = runtime_warnings
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
