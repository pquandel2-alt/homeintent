"""Home Assistant state-change bridge into normalized local situations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_ANOMALY_THRESHOLD_PERCENT,
    CONF_BANTER_LEVEL,
    CONF_PERSONA_STYLE,
    CONF_ROUTINE_DETECTION_ENABLED,
    CONF_ROUTINE_MIN_OBSERVATIONS,
)
from .agent_config_validation import parse_event_categories
from .entities import EntitySnapshot
from .event_interest import (
    EventFilterReason,
    EventInterestIndex,
    MAX_CONSUMER_WATCH_ENTITIES,
)
from .event_priority import EventPriority
from .event_runtime_metrics import EventRuntimeMetrics
from .event_runtime_queue import (
    _EVICTED,
    _QUEUED,
    _Entry,
    _Generation,
)
from .event_runtime_supervisor import (
    EventWorkerSupervisor,
    WorkerRetryConfig,
    async_worker_backoff,
)
from .event_runtime_scheduler import (
    LongRunningInterval as _LongRunningInterval,
    LongRunningScheduler,
)
from .long_running import activity_marker
from .hass_entities import (
    SelectedEntityFilter,
    build_entity_snapshots,
    snapshot_at_state,
    snapshot_from_state,
)
from .entities import spoken_state
from .agent_event import AgentMode
from .effect_monitor import ExpectedEffect
from .proactive_decision import ProactiveDecisionEngine
from .runtime_data import HomeIntentRuntimeData
from .service_call import ServiceCallPlan
from .statistical_models import evaluate_latency_anomaly
from .response_planner import (
    DialogAct,
    GermanResponseRealizer,
    PersonaStyle,
    ResponsePlan,
    Urgency,
)
from .situation import (
    RoutineStatistics,
    Situation,
    SituationSeverity,
    normalize_state_change,
)


_LOGGER = logging.getLogger(__name__)

# Live entries (to evaluate or kept for the house view) waiting for the single
# worker (7.9.4 P0). The bound keeps memory finite; under overload the
# priority decides what goes first (7.9.7): coalescible, category, protected,
# then critical. Equal-priority overload keeps the already queued FIFO work.
MAX_PENDING_EVENTS = 4096
# Exact rewind information is useful only while older live work exists. It is
# separately bounded because limiting the queue alone did not limit 7.9.6's
# ``gaps``/``carry_previous`` dictionaries. Queue records plus auxiliary
# records therefore have a finite, documented ceiling.
MAX_HISTORY_ENTITIES = 4096
# Conservative accounting ceiling for queue/tombstone records, priority
# references, latest/pending maps, the two history maps and the bounded set of
# protected entities whose own history became unsafe. Structures share entries,
# so this is a reference-record bound rather than an object-byte estimate. See
# ``_Generation.update_retention``.
MAX_TRANSIENT_RECORDS = 10 * MAX_PENDING_EVENTS + 1024
# Events taken from the queue per snapshot build. The snapshot is the house
# as it is now; each event is evaluated against the house as it was when the
# event fired (7.9.5), derived from the queued events' own old/new states.
# Every batch rewinds all still queued changes, so a full queue costs
# O(queue / batch) rewinds of the whole queue; 7.9.6 takes 1024 instead of
# 256 (the view is exact for any batch size, a snapshot per batch remains).
MAX_BATCH_EVENTS = 1024
_DROP_WARNING_INTERVAL_SECONDS = 60.0
# The worker hands the event loop back after this much work in one go.
_YIELD_AFTER_SECONDS = 0.02
# A failed snapshot build keeps the batch queued and is retried with an
# exponential backoff (7.9.6); after ``SNAPSHOT_MAX_ATTEMPTS`` failures in a
# row the batch is evaluated without registry data (critical and lossless
# events) instead of waiting forever.
SNAPSHOT_MAX_ATTEMPTS = 5
SNAPSHOT_RETRY_BASE_SECONDS = 0.05
SNAPSHOT_RETRY_MAX_SECONDS = 5.0
_SNAPSHOT_ERROR_INTERVAL_SECONDS = 60.0
_HISTORY_WARNING_INTERVAL_SECONDS = 60.0
_SUPPRESSION_WARNING_INTERVAL_SECONDS = 60.0
# One supervisor task owns all attempts for a generation.  It sleeps between
# attempts instead of creating a task from every failing done callback.
WORKER_RETRY_BASE_SECONDS = 0.05
WORKER_RETRY_MAX_SECONDS = 5.0
WORKER_CIRCUIT_FAILURES = 5
WORKER_RAPID_WINDOW_SECONDS = 60.0
WORKER_MAX_RAPID_RESTARTS = 5
# Once the circuit is open, repeated failures are summarized at this cadence
# instead of producing one traceback for every accepted state change.
WORKER_STATUS_LOG_INTERVAL_SECONDS = 60.0
# One heap and one timer for every selected long-running entity; never one
# task per entity.  Stale heap rows are compacted before this hard ceiling.
MAX_LONG_RUNNING_TIMERS = 4096


class DegradationMode(StrEnum):
    """Data quality of the historical view supplied to an evaluation."""

    NORMAL = "normal"
    HISTORY = "history_degraded"
    REGISTRY = "registry_degraded"


class _MergedEvent:
    """The evaluation of merged events: first ``old_state``, last event."""

    __slots__ = ("data", "time_fired", "context", "origin")

    def __init__(self, event: Any, previous: Any) -> None:
        data = _event_data(event)
        self.data = {**data, "old_state": previous}
        self.time_fired = getattr(event, "time_fired", None)
        self.context = getattr(event, "context", None)
        self.origin = event


class SituationRuntime:
    """Normalizes selected HA events and emits bounded AgentEvents."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, runtime_data: HomeIntentRuntimeData
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime_data = runtime_data
        self._seen: set[str] = set()
        self._decision_engine = ProactiveDecisionEngine()
        self._is_selected = SelectedEntityFilter(hass, entry)
        self._interest = EventInterestIndex(entry, runtime_data)
        self._gen = _Generation(MAX_HISTORY_ENTITIES)
        self._gen.stopped = True
        self._worker: asyncio.Task[None] | None = None
        self._worker_supervisor = EventWorkerSupervisor(
            owns=lambda generation: self._owns(generation),
            drain=lambda generation: self._async_drain(generation),
            backoff=lambda generation, delay, *, wakeable: self._async_worker_backoff(
                generation, delay, wakeable=wakeable
            ),
            monotonic=lambda: time.monotonic(),
            logger=_LOGGER,
            config=WorkerRetryConfig(
                base_seconds=WORKER_RETRY_BASE_SECONDS,
                max_seconds=WORKER_RETRY_MAX_SECONDS,
                circuit_failures=WORKER_CIRCUIT_FAILURES,
                rapid_window_seconds=WORKER_RAPID_WINDOW_SECONDS,
                max_rapid_restarts=WORKER_MAX_RAPID_RESTARTS,
                status_log_interval_seconds=WORKER_STATUS_LOG_INTERVAL_SECONDS,
            ),
        )
        self._long_running_scheduler = LongRunningScheduler(
            hass=hass,
            entry=entry,
            owns=lambda generation: self._owns(generation),
            is_selected=lambda entity_id: self._is_selected(entity_id),
            enqueue=lambda event: self.async_enqueue_state_changed(event),
            snapshot_from_state=lambda entity_id, state: snapshot_from_state(
                entity_id, state
            ),
            utcnow=lambda: dt_util.utcnow(),
            schedule_later=lambda delay, callback, *args: _schedule_later(
                delay, callback, *args
            ),
            capacity=lambda: MAX_LONG_RUNNING_TIMERS,
            logger=_LOGGER,
        )
        # Compatibility seams used by the existing load and lifecycle tests.
        self._long_running_notified = self._long_running_scheduler.notified
        self._long_running = self._long_running_scheduler.intervals
        self._long_running_heap = self._long_running_scheduler.heap
        self._stopped = True

    # -- introspection (tests, diagnostics) --------------------------------
    @property
    def metrics(self) -> EventRuntimeMetrics:
        """Counters of the current (or last) runtime start."""
        return self._gen.metrics

    @property
    def interest(self) -> EventInterestIndex:
        return self._interest

    @property
    def _pending(self) -> tuple[Any, ...] | None:
        """Live queued entries, ``None`` before the first queued event."""
        gen = self._gen
        if not gen.created_queue or gen.stopped:
            return None
        return tuple(gen.queued_entries())

    @property
    def _dropped(self) -> int:
        metrics = self._gen.metrics
        return (
            metrics.dropped_coalescible + metrics.dropped_lossless + metrics.dropped_critical
        )

    def has_pending(self, entity_id: str) -> bool:
        """A state change of ``entity_id`` still waits for its evaluation."""
        return entity_id in self._gen.unprocessed_by_entity

    @callback
    def reset_worker_circuit(self) -> bool:
        """Reset a failed worker cycle and wake its supervised retry.

        Normal events intentionally never call this method. It is a narrow
        health/reload seam for operators and tests; a full integration reload
        also resets the state by creating a new generation.
        """
        gen = self._gen
        if not self._owns(gen) or not (
            gen.worker_circuit_open or gen.worker_failures_in_row
        ):
            return False
        self._worker_supervisor.reset(gen)
        gen.worker_wakeup.set()
        _LOGGER.info("HomeIntent event worker circuit was reset explicitly")
        return True

    # -- lifecycle ---------------------------------------------------------
    def async_start(self) -> Any:
        effect_monitor = self._runtime_data.effect_monitor
        effect_monitor.set_expired_handler(self.async_handle_expected_effect_expired)
        set_probe = getattr(effect_monitor, "set_pending_probe", None)
        if callable(set_probe):
            set_probe(self.has_pending)
        old = self._gen
        old.stopped = True
        old.worker_wakeup.set()
        previous_worker, self._worker = self._worker, None
        if previous_worker is not None and not previous_worker.done():
            previous_worker.cancel()
        self._clear_long_running_scheduler()
        old.discard_pending()
        self._gen = _Generation(MAX_HISTORY_ENTITIES)
        self._stopped = False
        self._long_running_notified.clear()
        self._interest.bind()
        bus = getattr(self._hass, "bus", None)
        listen = getattr(bus, "async_listen", None)
        if listen is None:
            unlisten = lambda: None
        else:
            # A plain event-loop callback: Home Assistant runs it inline for
            # every state change in the house, so it only filters and queues.
            # One worker does the real work; no task per event (7.9.4 P0).
            unlisten = listen("state_changed", self.async_enqueue_state_changed)
        generation = self._gen
        self._long_running_scheduler.start(generation)

        def stop() -> None:
            unlisten()
            generation.stopped = True
            generation.worker_wakeup.set()
            if self._gen is generation:
                self._stopped = True
                self._interest.unbind()
                worker, self._worker = self._worker, None
                if worker is not None and not worker.done():
                    worker.cancel()
                self._clear_long_running_scheduler()
                if callable(set_probe):
                    set_probe(None)
                self._runtime_data.effect_monitor.set_expired_handler(None)
            # Release the queue and any leased batch; metrics stay readable.
            generation.discard_pending()

        return stop

    # -- central long-running-state scheduler -----------------------------
    def _long_running_minutes(self) -> int:
        return self._long_running_scheduler.minutes()

    def _long_running_enabled(self) -> bool:
        return self._long_running_scheduler.enabled()

    async def _async_initialize_long_running(self, gen: _Generation) -> None:
        await self._long_running_scheduler.initialize(gen)

    def _sync_long_running_interval(
        self,
        gen: _Generation,
        entity: EntitySnapshot,
        old_state: Any,
        occurred_at: datetime,
        *,
        initial: bool = False,
    ) -> _LongRunningInterval | None:
        return self._long_running_scheduler.sync(
            gen, entity, old_state, occurred_at, initial=initial
        )

    def _clear_long_running_scheduler(self) -> None:
        self._long_running_scheduler.clear(self._gen)

    # -- event-bus callback ------------------------------------------------
    @callback
    def async_enqueue_state_changed(self, raw_event: Any) -> None:
        """Queue a needed state change of a selected entity; drop the rest.

        Synchronous and cheap: a set lookup for the selection, attribute
        reads and set lookups for the interest - no disk, no registry, no
        snapshot, no task per event (7.9.4 P0, 7.9.6).
        """
        gen = self._gen
        if gen.stopped:
            return
        data = getattr(raw_event, "data", None)
        if not isinstance(data, dict):
            return
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not isinstance(entity_id, str) or new_state is None:
            return
        metrics = gen.metrics
        metrics.received += 1
        if not self._is_selected(entity_id):
            metrics.filtered_unselected += 1
            return
        old_state = data.get("old_state")
        priority, filter_reason = self._interest.classify_with_reason(
            entity_id, old_state, new_state
        )
        if priority is None:
            metrics.filtered_no_interest += 1
            metrics.filtered_by_interest += 1
            if filter_reason is EventFilterReason.CATEGORY:
                metrics.filtered_by_category += 1
            elif filter_reason is EventFilterReason.PROACTIVE:
                metrics.filtered_by_proactive += 1
            else:
                metrics.filtered_no_consumer += 1
            return
        self._enqueue(gen, raw_event, entity_id, old_state, new_state, priority)

    def _enqueue(
        self, gen: _Generation, raw_event: Any, entity_id: str, old_state: Any,
        new_state: Any, priority: EventPriority,
    ) -> None:
        metrics = gen.metrics
        gen.seq += 1
        seq = gen.seq
        latest = gen.latest.get(entity_id)
        mergeable = (
            priority is EventPriority.COALESCIBLE
            and latest is not None
            and latest.status == _QUEUED
            and latest.process
            and latest.priority is EventPriority.COALESCIBLE
        )
        if mergeable and latest is not None and gen.queue and gen.queue[-1] is latest:
            # Directly behind its predecessor: nothing happened in between,
            # so one entry for both keeps the history exact.
            latest.event = raw_event
            latest.new_state = new_state
            metrics.queued += 1
            metrics.coalesced += 1
            return
        entry = _Entry(seq, raw_event, entity_id, old_state, new_state, priority)
        if gen.live >= MAX_PENDING_EVENTS and not self._make_room(gen, priority):
            self._reject(gen, entry)
            return
        if mergeable and latest is not None:
            # The earlier change stays in the queue for the house view, its
            # evaluation moves into this one (first previous, last state).
            latest.process = False
            entry.previous = latest.previous
            gen.view_only.append(latest)
            gen.unprocessed(entity_id, -1)
            metrics.coalesced += 1
        elif priority is EventPriority.COALESCIBLE:
            if entity_id in gen.carry_previous:
                entry.previous = gen.carry_previous.pop(entity_id)
        else:
            gen.carry_previous.pop(entity_id, None)
        gen.queue.append(entry)
        gen.created_queue = True
        gen.live += 1
        gen.latest[entity_id] = entry
        gen.unprocessed(entity_id, 1)
        if priority is EventPriority.COALESCIBLE:
            gen.coalescible.append(entry)
        elif priority is EventPriority.CATEGORY:
            gen.category.append(entry)
        elif priority is EventPriority.PROTECTED:
            gen.protected.append(entry)
        metrics.queued += 1
        if gen.live > metrics.max_queue_depth:
            metrics.max_queue_depth = gen.live
        if self._worker is None or self._worker.done():
            self._start_worker(gen)
        gen.update_retention()

    def _start_worker(self, gen: _Generation) -> None:
        """Create the generation's sole long-lived supervisor task."""
        if not self._owns(gen) or (self._worker is not None and not self._worker.done()):
            return
        # Not eager: the bus callback stays cheap and never re-enters.
        worker = self._hass.async_create_background_task(
            self._async_supervise_worker(gen),
            "HomeIntent situation events",
            eager_start=False,
        )
        self._worker = worker
        worker.add_done_callback(lambda task: self._worker_finished(gen, task))

    @callback
    def _worker_finished(
        self, gen: _Generation, task: asyncio.Task[None]
    ) -> None:
        """Retrieve terminal state and close the enqueue/finalization race."""
        failure: BaseException | None = None
        if not task.cancelled():
            try:
                failure = task.exception()
            except asyncio.CancelledError:
                pass
        if self._worker is not task:
            return
        self._worker = None
        # Interpreter/process-control exceptions must never enter normal
        # recovery. Retrieving the exception above prevents an unhandled-task
        # warning; leaving the queued work untouched keeps the failure visible.
        if failure is not None and not isinstance(failure, Exception):
            return
        if self._owns(gen) and gen.live:
            self._start_worker(gen)

    async def _async_supervise_worker(self, gen: _Generation) -> None:
        """Delegate retry and circuit state to the single-task supervisor."""
        await self._worker_supervisor.run(gen)

    async def _async_worker_backoff(
        self, gen: _Generation, delay: float, *, wakeable: bool
    ) -> None:
        """Preserved test seam for injected retry clocks."""
        await async_worker_backoff(gen, delay, wakeable=wakeable)

    def _make_room(self, gen: _Generation, priority: EventPriority) -> bool:
        """Evict one entry of lower rank for an incoming ``priority`` event.

        History-only entries go first (no evaluation is lost). Evaluation
        tiers are then considered strictly from lowest to highest. An entry
        never displaces its own tier: at equal priority the queued FIFO work
        wins and the incoming event is rejected deterministically.
        """
        victim = _first_queued(gen.view_only)
        if victim is None and priority in {
            EventPriority.CATEGORY, EventPriority.PROTECTED, EventPriority.CRITICAL,
        }:
            victim = _first_queued(gen.coalescible, process=True)
        if victim is None and priority in {EventPriority.PROTECTED, EventPriority.CRITICAL}:
            victim = _first_queued(gen.category)
        if victim is None and priority is EventPriority.CRITICAL:
            victim = _first_queued(gen.protected)
        if victim is None:
            return False
        self._evict(gen, victim)
        return True

    def _evict(self, gen: _Generation, victim: _Entry) -> None:
        metrics = gen.metrics
        metrics.evicted_queue += 1
        victim.status = _EVICTED
        gen.live -= 1
        gen.tombstones += 1
        if gen.record_gap(
            victim.entity_id, victim.seq, victim.old_state, victim.new_state
        ):
            self._warn_history_degraded(gen, victim.entity_id)
        if victim.process:
            gen.unprocessed(victim.entity_id, -1)
            self._count_drop(gen, victim.priority, victim.entity_id)
            if (
                victim.priority is EventPriority.COALESCIBLE
                and gen.latest.get(victim.entity_id) is victim
            ):
                if gen.remember_previous(
                    victim.entity_id, victim.previous, victim.seq
                ):
                    self._warn_history_degraded(gen, victim.entity_id)
        else:
            metrics.view_evictions += 1
        if gen.latest.get(victim.entity_id) is victim:
            del gen.latest[victim.entity_id]
        victim.event = victim.old_state = victim.new_state = victim.previous = None
        gen.compact()
        gen.update_retention()

    def _reject(self, gen: _Generation, entry: _Entry) -> None:
        gen.metrics.rejected_queue_full += 1
        if gen.live:
            if gen.record_gap(
                entry.entity_id, entry.seq, entry.old_state, entry.new_state
            ):
                self._warn_history_degraded(gen, entry.entity_id)
        if entry.priority is EventPriority.COALESCIBLE:
            latest = gen.latest.get(entry.entity_id)
            if latest is None or latest.status != _QUEUED:
                if gen.remember_previous(entry.entity_id, entry.previous, entry.seq):
                    self._warn_history_degraded(gen, entry.entity_id)
        self._count_drop(gen, entry.priority, entry.entity_id)
        gen.update_retention()

    def _warn_history_degraded(self, gen: _Generation, entity_id: str) -> None:
        now = time.monotonic()
        if (
            gen.last_history_warning is not None
            and now - gen.last_history_warning < _HISTORY_WARNING_INTERVAL_SECONDS
        ):
            return
        gen.last_history_warning = now
        _LOGGER.warning(
            "HomeIntent history bookkeeping reached its %s-entity bound at %s; "
            "using a conservative event-only view until the pending queue drains",
            MAX_HISTORY_ENTITIES,
            entity_id,
        )

    def _count_drop(self, gen: _Generation, priority: EventPriority, entity_id: str) -> None:
        metrics = gen.metrics
        now = time.monotonic()
        if priority is EventPriority.CRITICAL:
            metrics.dropped_critical += 1
            if (
                gen.last_critical_warning is None
                or now - gen.last_critical_warning >= _DROP_WARNING_INTERVAL_SECONDS
            ):
                gen.last_critical_warning = now
                _LOGGER.error(
                    "SAFETY: HomeIntent dropped a safety-critical state change of %s; "
                    "the event queue holds %s critical events (%s dropped so far)",
                    entity_id, gen.live, metrics.dropped_critical,
                )
            return
        if priority is EventPriority.PROTECTED:
            metrics.dropped_protected += 1
            metrics.dropped_lossless += 1
            if len(gen.unsafe_protected_entities) < MAX_HISTORY_ENTITIES:
                gen.unsafe_protected_entities.add(entity_id)
            else:
                gen.unsafe_all_protected = True
        elif priority is EventPriority.CATEGORY:
            metrics.dropped_category += 1
            metrics.dropped_lossless += 1
        else:
            metrics.dropped_coalescible += 1
        if (
            gen.last_drop_warning is None
            or now - gen.last_drop_warning >= _DROP_WARNING_INTERVAL_SECONDS
        ):
            gen.last_drop_warning = now
            _LOGGER.warning(
                "HomeIntent is behind on state changes; %s events skipped so far "
                "(%s coalescible, %s category, %s protected)",
                metrics.dropped_coalescible + metrics.dropped_lossless,
                metrics.dropped_coalescible, metrics.dropped_category,
                metrics.dropped_protected,
            )

    def _suppress_consumer(
        self,
        gen: _Generation,
        mode: DegradationMode,
        consumer: str,
        priority: EventPriority,
    ) -> None:
        """Count and rate-limit one intentional downstream suppression."""
        metrics = gen.metrics
        if mode is DegradationMode.HISTORY:
            metrics.consumer_suppressed_history += 1
        elif mode is DegradationMode.REGISTRY:
            metrics.consumer_suppressed_registry += 1
        if priority is EventPriority.PROTECTED:
            metrics.suppressed_protected += 1
        elif priority is EventPriority.CATEGORY:
            metrics.suppressed_category += 1
        if consumer == "thermal":
            metrics.suppressed_thermal += 1
        elif consumer == "proactive":
            metrics.suppressed_proactive += 1
        elif consumer == "monitor":
            metrics.suppressed_monitor += 1
        elif consumer == "effect":
            metrics.suppressed_effect += 1
        now = time.monotonic()
        if (
            gen.last_suppression_warning is not None
            and now - gen.last_suppression_warning < _SUPPRESSION_WARNING_INTERVAL_SECONDS
        ):
            return
        gen.last_suppression_warning = now
        _LOGGER.warning(
            "HomeIntent suppressed %s consumer delivery in %s mode "
            "(priority=%s)",
            consumer,
            mode.value,
            priority.value,
        )

    # -- worker ------------------------------------------------------------
    def _owns(self, gen: _Generation) -> bool:
        return gen is self._gen and not gen.stopped

    async def _async_drain(self, gen: _Generation) -> None:
        # The worker belongs to one generation: after a stop (and a restart
        # with a new queue) a worker that outlived its cancellation must not
        # drain the new queue next to the new worker (7.9.5).
        slice_started = time.monotonic()
        while self._owns(gen) and gen.live:
            try:
                entities = build_entity_snapshots(self._hass, self._entry)
            except Exception:  # noqa: BLE001 - the batch stays queued (7.9.6)
                if not await self._async_snapshot_failed(gen):
                    return
                slice_started = time.monotonic()
                continue
            gen.snapshot_failures_in_row = 0
            gen.metrics.snapshot_builds += 1
            # Only now leave the queue: a failed build above lost nothing.
            batch = gen.lease(MAX_BATCH_EVENTS)
            history_degraded = bool(
                batch
                and gen.history_degraded_through
                and batch[0].seq <= gen.history_degraded_through
            )
            protected_view: _HouseView | None = None
            if history_degraded:
                protected_entities = self._interest.protected_view_entities(entities)
                if protected_entities is not None:
                    protected_view = _HouseView.for_batch(
                        protected_entities,
                        batch,
                        gen,
                    )
                # An untracked dropped transition may already be reflected in
                # the fresh house snapshot. Restrict the base to entities for
                # which queued old/new states can rewind the timeline; absence
                # is safer than presenting a future state as historical.
                retained_ids = {
                    entry.entity_id for entry in (*batch, *gen.queued_entries())
                }
                entities = [
                    entity for entity in entities if entity.entity_id in retained_ids
                ]
                gen.metrics.history_degraded_batches += 1
            view = _HouseView.for_batch(entities, batch, gen)
            if batch:
                gen.prune_gaps_before(batch[0].seq)
            for entry in batch:
                if not self._owns(gen):
                    return
                if entry.process:
                    gen.metrics.dequeued += 1
                    current, by_id = view.advance(entry)
                    protected_current: list[EntitySnapshot] | None = None
                    protected_by_id: dict[str, EntitySnapshot] | None = None
                    if protected_view is not None:
                        protected_current, protected_by_id = protected_view.advance(entry)
                    try:
                        event = _evaluated_event(entry)
                        await self._async_process_state_changed(
                            event,
                            current,
                            by_id,
                            mode=(
                                DegradationMode.HISTORY
                                if history_degraded
                                else DegradationMode.NORMAL
                            ),
                            protected_entities=protected_current,
                            protected_by_id=protected_by_id,
                            priority=entry.priority,
                        )
                    except Exception:  # noqa: BLE001 - one event must not stop the worker
                        gen.metrics.evaluation_failures += 1
                        _LOGGER.exception("HomeIntent state change evaluation failed")
                    if not self._owns(gen):
                        return
                    gen.metrics.processed += 1
                    gen.unprocessed(entry.entity_id, -1)
                gen.finish(entry)
                # A burst never monopolizes the loop, yet the worker keeps
                # pace with the events Home Assistant fires meanwhile.
                if time.monotonic() - slice_started >= _YIELD_AFTER_SECONDS:
                    await asyncio.sleep(0)
                    slice_started = time.monotonic()
            oldest = gen.oldest_live_seq()
            if (
                gen.history_degraded_through
                and oldest is not None
                and oldest > gen.history_degraded_through
            ):
                gen.history_degraded_through = 0
            gen.update_retention()
        if self._owns(gen) and not gen.live:
            # No await occurs between the final live check and this cleanup.
            # A callback that ran during processing has already incremented
            # ``live`` and keeps this worker in the loop; a later callback sees
            # the completed task and starts exactly one new worker.
            gen.stable_cleanup()
            self._seen.clear()

    async def _async_snapshot_failed(self, gen: _Generation) -> bool:
        """Back off after a failed snapshot build; ``False`` ends the worker.

        The batch never left the queue. Up to ``SNAPSHOT_MAX_ATTEMPTS``
        builds in a row are tried with a growing pause (never a hot loop);
        then the head batch is evaluated without registry data so critical
        and lossless events still reach their consumers, coalescible ones are
        counted as dropped, and the next batch starts a new series.
        """
        metrics = gen.metrics
        metrics.snapshot_failures += 1
        gen.snapshot_failures_in_row += 1
        failures = gen.snapshot_failures_in_row
        now = time.monotonic()
        if (
            gen.last_snapshot_error is None
            or now - gen.last_snapshot_error >= _SNAPSHOT_ERROR_INTERVAL_SECONDS
        ):
            gen.last_snapshot_error = now
            _LOGGER.exception(
                "HomeIntent could not read the selected entities (attempt %s of %s); "
                "%s state changes stay queued",
                failures, SNAPSHOT_MAX_ATTEMPTS, gen.live,
            )
        delay = min(
            SNAPSHOT_RETRY_MAX_SECONDS,
            SNAPSHOT_RETRY_BASE_SECONDS * (2 ** (failures - 1)),
        )
        await asyncio.sleep(delay)
        if not self._owns(gen):
            return False
        if failures < SNAPSHOT_MAX_ATTEMPTS:
            metrics.snapshot_retries += 1
            return True
        gen.snapshot_failures_in_row = 0
        await self._async_degraded_batch(gen)
        return self._owns(gen)

    async def _async_degraded_batch(self, gen: _Generation) -> None:
        metrics = gen.metrics
        metrics.snapshot_degraded_batches += 1
        metrics.registry_degraded_batches += 1
        batch = gen.lease(MAX_BATCH_EVENTS)
        critical = sum(
            1 for entry in batch
            if entry.process and entry.priority is EventPriority.CRITICAL
        )
        _LOGGER.error(
            "%sHomeIntent evaluates %s state changes without registry data after "
            "%s failed snapshot builds (%s safety-critical)",
            "SAFETY: " if critical else "", len(batch), SNAPSHOT_MAX_ATTEMPTS, critical,
        )
        view = _HouseView.for_batch(_degraded_base(batch), batch, gen, with_queue=False)
        if batch:
            gen.prune_gaps_before(batch[0].seq)
        for entry in batch:
            if not self._owns(gen):
                return
            if entry.process:
                metrics.dequeued += 1
            if entry.process and entry.priority in {
                EventPriority.COALESCIBLE,
                EventPriority.CATEGORY,
            }:
                if self._long_running_enabled() or entry.entity_id in self._long_running:
                    snapshot = snapshot_from_state(entry.entity_id, entry.new_state)
                    event_time = getattr(entry.event, "time_fired", None)
                    if (
                        snapshot is not None
                        and isinstance(event_time, datetime)
                        and event_time.tzinfo is not None
                    ):
                        self._sync_long_running_interval(
                            gen, snapshot, entry.old_state, event_time
                        )
                metrics.discarded_registry += 1
                proactive = self._runtime_data.proactive_context
                if (
                    proactive is not None
                    and getattr(proactive, "enabled", None) is not False
                ):
                    self._suppress_consumer(
                        gen, DegradationMode.REGISTRY, "proactive", entry.priority
                    )
                if self._interest.category_relevant(
                    entry.entity_id, entry.old_state, entry.new_state
                ):
                    self._suppress_consumer(
                        gen, DegradationMode.REGISTRY, "category", entry.priority
                    )
                gen.unprocessed(entry.entity_id, -1)
            elif entry.process:
                current, by_id = view.advance(entry)
                try:
                    await self._async_process_state_changed(
                        _evaluated_event(entry),
                        current,
                        by_id,
                        mode=DegradationMode.REGISTRY,
                        priority=entry.priority,
                    )
                except Exception:  # noqa: BLE001 - one event must not stop the worker
                    metrics.evaluation_failures += 1
                    _LOGGER.exception("HomeIntent state change evaluation failed")
                if not self._owns(gen):
                    return
                metrics.processed += 1
                gen.unprocessed(entry.entity_id, -1)
            gen.finish(entry)
            await asyncio.sleep(0)
        gen.update_retention()

    async def async_handle_state_changed(self, raw_event: Any) -> None:
        data = getattr(raw_event, "data", {})
        if not isinstance(data, dict):
            return
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str) or data.get("new_state") is None:
            return
        self._gen.metrics.snapshot_builds += 1
        view = _HouseView(
            build_entity_snapshots(self._hass, self._entry),
            [(0, 0, entity_id, data.get("old_state"), data.get("new_state"))],
        )
        entities, by_id = view.advance_to(0)
        await self._async_process_state_changed(raw_event, entities, by_id)

    async def _async_process_state_changed(
        self,
        raw_event: Any,
        entities: list[EntitySnapshot],
        by_id: dict[str, EntitySnapshot],
        *,
        mode: DegradationMode = DegradationMode.NORMAL,
        protected_entities: list[EntitySnapshot] | None = None,
        protected_by_id: dict[str, EntitySnapshot] | None = None,
        priority: EventPriority = EventPriority.CATEGORY,
    ) -> None:
        """Evaluate one event with an explicit data-quality mode."""
        data = getattr(raw_event, "data", {})
        if not isinstance(data, dict):
            return
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        if not isinstance(entity_id, str) or new_state is None:
            return
        entity = by_id.get(entity_id)
        if entity is None:
            return
        gen = self._gen
        metrics = gen.metrics
        event_time = getattr(raw_event, "time_fired", None) or dt_util.utcnow()
        if not isinstance(event_time, datetime) or event_time.tzinfo is None:
            event_time = dt_util.utcnow()
        long_interval: _LongRunningInterval | None = None
        if self._long_running_enabled() or entity_id in self._long_running:
            long_interval = self._sync_long_running_interval(
                gen, entity, old_state, event_time
            )
        consumer_entities = protected_entities if protected_entities is not None else entities
        consumer_by_id = protected_by_id if protected_by_id is not None else by_id

        thermal_tracker = self._runtime_data.thermal_tracker
        thermal_ids = self._interest.thermal_entity_ids
        thermal_event = (
            thermal_tracker is not None
            and getattr(thermal_tracker, "has_active_cycle", True)
            and (thermal_ids is None or entity_id in thermal_ids)
        )
        if thermal_event and thermal_tracker is not None:
            thermal_safe = (
                mode is DegradationMode.NORMAL
                or (
                    mode is DegradationMode.HISTORY
                    and thermal_ids is not None
                    and protected_entities is not None
                    and not gen.unsafe_all_protected
                    and not (thermal_ids & gen.unsafe_protected_entities)
                    and thermal_ids <= consumer_by_id.keys()
                )
            )
            if thermal_safe:
                metrics.consumer_delivered += 1
                metrics.delivered_thermal += 1
                try:
                    await thermal_tracker.async_observe_states(
                        tuple(consumer_entities), occurred_at=event_time
                    )
                except Exception:  # noqa: BLE001 - learning must not break HA event flow
                    _LOGGER.exception("HomeIntent thermal experience update failed")
            else:
                self._suppress_consumer(gen, mode, "thermal", priority)

        effects = self._runtime_data.effect_monitor
        watched_effects = getattr(effects, "watched_entity_ids", frozenset())
        effect_event = (
            not isinstance(watched_effects, frozenset)
            or entity_id in watched_effects
        )
        if effect_event:
            # Effect fulfilment needs only the event entity and is safe in
            # both degradation modes.
            metrics.consumer_delivered += 1
            metrics.delivered_effect += 1
        effects.observe(entity_id, entity.state)
        previous = getattr(old_state, "state", None)
        monitor_runtime = self._runtime_data.monitor_runtime
        monitor_store = (
            getattr(monitor_runtime, "store", None)
            if monitor_runtime is not None
            else None
        )
        monitor_store_loaded = getattr(monitor_store, "loaded", None)
        monitor_interest_undeclared = (
            monitor_store is None
            or not isinstance(monitor_store_loaded, bool)
            or not monitor_store_loaded
        )
        watched_person_ids = getattr(
            monitor_store, "watched_person_entity_ids", frozenset()
        )
        watched_nobody_ids = getattr(
            monitor_store, "watched_nobody_home_person_ids", frozenset()
        )
        nobody_home_uses_household = bool(
            getattr(monitor_store, "nobody_home_uses_household", False)
        )
        person_watch_sets_valid = isinstance(
            watched_person_ids, frozenset
        ) and isinstance(watched_nobody_ids, frozenset)
        person_monitored = (
            monitor_interest_undeclared
            or not person_watch_sets_valid
            or entity_id in watched_person_ids
            or entity_id in watched_nobody_ids
            or nobody_home_uses_household
        )
        if (
            monitor_runtime is not None
            and entity.domain == "person"
            and person_monitored
            and isinstance(previous, str)
            and previous != entity.state
        ):
            needs_house = bool(
                getattr(
                    monitor_store,
                    "person_transition_needs_house_context",
                    lambda _id: True,
                )(entity.entity_id)
            )
            needs_snapshot = bool(
                getattr(
                    monitor_store,
                    "person_transition_needs_snapshot",
                    lambda _id: True,
                )(entity.entity_id)
            )
            person_history_unsafe = False
            person_context_incomplete = False
            if mode is DegradationMode.HISTORY:
                monitored_person_ids: set[str] = set()
                person_watch_overflow = not person_watch_sets_valid
                if person_watch_sets_valid:
                    watched_total = len(watched_person_ids) + len(watched_nobody_ids)
                    if watched_total > MAX_CONSUMER_WATCH_ENTITIES:
                        person_watch_overflow = True
                    else:
                        monitored_person_ids.update(watched_person_ids)
                        monitored_person_ids.update(watched_nobody_ids)
                if nobody_home_uses_household and not person_watch_overflow:
                    user_contexts = getattr(monitor_runtime, "user_contexts", None)
                    household = getattr(user_contexts, "household", None)
                    household_ids = getattr(household, "person_entity_ids", None)
                    if isinstance(household_ids, tuple):
                        if (
                            len(monitored_person_ids) + len(household_ids)
                            > MAX_CONSUMER_WATCH_ENTITIES
                        ):
                            person_watch_overflow = True
                        else:
                            monitored_person_ids.update(
                                item for item in household_ids if isinstance(item, str)
                            )
                    elif protected_entities is not None:
                        for item in consumer_entities:
                            if item.domain != "person":
                                continue
                            monitored_person_ids.add(item.entity_id)
                            if (
                                len(monitored_person_ids)
                                > MAX_CONSUMER_WATCH_ENTITIES
                            ):
                                person_watch_overflow = True
                                break
                    else:
                        person_watch_overflow = True
                person_history_unsafe = gen.unsafe_all_protected or bool(
                    monitored_person_ids & gen.unsafe_protected_entities
                )
                person_context_incomplete = person_watch_overflow or not (
                    monitored_person_ids <= consumer_by_id.keys()
                )
            monitor_safe = not (
                (mode is DegradationMode.HISTORY and needs_house)
                or (mode is DegradationMode.REGISTRY and needs_snapshot)
                or (
                    mode is DegradationMode.HISTORY
                    and (person_history_unsafe or person_context_incomplete)
                )
                or (mode is DegradationMode.HISTORY and protected_entities is None)
            )
            if monitor_safe:
                metrics.consumer_delivered += 1
                metrics.delivered_monitor += 1
                try:
                    event_context = getattr(raw_event, "context", None)
                    context_id = getattr(event_context, "id", None)
                    await monitor_runtime.async_process_person_transition(
                        entity.entity_id,
                        previous,
                        entity.state,
                        occurred_at=event_time,
                        occurrence_id=(
                            context_id if isinstance(context_id, str) else None
                        ),
                        entities=tuple(consumer_entities),
                    )
                except Exception:  # noqa: BLE001 - monitor failure must not break HA event flow
                    _LOGGER.exception("HomeIntent monitor goal evaluation failed")
            else:
                self._suppress_consumer(gen, mode, "monitor", priority)
        if (
            monitor_runtime is not None
            and entity.domain == "sensor"
            and (
                monitor_interest_undeclared
                or entity_id
                in getattr(
                    monitor_store, "watched_value_entity_ids", frozenset()
                )
            )
            and isinstance(previous, str)
            and previous != entity.state
        ):
            try:
                value = float(entity.state)
            except ValueError:
                value = None
            if value is not None:
                if mode is DegradationMode.HISTORY and (
                    gen.unsafe_all_protected
                    or entity_id in gen.unsafe_protected_entities
                ):
                    self._suppress_consumer(gen, mode, "monitor", priority)
                else:
                    metrics.consumer_delivered += 1
                    metrics.delivered_monitor += 1
                    try:
                        # The rate decision uses Recorder history plus this
                        # exact event value; registry metadata is optional.
                        await monitor_runtime.async_process_value_change(
                            entity.entity_id,
                            value,
                            occurred_at=event_time,
                            entity=entity,
                        )
                    except Exception:  # noqa: BLE001 - monitor failure must not break HA event flow
                        _LOGGER.exception("HomeIntent value-change monitor evaluation failed")
        proactive = self._runtime_data.proactive_context
        if (
            proactive is not None
            and getattr(proactive, "enabled", None) is not False
        ):
            proactive_safe = (
                mode is DegradationMode.NORMAL
                or (
                    not bool(getattr(proactive, "requires_complete_house_view", True))
                    and (
                        mode is not DegradationMode.REGISTRY
                        or not bool(getattr(proactive, "requires_registry_data", True))
                    )
                )
            )
            if proactive_safe:
                metrics.consumer_delivered += 1
                metrics.delivered_proactive += 1
                try:
                    await proactive.async_observe_state(
                        entity,
                        previous if isinstance(previous, str) else None,
                        tuple(entities),
                    )
                except Exception:  # noqa: BLE001 - V12 must never break HA event flow
                    _LOGGER.exception("HomeIntent proactive context evaluation failed")
            else:
                self._suppress_consumer(gen, mode, "proactive", priority)
        categories = self._configured_categories()
        if mode is not DegradationMode.NORMAL:
            # These rules depend only on the event entity. Occupancy, heating
            # and routine rules would need a complete historical house view;
            # suppressing them is safer than inventing facts.
            safe_categories = categories & {
                "safety", "safety_alarm", "device_unavailable", "long_running_state"
            }
            if categories - safe_categories and self._interest.category_relevant(
                entity_id, old_state, new_state
            ):
                self._suppress_consumer(gen, mode, "category", priority)
            categories = safe_categories
        if not categories:
            return
        people = _people_home(entities, by_id)
        event = normalize_state_change(
            entity,
            previous if isinstance(previous, str) else None,
            occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
            person_ids=people,
            occupied=bool(people),
            operating_mode="home" if people else "away",
        )
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        if len(self._seen) > 4096:
            self._seen = {event.event_id}
        long_state_seconds = (
            {entity_id: long_interval.threshold_seconds}
            if (
                "long_running_state" in categories
                and long_interval is not None
                and activity_marker(entity) == long_interval.marker
            )
            else None
        )
        long_elapsed_seconds = (
            {entity_id: max(
                0.0, (_as_utc(event_time) - long_interval.started_at).total_seconds()
            )}
            if long_state_seconds is not None and long_interval is not None
            else None
        )
        situations = self._runtime_data.situation_evaluator.evaluate(
            event,
            entities,
            long_state_seconds=long_state_seconds,
            long_state_elapsed_seconds=long_elapsed_seconds,
        )
        routine_assessment = None
        if bool(self._entry.options.get(CONF_ROUTINE_DETECTION_ENABLED, False)):
            stats = self._runtime_data.routine_statistics.setdefault(
                entity_id,
                RoutineStatistics(
                    minimum_observations=int(
                        self._entry.options.get(CONF_ROUTINE_MIN_OBSERVATIONS, 10)
                    ),
                    anomaly_threshold=float(
                        self._entry.options.get(CONF_ANOMALY_THRESHOLD_PERCENT, 5)
                    ) / 100.0,
                ),
            )
            assessment = stats.assess(event)
            routine_assessment = assessment
            stats.observe(event)
            if assessment.unusual and "routine_anomaly" in categories:
                routine_situation = Situation(
                    f"routine:{event.event_id}",
                    "routine_anomaly",
                    SituationSeverity.NOTICE,
                    "Ein lokales Zeitmuster weicht ab.",
                    (),
                    event.event_id,
                )
                decision = self._decision_engine.decide(
                    event,
                    routine_situation,
                    entities=entities,
                    options=self._entry.options,
                    now=event.occurred_at,
                )
                if decision.deliver:
                    await self._signal(
                        event.entity_id,
                        "routine_anomaly",
                        "Ein lokales Zeitmuster weicht ab.",
                        (
                            assessment.observed,
                            assessment.normal,
                            f"{assessment.observation_count} Beobachtungen",
                            assessment.uncertainty,
                        ),
                        event.state,
                        critical=False,
                        mode=decision.mode,
                        action=decision.action,
                    )
        for situation in situations:
            if situation.category not in categories and not (
                "safety" in categories and situation.severity.value == "critical"
            ):
                continue
            if situation.category == "long_running_state":
                token = (
                    long_interval.marker,
                    long_interval.serial,
                ) if long_interval is not None else (entity.state, entity.last_changed)
                if not self._long_running_scheduler.remember_notification(
                    entity_id, token
                ):
                    continue
            facts = situation.facts
            if routine_assessment is not None:
                facts = (
                    *facts,
                    "Historie: "
                    f"{routine_assessment.normal}; "
                    f"{routine_assessment.observation_count} Beobachtungen; "
                    f"{routine_assessment.uncertainty}",
                )
            action = (
                ServiceCallPlan(
                    "homeassistant",
                    situation.suggested_operation,
                    situation.suggested_entity_id,
                    {},
                )
                if situation.suggested_entity_id is not None
                and situation.suggested_operation == "turn_off"
                else None
            )
            decision = self._decision_engine.decide(
                event,
                situation,
                entities=entities,
                options=self._entry.options,
                proposed_action=action,
                requested_mode=AgentMode.ASK if action is not None else AgentMode.INFORM,
                now=event.occurred_at,
            )
            if not decision.deliver:
                continue
            await self._signal(
                event.entity_id,
                situation.category,
                situation.summary,
                facts,
                event.state,
                critical=situation.severity.value == "critical",
                mode=decision.mode,
                action=decision.action,
            )

    async def async_handle_expected_effect_expired(
        self, effect: ExpectedEffect
    ) -> None:
        """Report a missing observable effect without retrying the action."""
        proactive = self._runtime_data.proactive_context
        if proactive is not None:
            try:
                name = next(
                    (item.friendly_name for item in build_entity_snapshots(self._hass, self._entry)
                     if item.entity_id == effect.entity_id),
                    effect.entity_id,
                )
                await proactive.async_report_effect_anomaly(
                    effect.entity_id, effect.operator_id, name
                )
            except Exception:  # noqa: BLE001 - V12 must never break HA event flow
                _LOGGER.exception("HomeIntent proactive effect report failed")
        if "expected_effect_missing" not in self._configured_categories():
            return
        entities = build_entity_snapshots(self._hass, self._entry)
        entity = next(
            (item for item in entities if item.entity_id == effect.entity_id), None
        )
        if entity is None or entity.state in {"unknown", "unavailable"}:
            return
        facts = [
            f"Erwarteter Zustand: {effect.expected_state}",
            f"Beobachteter Zustand: {spoken_state(entity.state)}",
            "Die Aktion wird nicht automatisch wiederholt.",
        ]
        summary = "Die erwartete Gerätewirkung wurde nicht beobachtet."
        predictive = self._runtime_data.predictive_house
        if predictive is not None and effect.operator_id is not None:
            timing = predictive.effect_timing_model(
                effect.operator_id, effect.entity_id
            )
            if timing is not None:
                elapsed = max(
                    0.0, (dt_util.utcnow() - effect.registered_at).total_seconds()
                )
                anomaly = evaluate_latency_anomaly(timing, elapsed)
                if anomaly.anomalous:
                    summary = "Das Gerät reagiert ungewöhnlich langsam."
                    facts.insert(
                        0,
                        f"Normaler Median: {timing.median_seconds:.1f} s; "
                        f"robuste Anomaliegrenze: {anomaly.threshold_seconds:.1f} s",
                    )
        await self._signal(
            effect.entity_id,
            "expected_effect_missing",
            summary,
            tuple(facts),
            entity.state,
            critical=False,
            mode=AgentMode.INFORM,
        )

    async def _signal(
        self,
        entity_id: str,
        category: str,
        summary: str,
        facts: tuple[str, ...],
        expected_state: str,
        *,
        critical: bool,
        mode: AgentMode = AgentMode.INFORM,
        action: ServiceCallPlan | None = None,
    ) -> None:
        agent = self._runtime_data.proactive_agent
        if agent is None:
            return
        factual_result = summary
        if facts:
            factual_result += " " + "; ".join(facts[:4]) + "."
        raw_style = self._entry.options.get(CONF_PERSONA_STYLE, "neutral")
        try:
            style = PersonaStyle(str(raw_style))
        except ValueError:
            style = PersonaStyle.NEUTRAL
        raw_banter = self._entry.options.get(CONF_BANTER_LEVEL, 0)
        banter = raw_banter if isinstance(raw_banter, int) else 0
        message = GermanResponseRealizer().realize(
            ResponsePlan(
                DialogAct.WARN if critical else DialogAct.INFORM,
                factual_result,
                urgency=Urgency.CRITICAL if critical else Urgency.NORMAL,
                safety_level="critical" if critical else "low",
                banter_level=max(0, min(3, banter)),
                category=category,
            ),
            style=style,
        )
        try:
            payload: dict[str, object] = {
                "rule_id": f"situation:{category}",
                "message": message,
                "mode": mode.value,
                "source_entity_id": entity_id,
                "expected_source_state": expected_state,
                "dedupe_key": entity_id,
                "safety_critical": critical,
            }
            if action is not None:
                payload.update(
                    {
                        "action_domain": action.domain,
                        "action_service": action.service,
                        "action_entity_id": action.entity_id,
                        "action_data": action.data,
                    }
                )
            await agent.async_signal(payload)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid normalized situation %s", category, exc_info=True)

    def _configured_categories(self) -> frozenset[str]:
        raw = self._entry.options.get(CONF_AGENT_EVENT_CATEGORIES, "")
        try:
            return parse_event_categories(raw)
        except ValueError:
            _LOGGER.warning("Invalid configured HomeIntent event categories")
            return frozenset()


