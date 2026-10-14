"""Home Assistant state-change bridge into normalized local situations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_AGENT_LONG_RUNNING_MINUTES,
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


class DegradationMode(StrEnum):
    """Data quality of the historical view supplied to an evaluation."""

    NORMAL = "normal"
    HISTORY = "history_degraded"
    REGISTRY = "registry_degraded"


@dataclass
class EventRuntimeMetrics:
    """Counters of one integration-runtime generation.

    ``async_start()`` creates a fresh generation. An automatic worker
    recovery stays in that generation and increments ``worker_restarts``.

    ``received`` = ``filtered_unselected`` + ``filtered_no_interest`` +
    ``queued`` + ``rejected_queue_full``. ``queued`` includes accepted
    coalesced events. ``coalesced`` counts evaluations merged into a later
    event of the same entity; ``dropped_*`` counts evaluations lost to the
    queue bound through rejection or eviction, per priority. ``evicted_queue``
    distinguishes the latter. ``dropped_lossless`` remains the compatibility
    aggregate of category + protected drops. ``view_evictions`` counts history-only
    entries given up under overload (no evaluation is lost by that).
    ``processed`` means that event-level runtime dispatch ended (including an
    intentional consumer suppression); it does not claim delivery to any
    downstream consumer.  Registry-discarded lower tiers are recorded only
    in ``discarded_registry``. Consumer delivery and intentional suppression
    have separate counters below.
    """

    received: int = 0
    filtered_unselected: int = 0
    filtered_no_interest: int = 0
    filtered_by_interest: int = 0
    filtered_by_category: int = 0
    filtered_by_proactive: int = 0
    filtered_no_consumer: int = 0
    queued: int = 0
    rejected_queue_full: int = 0
    evicted_queue: int = 0
    dequeued: int = 0
    processed: int = 0
    discarded_registry: int = 0
    evaluation_failures: int = 0
    coalesced: int = 0
    dropped_coalescible: int = 0
    dropped_category: int = 0
    dropped_protected: int = 0
    dropped_lossless: int = 0
    dropped_critical: int = 0
    view_evictions: int = 0
    snapshot_builds: int = 0
    snapshot_failures: int = 0
    snapshot_retries: int = 0
    snapshot_degraded_batches: int = 0
    registry_degraded_batches: int = 0
    history_degraded: int = 0
    history_degraded_events: int = 0
    history_degraded_batches: int = 0
    consumer_delivered: int = 0
    consumer_suppressed_history: int = 0
    consumer_suppressed_registry: int = 0
    delivered_effect: int = 0
    delivered_monitor: int = 0
    delivered_thermal: int = 0
    delivered_proactive: int = 0
    suppressed_protected: int = 0
    suppressed_category: int = 0
    suppressed_effect: int = 0
    suppressed_monitor: int = 0
    suppressed_thermal: int = 0
    suppressed_proactive: int = 0
    worker_starts: int = 0
    worker_restarts: int = 0
    max_queue_depth: int = 0
    retained_live: int = 0
    retained_latest: int = 0
    retained_gaps: int = 0
    retained_carry_previous: int = 0
    retained_priority_refs: int = 0
    retained_unprocessed: int = 0
    retained_unsafe_protected: int = 0
    retained_tombstones: int = 0
    retained_total: int = 0
    max_retained_total: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(asdict(self))


_QUEUED = 0
_TAKEN = 1
_EVICTED = 2
_DONE = 3


class _Entry:
    """One accepted state change in global order.

    ``process`` False: kept only so the house view stays exact - its
    evaluation was merged into a later event of the same entity.
    ``previous`` is the state the evaluation reports as the previous one
    (the first ``old_state`` of merged events).
    """

    __slots__ = (
        "seq", "event", "entity_id", "old_state", "new_state", "priority",
        "previous", "process", "status",
    )

    def __init__(
        self, seq: int, event: Any, entity_id: str, old_state: Any, new_state: Any,
        priority: EventPriority,
    ) -> None:
        self.seq = seq
        self.event = event
        self.entity_id = entity_id
        self.old_state = old_state
        self.new_state = new_state
        self.priority = priority
        self.previous = old_state
        self.process = True
        self.status = _QUEUED


class _Gap:
    """State changes of one entity given up under overload (7.9.6).

    The house view shows the entity at ``old_state`` before ``first_seq``
    and at ``new_state`` from ``last_seq`` on - never a later state for an
    earlier event; between the two it may show the older one.
    """

    __slots__ = ("first_seq", "old_state", "last_seq", "new_state")

    def __init__(self, seq: int, old_state: Any, new_state: Any) -> None:
        self.first_seq = seq
        self.old_state = old_state
        self.last_seq = seq
        self.new_state = new_state

    def extend(self, seq: int, old_state: Any, new_state: Any) -> None:
        if seq < self.first_seq:
            self.first_seq, self.old_state = seq, old_state
        if seq > self.last_seq:
            self.last_seq, self.new_state = seq, new_state


class _MergedEvent:
    """The evaluation of merged events: first ``old_state``, last event."""

    __slots__ = ("data", "time_fired", "context", "origin")

    def __init__(self, event: Any, previous: Any) -> None:
        data = _event_data(event)
        self.data = {**data, "old_state": previous}
        self.time_fired = getattr(event, "time_fired", None)
        self.context = getattr(event, "context", None)
        self.origin = event


class _Generation:
    """Queue, bookkeeping and metrics of one runtime start.

    A worker belongs to exactly one generation; after a stop (and a restart
    with a new generation) an old worker can neither drain nor count into
    the new one (7.9.5/7.9.6).
    """

    def __init__(self) -> None:
        self.queue: deque[_Entry] = deque()
        self.live = 0
        self.tombstones = 0
        self.seq = 0
        self.coalescible: deque[_Entry] = deque()
        self.view_only: deque[_Entry] = deque()
        self.category: deque[_Entry] = deque()
        self.protected: deque[_Entry] = deque()
        self.latest: dict[str, _Entry] = {}
        self.carry_previous: dict[str, Any] = {}
        self.unprocessed_by_entity: dict[str, int] = {}
        self.gaps: dict[str, _Gap] = {}
        self.metrics = EventRuntimeMetrics()
        self.stopped = False
        self.created_queue = False
        self.snapshot_failures_in_row = 0
        self.last_drop_warning: float | None = None
        self.last_critical_warning: float | None = None
        self.last_snapshot_error: float | None = None
        self.last_history_warning: float | None = None
        self.last_suppression_warning: float | None = None
        # Non-zero means an auxiliary overflow made whole-house history
        # inexact through this sequence. Affected batches use a conservative
        # event-entity-only view; a scalar replaces an unbounded entity set.
        self.history_degraded_through = 0
        # A protected transition can only be lost to critical pressure.  Its
        # entity remains unsafe until drain; ids are bounded by the history
        # limit and then collapse to one conservative overflow flag.
        self.unsafe_protected_entities: set[str] = set()
        self.unsafe_all_protected = False

    def oldest_live_seq(self) -> int | None:
        while self.queue and self.queue[0].status != _QUEUED:
            self.queue.popleft()
            self.tombstones -= 1
        return self.queue[0].seq if self.queue else None

    def take(self, limit: int) -> list[_Entry]:
        batch: list[_Entry] = []
        while self.queue and len(batch) < limit:
            entry = self.queue.popleft()
            if entry.status != _QUEUED:
                self.tombstones -= 1
                continue
            entry.status = _TAKEN
            self.live -= 1
            batch.append(entry)
        while self.coalescible and self.coalescible[0].status != _QUEUED:
            self.coalescible.popleft()
        while self.category and self.category[0].status != _QUEUED:
            self.category.popleft()
        while self.protected and self.protected[0].status != _QUEUED:
            self.protected.popleft()
        while self.view_only and self.view_only[0].status != _QUEUED:
            self.view_only.popleft()
        # Gaps stay until a view built after them no longer needs them
        # (``_HouseView.for_batch``): this batch may still predate them.
        return batch

    def queued_entries(self) -> list[_Entry]:
        return [entry for entry in self.queue if entry.status == _QUEUED]

    def unprocessed(self, entity_id: str, delta: int) -> None:
        count = self.unprocessed_by_entity.get(entity_id, 0) + delta
        if count > 0:
            self.unprocessed_by_entity[entity_id] = count
        else:
            self.unprocessed_by_entity.pop(entity_id, None)

    def record_gap(
        self, entity_id: str, seq: int, old_state: Any, new_state: Any
    ) -> bool:
        """Remember a dropped transition; return true on degradation entry."""
        if self.history_degraded_through:
            self.history_degraded_through = max(self.history_degraded_through, seq)
            self.metrics.history_degraded_events += 1
            return False
        oldest = self.oldest_live_seq()
        gap = self.gaps.get(entity_id)
        if gap is None and len(self.gaps) >= MAX_HISTORY_ENTITIES:
            return self.degrade_history(seq)
        if gap is None or oldest is None or gap.last_seq < oldest:
            self.gaps[entity_id] = _Gap(seq, old_state, new_state)
        else:
            gap.extend(seq, old_state, new_state)
        return False

    def remember_previous(self, entity_id: str, previous: Any, seq: int) -> bool:
        """Keep a coalesced chain's first state within the auxiliary bound."""
        if self.history_degraded_through:
            self.history_degraded_through = max(self.history_degraded_through, seq)
            return False
        if entity_id in self.carry_previous:
            return False
        if len(self.carry_previous) >= MAX_HISTORY_ENTITIES:
            return self.degrade_history(seq)
        self.carry_previous[entity_id] = previous
        return False

    def degrade_history(self, seq: int) -> bool:
        """Enter bounded safe mode instead of retaining more entity history."""
        entered = not self.history_degraded_through
        self.history_degraded_through = max(self.history_degraded_through, seq)
        self.gaps.clear()
        self.carry_previous.clear()
        self.metrics.history_degraded_events += 1
        if entered:
            self.metrics.history_degraded += 1
        return entered

    def finish(self, entry: _Entry) -> None:
        """Release one completed entry and any exact latest pointer to it."""
        entry.status = _DONE
        if self.latest.get(entry.entity_id) is entry:
            del self.latest[entry.entity_id]
        entry.event = entry.old_state = entry.new_state = entry.previous = None

    def stable_cleanup(self) -> None:
        """Release every transient structure once no live work remains."""
        if self.live:
            return
        self.queue.clear()
        self.coalescible.clear()
        self.view_only.clear()
        self.category.clear()
        self.protected.clear()
        self.latest.clear()
        self.carry_previous.clear()
        self.unprocessed_by_entity.clear()
        self.gaps.clear()
        self.tombstones = 0
        self.history_degraded_through = 0
        self.unsafe_protected_entities.clear()
        self.unsafe_all_protected = False
        self.snapshot_failures_in_row = 0
        self.update_retention()

    def update_retention(self) -> None:
        """Publish low-cardinality gauges without retaining metric objects."""
        metrics = self.metrics
        metrics.retained_live = self.live
        metrics.retained_latest = len(self.latest)
        metrics.retained_gaps = len(self.gaps)
        metrics.retained_carry_previous = len(self.carry_previous)
        metrics.retained_priority_refs = (
            len(self.coalescible) + len(self.view_only)
            + len(self.category) + len(self.protected)
        )
        metrics.retained_unprocessed = len(self.unprocessed_by_entity)
        metrics.retained_unsafe_protected = (
            len(self.unsafe_protected_entities) + int(self.unsafe_all_protected)
        )
        metrics.retained_tombstones = self.tombstones
        metrics.retained_total = (
            len(self.queue)
            + metrics.retained_latest
            + metrics.retained_gaps
            + metrics.retained_carry_previous
            + metrics.retained_priority_refs
            + metrics.retained_unprocessed
            + metrics.retained_unsafe_protected
        )
        metrics.max_retained_total = max(
            metrics.max_retained_total, metrics.retained_total
        )

    def compact(self) -> None:
        if self.tombstones > max(self.live, 1024):
            self.queue = deque(entry for entry in self.queue if entry.status == _QUEUED)
            self.tombstones = 0


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
        self._long_running_notified: dict[str, tuple[str, Any]] = {}
        self._gen = _Generation()
        self._gen.stopped = True
        self._worker: asyncio.Task[None] | None = None
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

    # -- lifecycle ---------------------------------------------------------
    def async_start(self) -> Any:
        effect_monitor = self._runtime_data.effect_monitor
        effect_monitor.set_expired_handler(self.async_handle_expected_effect_expired)
        set_probe = getattr(effect_monitor, "set_pending_probe", None)
        if callable(set_probe):
            set_probe(self.has_pending)
        old = self._gen
        old.stopped = True
        self._gen = _Generation()
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

        def stop() -> None:
            unlisten()
            generation.stopped = True
            if self._gen is generation:
                self._stopped = True
                self._interest.unbind()
                worker, self._worker = self._worker, None
                if worker is not None and not worker.done():
                    worker.cancel()
                if callable(set_probe):
                    set_probe(None)
                self._runtime_data.effect_monitor.set_expired_handler(None)
            # Release the queue; the metrics stay readable.
            generation.queue = deque()
            generation.coalescible = deque()
            generation.view_only = deque()
            generation.category = deque()
            generation.protected = deque()
            generation.latest = {}
            generation.carry_previous = {}
            generation.unprocessed_by_entity = {}
            generation.gaps = {}
            generation.unsafe_protected_entities = set()
            generation.unsafe_all_protected = False
            generation.live = 0
            generation.tombstones = 0
            generation.history_degraded_through = 0
            generation.update_retention()

        return stop

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

    def _start_worker(self, gen: _Generation, *, restart: bool = False) -> None:
        """Start the sole worker; ``restart`` is only unexpected recovery."""
        if not self._owns(gen):
            return
        gen.metrics.worker_starts += 1
        if restart:
            gen.metrics.worker_restarts += 1
        # Not eager: the bus callback stays cheap and never re-enters.
        worker = self._hass.async_create_background_task(
            self._async_drain(gen),
            "HomeIntent situation events",
            eager_start=False,
        )
        self._worker = worker
        worker.add_done_callback(lambda task: self._worker_finished(gen, task))

    @callback
    def _worker_finished(
        self, gen: _Generation, task: asyncio.Task[None]
    ) -> None:
        """Retrieve failures and recover only when unfinished work remains."""
        failure: BaseException | None = None
        if not task.cancelled():
            try:
                failure = task.exception()
            except asyncio.CancelledError:
                failure = None
        if self._worker is not task:
            return
        self._worker = None
        if not self._owns(gen) or not gen.live:
            return
        if failure is not None:
            _LOGGER.error(
                "HomeIntent event worker stopped with %s pending events; restarting",
                gen.live,
                exc_info=(type(failure), failure, failure.__traceback__),
            )
        else:
            _LOGGER.warning(
                "HomeIntent event worker ended with %s pending events; restarting",
                gen.live,
            )
        self._start_worker(gen, restart=True)

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
            batch = gen.take(MAX_BATCH_EVENTS)
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
        batch = gen.take(MAX_BATCH_EVENTS)
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
        for entry in batch:
            if not self._owns(gen):
                return
            if entry.process:
                metrics.dequeued += 1
            if entry.process and entry.priority in {
                EventPriority.COALESCIBLE,
                EventPriority.CATEGORY,
            }:
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
        raw_long_minutes = self._entry.options.get(CONF_AGENT_LONG_RUNNING_MINUTES, 0)
        long_minutes = (
            raw_long_minutes
            if isinstance(raw_long_minutes, (int, float)) and raw_long_minutes > 0
            else 0
        )
        long_state_seconds = (
            {entity_id: float(long_minutes) * 60.0}
            if "long_running_state" in categories and long_minutes
            else None
        )
        situations = self._runtime_data.situation_evaluator.evaluate(
            event, entities, long_state_seconds=long_state_seconds
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
                token = (entity.state, entity.last_changed)
                if self._long_running_notified.get(entity_id) == token:
                    continue
                self._long_running_notified[entity_id] = token
                if len(self._long_running_notified) > 4096:
                    self._long_running_notified = {entity_id: token}
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
        for entity_id in [key for key, gap in gen.gaps.items() if gap.last_seq < first]:
            del gen.gaps[entity_id]
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
