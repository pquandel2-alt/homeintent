"""7.9.10: EventRuntime circuit timing and fatal-exception boundaries."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from test_event_runtime_hardening_796 import _Harness

from homeintent import event_runtime
from homeintent.const import CONF_ROUTINE_DETECTION_ENABLED


class _FakeClock:
    """Deterministic monotonic clock for supervised retry waits."""

    def __init__(self) -> None:
        self.now = 100.0
        self.waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def backoff(
        self, gen: Any, delay: float, *, wakeable: bool
    ) -> None:
        deadline = (
            gen.worker_next_retry_at
            if wakeable and gen.worker_next_retry_at is not None
            else self.now + delay
        )
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((deadline, future))
        await future

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in self.waiters:
            if deadline <= self.now and not future.done():
                future.set_result(None)


async def _wait_for(predicate: Any, message: str) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(message)


def test_open_circuit_ignores_event_stream_and_retries_once_per_interval(
    monkeypatch, caplog
):
    ids = [f"switch.circuit_{index}" for index in range(32)]
    harness = _Harness(
        monkeypatch,
        ids,
        categories="routine_anomaly",
        options={CONF_ROUTINE_DETECTION_ENABLED: True},
    )
    for entity_id in ids:
        harness.house.seed(entity_id, "off")

    clock = _FakeClock()
    monkeypatch.setattr(event_runtime.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(harness.runtime, "_async_worker_backoff", clock.backoff)
    original_drain = harness.runtime._async_drain
    calls = 0

    async def fail(_gen):
        nonlocal calls
        calls += 1
        raise RuntimeError("permanent provider failure")

    monkeypatch.setattr(harness.runtime, "_async_drain", fail)
    caplog.set_level(logging.INFO, logger=event_runtime.__name__)

    async def wait_for_calls(expected: int) -> None:
        await _wait_for(
            lambda: calls >= expected,
            f"worker made {calls} calls, expected at least {expected}",
        )
        await _wait_for(
            lambda: bool(clock.waiters and not clock.waiters[-1][1].done()),
            "retry wait was not armed",
        )

    async def advance_to_next_retry() -> None:
        deadline = clock.waiters[-1][0]
        clock.advance(deadline - clock.now)
        await asyncio.sleep(0)

    async def open_circuit(first_call: int) -> None:
        for offset in range(1, event_runtime.WORKER_CIRCUIT_FAILURES):
            await advance_to_next_retry()
            await wait_for_calls(first_call + offset)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        harness.house.set(ids[0], "on")
        await wait_for_calls(1)
        await open_circuit(1)

        gen = harness.runtime._gen
        assert gen.worker_circuit_open
        assert gen.worker_next_retry_at == pytest.approx(
            clock.now + event_runtime.WORKER_RETRY_MAX_SECONDS
        )
        assert harness.metrics.worker_circuit_breaks == 1

        # A sustained stream is accepted and bounded by the existing queue,
        # but cannot wake or shorten the open circuit's deadline.
        deadline = gen.worker_next_retry_at
        for entity_id in ids[1:21]:
            harness.house.set(entity_id, "on")
            await asyncio.sleep(0)
        assert calls == event_runtime.WORKER_CIRCUIT_FAILURES
        assert gen.worker_next_retry_at == deadline

        clock.advance(event_runtime.WORKER_RETRY_MAX_SECONDS - 0.001)
        await asyncio.sleep(0)
        assert calls == event_runtime.WORKER_CIRCUIT_FAILURES
        clock.advance(0.001)
        await wait_for_calls(event_runtime.WORKER_CIRCUIT_FAILURES + 1)
        assert calls == event_runtime.WORKER_CIRCUIT_FAILURES + 1
        assert harness.metrics.worker_circuit_breaks == 1

        # The next event still cannot create an extra attempt in this interval.
        harness.house.set(ids[21], "on")
        await asyncio.sleep(0)
        assert calls == event_runtime.WORKER_CIRCUIT_FAILURES + 1

        # One due retry succeeds, drains everything, and closes the cycle.
        monkeypatch.setattr(harness.runtime, "_async_drain", original_drain)
        await advance_to_next_retry()
        await _wait_for(lambda: not harness.pending_tasks(), "worker did not recover")
        assert not gen.worker_circuit_open
        assert gen.worker_next_retry_at is None
        assert harness.metrics.worker_suspended == 0
        assert harness.metrics.worker_recoveries == 1

        # A new circuit is counted only after a genuinely new failure cycle.
        monkeypatch.setattr(harness.runtime, "_async_drain", fail)
        harness.house.set(ids[22], "on")
        second_cycle_first = event_runtime.WORKER_CIRCUIT_FAILURES + 2
        await wait_for_calls(second_cycle_first)
        assert harness.metrics.worker_circuit_breaks == 1
        await open_circuit(second_cycle_first)
        assert harness.metrics.worker_circuit_breaks == 2

        # Unload while open cancels the sole wait and leaves no live task.
        stop()
        await asyncio.gather(*harness.hass._tasks, return_exceptions=True)
        assert harness.pending_tasks() == []

    asyncio.run(scenario())
    messages = [record.getMessage() for record in caplog.records]
    assert sum("event worker failed with" in message for message in messages) == 2
    assert sum("event worker suspended after" in message for message in messages) == 2
    assert sum("event worker recovered after" in message for message in messages) == 1


def test_explicit_health_reset_closes_and_wakes_open_circuit(monkeypatch):
    harness = _Harness(monkeypatch, ["switch.reset"], categories="routine_anomaly")

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        gen = harness.runtime._gen
        gen.worker_failures_in_row = event_runtime.WORKER_CIRCUIT_FAILURES
        gen.worker_circuit_open = True
        gen.worker_suspended = True
        gen.worker_next_retry_at = event_runtime.time.monotonic() + 1000
        gen.metrics.worker_suspended = 1
        gen.worker_wakeup.clear()
        wait = asyncio.create_task(
            harness.runtime._async_worker_backoff(gen, 1000, wakeable=True)
        )
        await asyncio.sleep(0)

        assert harness.runtime.reset_worker_circuit()
        await asyncio.wait_for(wait, timeout=1)
        assert not gen.worker_circuit_open
        assert gen.worker_next_retry_at is None
        assert gen.metrics.worker_suspended == 0
        assert gen.metrics.worker_wakeups == 1
        stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_type", [GeneratorExit, SystemExit, KeyboardInterrupt])
def test_process_control_exception_is_not_retried(monkeypatch, failure_type):
    harness = _Harness(monkeypatch, ["switch.fatal"], categories="routine_anomaly")
    attempts = 0

    async def fatal(_gen):
        nonlocal attempts
        attempts += 1
        raise failure_type()

    monkeypatch.setattr(harness.runtime, "_async_drain", fatal)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        gen = harness.runtime._gen
        gen.live = 1
        with pytest.raises(failure_type):
            await harness.runtime._async_supervise_worker(gen)
        assert attempts == 1
        assert gen.metrics.worker_unexpected_exits == 0
        assert gen.metrics.worker_backoffs == 0
        stop()

    asyncio.run(scenario())


def test_cancelled_worker_ends_without_retry(monkeypatch):
    harness = _Harness(monkeypatch, ["switch.cancel"], categories="routine_anomaly")

    async def cancelled(_gen):
        raise asyncio.CancelledError

    monkeypatch.setattr(harness.runtime, "_async_drain", cancelled)

    async def scenario() -> None:
        stop = harness.runtime.async_start()
        gen = harness.runtime._gen
        gen.live = 1
        with pytest.raises(asyncio.CancelledError):
            await harness.runtime._async_supervise_worker(gen)
        assert gen.metrics.worker_cancellations == 1
        assert gen.metrics.worker_backoffs == 0
        stop()

    asyncio.run(scenario())