class _HouseView:
    """The selected entities as they were when each queued event fired.

    ``build_entity_snapshots()`` returns the house *now*, i.e. after every
    queued event. 7.9.4 evaluated a whole batch against that one later
    snapshot: a door that opened and closed again within a batch was seen
    closed twice, a person who left and came back made no transition, the
    thermal tracker saw future temperatures (7.9.5). The view first rewinds
    every entity with a later change to the ``old_state`` of its earliest
    one, then ``advance_to()`` applies each change in order.

    A change is ``(rewind_seq, apply_seq, entity_id, old_state, new_state)``:
    a queued event has both at its own sequence number; a gap of changes
    given up under overload (7.9.6) rewinds at its first and applies its
    last state at its last sequence number, so an earlier event never sees
    a later state. History-only entries (merged evaluations) are applied
    like any other change.

    Only entities in the snapshot take part: an entity that is no longer
    selected (or no longer exists) is not evaluated, as before.
    """

    def __init__(
        self,
        entities: list[EntitySnapshot],
        changes: Iterable[tuple[int, int, str, Any, Any]],
        *,
        apply_until: int | None = None,
    ) -> None:
        self._base = {item.entity_id: item for item in entities}
        self._by_id = _ViewDict(self._base)
        self._by_id.person_ids = tuple(
            entity_id for entity_id, item in self._base.items() if item.domain == "person"
        )
        earliest: dict[str, tuple[int, Any]] = {}
        steps: list[tuple[int, str, Any]] = []
        for rewind_seq, apply_seq, entity_id, old_state, new_state in changes:
            known = earliest.get(entity_id)
            if known is None or rewind_seq < known[0]:
                earliest[entity_id] = (rewind_seq, old_state)
            if apply_until is None or apply_seq <= apply_until:
                steps.append((apply_seq, entity_id, new_state))
        for entity_id, (_seq, old_state) in earliest.items():
            base = self._base.get(entity_id)
            if base is None:
                continue
            if old_state is None:
                # Created by the event: it did not exist before.
                self._by_id.pop(entity_id, None)
            else:
                self._by_id[entity_id] = snapshot_at_state(base, old_state)
        steps.sort(key=lambda step: step[0])
        self._steps = steps
        self._next = 0

    @classmethod
    def for_batch(
        cls, entities: list[EntitySnapshot], batch: list[_Entry], gen: _Generation,
        *, with_queue: bool = True,
    ) -> "_HouseView":
        """The view for ``batch`` against a snapshot taken right before it.

        Entries still queued fired after the batch; the snapshot already
        contains their states, so they are rewound as well. Gaps entirely
        before the batch are already part of the snapshot and are dropped.
        """
        first = batch[0].seq if batch else 0
        last = batch[-1].seq if batch else 0
        changes: list[tuple[int, int, str, Any, Any]] = [
            (entry.seq, entry.seq, entry.entity_id, entry.old_state, entry.new_state)
            for entry in batch
        ]
        if with_queue:
            changes.extend(
                (entry.seq, entry.seq, entry.entity_id, entry.old_state, entry.new_state)
                for entry in gen.queued_entries()
            )
            changes.extend(
                (gap.first_seq, gap.last_seq, entity_id, gap.old_state, gap.new_state)
                for entity_id, gap in gen.gaps.items()
                if gap.last_seq >= first
            )
        return cls(entities, changes, apply_until=last)

    def advance(self, entry: _Entry) -> tuple[list[EntitySnapshot], dict[str, EntitySnapshot]]:
        return self.advance_to(entry.seq)

    def advance_to(
        self, seq: int
    ) -> tuple[list[EntitySnapshot], dict[str, EntitySnapshot]]:
        """Apply every change up to ``seq``; return the house right after it.

        The returned dict is the view itself and changes with the next
        ``advance_to()``; the worker evaluates one event at a time.
        """
        steps = self._steps
        while self._next < len(steps) and steps[self._next][0] <= seq:
            _apply_seq, entity_id, new_state = steps[self._next]
            self._next += 1
            base = self._base.get(entity_id)
            if base is not None and new_state is not None:
                self._by_id[entity_id] = snapshot_at_state(base, new_state)
        return list(self._by_id.values()), self._by_id


