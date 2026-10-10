#!/usr/bin/env python3
"""Machine-readable 7.9.7 EventRuntime overload reproduction.

The scenarios use ``SituationRuntime`` itself with the repository's HA stub.
They intentionally enqueue bursts without yielding before the worker runs.

    .venv/bin/python scripts/event_runtime_repro_797.py
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import sys
import warnings
from types import SimpleNamespace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))
sys.path.insert(0, str(ROOT / "tests"))

from test_event_runtime_hardening_796 import (  # noqa: E402
    LIGHT,
    SMOKE,
    _Harness,
    _legacy_house_consumer,
    _settle,
)

from homeintent import event_runtime  # noqa: E402
from homeintent.const import CONF_ROUTINE_DETECTION_ENABLED  # noqa: E402
from homeintent.effect_monitor import EffectMonitor  # noqa: E402
from homeintent.service_call import ServiceCallPlan  # noqa: E402


class _Patcher:
    """Small reversible subset of pytest's monkeypatch for the harness."""

    def __init__(self) -> None:
        self._changes: list[tuple[Any, str, Any]] = []

    def setattr(self, target: Any, name: str, value: Any, **_kwargs: Any) -> None:
        self._changes.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, value in reversed(self._changes):
            setattr(target, name, value)
        self._changes.clear()


def _result(harness: _Harness, **extra: Any) -> dict[str, Any]:
    metrics = harness.metrics.as_dict()
    return {
        "processed": metrics["processed"],
        "filtered": metrics["filtered_by_interest"] + metrics["filtered_unselected"],
        "coalesced": metrics["coalesced"],
        "dropped_by_priority": {
            "critical": metrics["dropped_critical"],
            "protected": metrics["dropped_protected"],
            "category": metrics["dropped_category"],
            "coalescible": metrics["dropped_coalescible"],
        },
        "queue_peak": metrics["max_queue_depth"],
        "worker_starts": metrics["worker_starts"],
        "worker_restarts": metrics["worker_restarts"],
        "history_degraded": metrics["history_degraded"],
        "retained_live": metrics["retained_live"],
        "retained_auxiliary": metrics["retained_total"] - metrics["retained_live"],
        "retained_total": metrics["retained_total"],
        "max_retained_total": metrics["max_retained_total"],
        "pending_after_drain": len(harness.runtime._pending or ()),
        **extra,
    }


async def _full_effect() -> dict[str, Any]:
    patcher = _Patcher()
    patcher.setattr(event_runtime, "MAX_PENDING_EVENTS", 64)
    switches = [f"switch.effect_{index}" for index in range(64)]
    effects = EffectMonitor()
    harness = _Harness(
        patcher,
        [*switches, LIGHT],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        effect_monitor=effects,
    )
    for entity_id in switches:
        harness.house.seed(entity_id, "off")
    harness.house.seed(LIGHT, "off")
    stop = harness.runtime.async_start()
    for entity_id in switches:
        harness.house.set(entity_id, "on")
    effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    harness.house.set(LIGHT, "on")
    await _settle(harness.hass)
    result = _result(
        harness,
        effect_processed=bool(harness.evaluations_of(LIGHT)),
        effect_pending=len(effects.pending),
    )
    stop()
    await effects.async_close()
    patcher.undo()
    return result


async def _priority_order() -> dict[str, Any]:
    patcher = _Patcher()
    patcher.setattr(event_runtime, "MAX_PENDING_EVENTS", 16)
    switches = [f"switch.priority_{index}" for index in range(18)]
    effects = EffectMonitor()
    harness = _Harness(
        patcher,
        [*switches, LIGHT, SMOKE],
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
        effect_monitor=effects,
    )
    for entity_id in switches:
        harness.house.seed(entity_id, "off")
    harness.house.seed(LIGHT, "off")
    harness.house.seed(SMOKE, "off")
    stop = harness.runtime.async_start()
    for entity_id in switches[:16]:
        harness.house.set(entity_id, "on")
    effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    harness.house.set(LIGHT, "on")
    harness.house.set(switches[16], "on")
    harness.house.set(SMOKE, "on")
    harness.house.set(switches[17], "on")
    await _settle(harness.hass)
    result = _result(
        harness,
        protected_processed=bool(harness.evaluations_of(LIGHT)),
        critical_processed=bool(harness.evaluations_of(SMOKE)),
    )
    stop()
    await effects.async_close()
    patcher.undo()
    return result


async def _negative_filter() -> dict[str, Any]:
    patcher = _Patcher()
    entity_ids = [f"sensor.filtered_{index}" for index in range(6000)]
    proactive = SimpleNamespace(
        enabled=True,
        is_relevant_event=lambda *_args: False,
        async_observe_state=AsyncMock(),
    )
    harness = _Harness(patcher, entity_ids, proactive_context=proactive)
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    for entity_id in entity_ids:
        harness.house.set(entity_id, "1")
    result = _result(harness)
    stop()
    patcher.undo()
    return result


