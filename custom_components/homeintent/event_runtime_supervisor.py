"""Single-task retry and circuit supervision for the event runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .event_runtime_queue import _Generation


@dataclass(frozen=True)
class WorkerRetryConfig:
    """Retry limits kept explicit for deterministic tests and diagnostics."""

    base_seconds: float
    max_seconds: float
    circuit_failures: int
    rapid_window_seconds: float
    max_rapid_restarts: int
    status_log_interval_seconds: float


class EventWorkerSupervisor:
    """Own retry timing, circuit transitions and failure-cycle logging."""

    def __init__(
        self,
        *,
        owns: Callable[[_Generation], bool],
        drain: Callable[[_Generation], Awaitable[None]],
        backoff: Callable[..., Awaitable[None]],
        monotonic: Callable[[], float],
        logger: logging.Logger,
        config: WorkerRetryConfig,
    ) -> None:
        self._owns = owns
        self._drain = drain
        self._backoff = backoff
        self._monotonic = monotonic
        self._logger = logger
        self._config = config

    async def run(self, gen: _Generation) -> None:
        """Run drain attempts with bounded backoff and a persistent circuit."""
        attempts = 0
        while self._owns(gen) and gen.live:
            attempts += 1
            metrics = gen.metrics
            metrics.worker_starts += 1
            if attempts > 1:
                metrics.worker_restarts += 1
            try:
                await self._drain(gen)
                if self._owns(gen) and gen.live:
                    raise RuntimeError("event worker ended with pending work")
            except asyncio.CancelledError:
                metrics.worker_cancellations += 1
                if self._owns(gen):
                    gen.rollback_lease()
                raise
            except Exception as failure:  # noqa: BLE001 - supervisor boundary
                if not self._owns(gen):
                    return
                restored = gen.rollback_lease()
                if restored:
                    metrics.batch_failures += 1
                    metrics.batch_retries += 1
                    metrics.batch_requeued_events += restored
                metrics.worker_unexpected_exits += 1
                gen.worker_failures_in_row += 1
                if gen.worker_failure_signature is None:
                    gen.worker_failure_signature = type(failure).__name__
                    self._logger.error(
                        "HomeIntent event worker failed with %s pending events",
                        gen.live,
                        exc_info=(type(failure), failure, failure.__traceback__),
                    )
                else:
                    gen.suppressed_worker_logs += 1
                now = self._monotonic()
                gen.rapid_worker_failures.append(now)
                while (
                    gen.rapid_worker_failures
                    and now - gen.rapid_worker_failures[0]
                    > self._config.rapid_window_seconds
                ):
                    gen.rapid_worker_failures.popleft()
                circuit = (
                    gen.worker_failures_in_row >= self._config.circuit_failures
                    or len(gen.rapid_worker_failures)
                    >= self._config.max_rapid_restarts
                )
                exponent = min(gen.worker_failures_in_row - 1, 30)
                delay = min(
                    self._config.max_seconds,
                    self._config.base_seconds * (2**exponent),
                )
                if circuit:
                    self._open_or_report_circuit(gen, now)
                    gen.worker_suspended = True
                    metrics.worker_suspended = 1
                    delay = self._config.max_seconds
                    gen.worker_next_retry_at = now + delay
                    # Clear before yielding so an explicit reset cannot be
                    # lost between the state transition and the wait.
                    gen.worker_wakeup.clear()
                else:
                    gen.worker_next_retry_at = None
                metrics.worker_backoffs += 1
                metrics.worker_backoff_milliseconds += round(delay * 1000)
                try:
                    await self._backoff(gen, delay, wakeable=circuit)
                except asyncio.CancelledError:
                    metrics.worker_cancellations += 1
                    raise
                continue
            self._record_recovery(gen)
        gen.update_retention()

    def _open_or_report_circuit(self, gen: _Generation, now: float) -> None:
        metrics = gen.metrics
        if not gen.worker_circuit_open:
            metrics.worker_circuit_breaks += 1
            self._logger.error(
                "HomeIntent event worker suspended after %s repeated failures; "
                "%s similar failure log(s) suppressed",
                gen.worker_failures_in_row,
                gen.suppressed_worker_logs,
            )
            gen.worker_circuit_open = True
            gen.last_worker_status_log = now
            return
        if (
            gen.last_worker_status_log is not None
            and now - gen.last_worker_status_log
            < self._config.status_log_interval_seconds
        ):
            return
        gen.last_worker_status_log = now
        self._logger.warning(
            "HomeIntent event worker remains suspended with %s pending events; "
            "%s failure log(s) suppressed",
            gen.live,
            gen.suppressed_worker_logs,
        )

    def _record_recovery(self, gen: _Generation) -> None:
        failures = gen.worker_failures_in_row
        was_open = gen.worker_circuit_open
        suppressed = gen.suppressed_worker_logs
        if failures:
            gen.metrics.worker_recoveries += 1
            if was_open:
                self._logger.info(
                    "HomeIntent event worker recovered after %s failures; "
                    "%s failure log(s) were suppressed",
                    failures,
                    suppressed,
                )
        self.reset(gen)

    @staticmethod
    def reset(gen: _Generation) -> None:
        """Close one failure cycle without touching queued or leased work."""
        gen.worker_failures_in_row = 0
        gen.worker_failure_signature = None
        gen.rapid_worker_failures.clear()
        gen.suppressed_worker_logs = 0
        gen.worker_circuit_open = False
        gen.worker_next_retry_at = None
        gen.worker_suspended = False
        gen.last_worker_status_log = None
        gen.metrics.worker_suspended = 0


async def async_worker_backoff(
    gen: _Generation, delay: float, *, wakeable: bool
) -> None:
    """Wait without blocking callbacks; only a health reset wakes a circuit."""
    if not wakeable:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(gen.worker_wakeup.wait(), timeout=delay)
    except TimeoutError:
        pass
    else:
        gen.metrics.worker_wakeups += 1