class _ViewDict(dict[str, EntitySnapshot]):
    """The view's entities by id, plus the person ids among them.

    Persons at home are read for every evaluated event; with the ids at hand
    that costs O(persons) instead of a scan of the whole house (7.9.6).
    """

    person_ids: tuple[str, ...] = ()


def _people_home(entities: list[EntitySnapshot], by_id: dict[str, EntitySnapshot]) -> tuple[str, ...]:
    person_ids = by_id.person_ids if isinstance(by_id, _ViewDict) else None
    if person_ids is None:
        return tuple(
            item.entity_id for item in entities if item.domain == "person" and item.state == "home"
        )
    people: list[str] = []
    for entity_id in person_ids:
        item = by_id.get(entity_id)
        if item is not None and item.state == "home":
            people.append(entity_id)
    return tuple(people)


def _first_queued(order: deque[_Entry], *, process: bool | None = None) -> _Entry | None:
    """The oldest entry of ``order`` still queued (and, if given, with
    ``process``); stale heads are discarded on the way."""
    while order and order[0].status != _QUEUED:
        order.popleft()
    if process is None:
        return order[0] if order else None
    for entry in order:
        if entry.status == _QUEUED and entry.process is process:
            return entry
    return None


def _evaluated_event(entry: _Entry) -> Any:
    """The event to evaluate: the original, or merged with an earlier one."""
    if entry.previous is _event_data(entry.event).get("old_state"):
        return entry.event
    return _MergedEvent(entry.event, entry.previous)


def _degraded_base(batch: list[_Entry]) -> list[EntitySnapshot]:
    """Snapshots from the batch's own states, without registry data."""
    latest: dict[str, Any] = {}
    for entry in batch:
        latest[entry.entity_id] = entry.new_state
    snapshots: list[EntitySnapshot] = []
    for entity_id, state in latest.items():
        snapshot = snapshot_from_state(entity_id, state)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def _event_data(raw_event: Any) -> dict[str, Any]:
    data = getattr(raw_event, "data", None)
    return data if isinstance(data, dict) else {}


def _as_utc(value: datetime) -> datetime:
    """Normalize scheduler timestamps while preserving aware HA instants."""
    if value.tzinfo is None:
        raise ValueError("long-running-state timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _schedule_later(
    delay: float, callback_fn: Any, *args: Any
) -> asyncio.TimerHandle:
    """Small seam for deterministic scheduler tests."""
    return asyncio.get_running_loop().call_later(delay, callback_fn, *args)