async def _unique_drain(count: int, prefix: str) -> dict[str, Any]:
    patcher = _Patcher()
    entity_ids = [f"sensor.{prefix}_{index}" for index in range(count)]
    harness = _Harness(
        patcher, entity_ids, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entity_ids:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    for entity_id in entity_ids:
        harness.house.set(entity_id, "1")
    retained_before = harness.metrics.retained_total
    await _settle(harness.hass)
    result = _result(harness, retained_before_drain=retained_before)
    stop()
    patcher.undo()
    return result


async def _repeated_bursts() -> dict[str, Any]:
    patcher = _Patcher()
    patcher.setattr(event_runtime, "MAX_PENDING_EVENTS", 64)
    entities = [f"sensor.repeated_{index}" for index in range(100)]
    harness = _Harness(
        patcher, entities, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entities:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    stable: list[int] = []
    for burst in range(10):
        for entity_id in entities:
            harness.house.set(entity_id, str(burst + 1))
        await _settle(harness.hass)
        stable.append(harness.metrics.retained_total)
    result = _result(harness, retained_after_each_burst=stable)
    stop()
    patcher.undo()
    return result


async def _snapshot_retry() -> dict[str, Any]:
    patcher = _Patcher()
    effects = EffectMonitor()
    harness = _Harness(patcher, [LIGHT], effect_monitor=effects)
    harness.house.seed(LIGHT, "off")
    build = harness.build
    attempts = 0

    def flaky(*args: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("reproduction snapshot failure")
        return build(*args)

    patcher.setattr(event_runtime, "build_entity_snapshots", flaky)
    stop = harness.runtime.async_start()
    effects.register(ServiceCallPlan("light", "turn_on", LIGHT, {}))
    harness.house.set(LIGHT, "on")
    await _settle(harness.hass)
    result = _result(
        harness,
        snapshot_attempts=attempts,
        effect_processed=bool(harness.evaluations_of(LIGHT)),
    )
    stop()
    await effects.async_close()
    patcher.undo()
    return result


async def _unload_pending() -> dict[str, Any]:
    patcher = _Patcher()
    entities = [f"sensor.unload_{index}" for index in range(6000)]
    harness = _Harness(
        patcher, entities, proactive_context=_legacy_house_consumer()
    )
    for entity_id in entities:
        harness.house.seed(entity_id, "0")
    stop = harness.runtime.async_start()
    for entity_id in entities:
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


async def _main() -> dict[str, Any]:
    # Each harness deliberately creates reference cycles similar to Home
    # Assistant's listener ownership.  A real unload releases those roots;
    # collect between isolated scenarios so the diagnostic process itself
    # does not make the runtime's bounded retention look cumulative.
    scenarios: dict[str, dict[str, Any]] = {}
    scenarios["full_queue_expected_effect"] = await _full_effect()
    gc.collect()
    scenarios["category_vs_protected"] = await _priority_order()
    gc.collect()
    scenarios["negative_relevance_6000"] = await _negative_filter()
    gc.collect()
    scenarios["drain_6000"] = await _unique_drain(6000, "drain")
    gc.collect()
    scenarios["ten_bursts"] = await _repeated_bursts()
    gc.collect()
    scenarios["unique_50000"] = await _unique_drain(50_000, "extreme")
    gc.collect()
    scenarios["snapshot_retry"] = await _snapshot_retry()
    gc.collect()
    scenarios["unload_pending"] = await _unload_pending()
    gc.collect()
    totals = {
        "processed": sum(item["processed"] for item in scenarios.values()),
        "filtered": sum(item["filtered"] for item in scenarios.values()),
        "coalesced": sum(item["coalesced"] for item in scenarios.values()),
        "dropped_by_priority": {
            priority: sum(
                item["dropped_by_priority"][priority] for item in scenarios.values()
            )
            for priority in ("critical", "protected", "category", "coalescible")
        },
        "queue_peak": max(item["queue_peak"] for item in scenarios.values()),
        "worker_starts": sum(item["worker_starts"] for item in scenarios.values()),
        "worker_restarts": sum(item["worker_restarts"] for item in scenarios.values()),
        "history_degraded": sum(item["history_degraded"] for item in scenarios.values()),
        "retained_live": sum(item["retained_live"] for item in scenarios.values()),
        "retained_auxiliary": sum(
            item["retained_auxiliary"] for item in scenarios.values()
        ),
        "retained_total": sum(item["retained_total"] for item in scenarios.values()),
        "pending_after_drain": sum(
            item["pending_after_drain"] for item in scenarios.values()
        ),
    }
    return {"version": "7.9.7", "scenarios": scenarios, "summary": totals}


def main() -> int:
    logging.getLogger("homeintent.event_runtime").setLevel(logging.CRITICAL + 1)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = asyncio.run(_main())
    report["summary"]["runtime_warnings"] = sum(
        issubclass(item.category, RuntimeWarning) for item in caught
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
