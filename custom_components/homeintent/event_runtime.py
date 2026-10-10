"""Home Assistant state-change bridge into normalized local situations."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
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
from .event_interest import EventInterestIndex
from .event_priority import EVICTION_ORDER, PRIORITY_RANK, EventPriority
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
# rank decides what goes first (7.9.6, ranks split in 7.9.7): an incoming
# event displaces only queued events of a strictly lower rank.
MAX_PENDING_EVENTS = 4096
# Events taken from the queue per snapshot build. The snapshot is the house
# as it is now; each event is evaluated against the house as it was when the
# event fired (7.9.5), derived from the queued events' own old/new states.
# Every batch rewinds all still queued changes, so a full queue costs
# O(queue / batch) rewinds of the whole queue; 7.9.6 takes 1024 instead of
# 256 (the view is exact for any batch size, a snapshot per batch remains).
MAX_BATCH_EVENTS = 1024
# Evicted entries left in the queue before it is rebuilt (they hold no event
# or state any more, 7.9.7: a fixed bound instead of one growing with the
# queue).
MAX_TOMBSTONES = 1024
# Entities whose given-up changes are remembered for the house view, and
# merged evaluations whose first previous state is carried (7.9.7). Beyond
# that the exact history is given up explicitly (``history_degraded``)
# instead of growing with the number of distinct entities.
MAX_GAPS = 4096
MAX_CARRIED = 4096
# Upper bound of everything one generation retains that refers to events or
# states: queue (live + tombstones), ``latest`` (queued + one batch in
# flight), gaps and carried previous states. The helper deques only hold
# entries of the queue.
MAX_RETAINED_ENTRIES = (
    MAX_PENDING_EVENTS + MAX_TOMBSTONES
    + MAX_PENDING_EVENTS + MAX_BATCH_EVENTS
    + MAX_GAPS + MAX_CARRIED
)
_DROP_WARNING_INTERVAL_SECONDS = 60.0
# The worker hands the event loop back after this much work in one go.
_YIELD_AFTER_SECONDS = 0.02
# A failed snapshot build keeps the batch queued and is retried with an
# exponential backoff (7.9.6); after ``SNAPSHOT_MAX_ATTEMPTS`` failures in a
# row the batch is evaluated without registry data (all but coalescible
# events) instead of waiting forever.
SNAPSHOT_MAX_ATTEMPTS = 5
SNAPSHOT_RETRY_BASE_SECONDS = 0.05
SNAPSHOT_RETRY_MAX_SECONDS = 5.0
_SNAPSHOT_ERROR_INTERVAL_SECONDS = 60.0


@dataclass
class EventRuntimeMetrics:
    """Counters of one runtime start (7.9.6); a restart starts from zero.

    ``received`` = ``filtered_unselected`` + ``filtered_no_interest`` +
    ``queued`` + events rejected by a full queue (``dropped_*`` of the
    incoming event). ``filtered_by_category`` is the part of
    ``filtered_no_interest`` filtered while agent event categories are
    configured (7.9.7). ``coalesced`` counts evaluations merged into a later
    event of the same entity; ``dropped_*`` counts evaluations lost to the
    queue bound, per rank; ``dropped_lossless`` is the sum of the protected,
    routine and category drops (the 7.9.6 rank they were split from).
    ``view_evictions`` counts history-only entries given up under overload
    (no evaluation is lost by that). ``history_degraded`` counts given-up
    changes whose exact history no longer fit the bookkeeping bound
    (``MAX_GAPS``/``MAX_CARRIED``); ``max_retained_entries`` is the
    high-water mark of the retained references (``MAX_RETAINED_ENTRIES``);
    ``cleanup_runs`` counts releases of the bookkeeping after a full drain.
    """

    received: int = 0
    filtered_unselected: int = 0
    filtered_no_interest: int = 0
    filtered_by_category: int = 0
    queued: int = 0
    processed: int = 0
    coalesced: int = 0
    dropped_coalescible: int = 0
    dropped_category: int = 0
    dropped_routine: int = 0
    dropped_protected: int = 0
    dropped_lossless: int = 0
    dropped_critical: int = 0
    view_evictions: int = 0
    snapshot_builds: int = 0
    snapshot_failures: int = 0
    snapshot_retries: int = 0
    snapshot_degraded_batches: int = 0
    worker_starts: int = 0
    max_queue_depth: int = 0
    history_degraded: int = 0
    history_degraded_batches: int = 0
    max_retained_entries: int = 0
    cleanup_runs: int = 0

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

    def release(self) -> None:
        """Drop the references to the event and its states."""
        self.event = self.old_state = self.new_state = self.previous = None


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

    Besides the queue (global order) every evictable rank has its own deque
    (oldest first) so a full queue finds the lowest-ranked victim without a
    scan. Entries leave the helper deques lazily (stale heads) and on
    ``compact()``; after a full drain ``release_drained()`` empties every
    structure (7.9.7) - the metrics stay.
    """

    def __init__(self) -> None:
        self.queue: deque[_Entry] = deque()
        self.live = 0
        self.tombstones = 0
        self.seq = 0
        self.by_priority: dict[EventPriority, deque[_Entry]] = {
            priority: deque() for priority in EVICTION_ORDER
        }
        self.view_only: deque[_Entry] = deque()
        self.latest: dict[str, _Entry] = {}
        self.carry_previous: dict[str, Any] = {}
        self.unprocessed_by_entity: dict[str, int] = {}
        self.gaps: dict[str, _Gap] = {}
        # Changes given up without a gap (``MAX_GAPS``) lie between these
        # sequence numbers (0: the history is exact).
        self.history_degraded_from = 0
        self.history_degraded_until = 0
        self.gaps_pruned_at: int | None = None
        self.metrics = EventRuntimeMetrics()
        self.stopped = False
        self.created_queue = False
        self.snapshot_failures_in_row = 0
        self.last_drop_warning: float | None = None
        self.last_critical_warning: float | None = None
        self.last_protected_warning: float | None = None
        self.last_degraded_warning: float | None = None
        self.last_snapshot_error: float | None = None

    # Per-rank deques by name (tests, diagnostics).
    @property
    def coalescible(self) -> deque[_Entry]:
        return self.by_priority[EventPriority.COALESCIBLE]

    @property
    def category(self) -> deque[_Entry]:
        return self.by_priority[EventPriority.CATEGORY]

    @property
    def routine(self) -> deque[_Entry]:
        return self.by_priority[EventPriority.ROUTINE]

    @property
    def protected(self) -> deque[_Entry]:
        return self.by_priority[EventPriority.PROTECTED]

    def oldest_live_seq(self) -> int | None:
        while self.queue and self.queue[0].status != _QUEUED:
            self.queue.popleft()
            self.tombstones -= 1
        return self.queue[0].seq if self.queue else None

    def take(self, limit: int, until_seq: int | None = None) -> list[_Entry]:
        """Up to ``limit`` queued entries in order (none after ``until_seq``)."""
        batch: list[_Entry] = []
        while self.queue and len(batch) < limit:
            entry = self.queue[0]
            if entry.status != _QUEUED:
                self.queue.popleft()
                self.tombstones -= 1
                continue
            if until_seq is not None and entry.seq > until_seq:
                break
            self.queue.popleft()
            entry.status = _TAKEN
            self.live -= 1
            batch.append(entry)
        for order in (*self.by_priority.values(), self.view_only):
            while order and order[0].status != _QUEUED:
                order.popleft()
        # Gaps stay until a view built after them no longer needs them
        # (``_HouseView.for_batch``): this batch may still predate them.
        return batch

    def finish(self, entry: _Entry) -> None:
        """``entry`` is evaluated (or given up): release what refers to it."""
        entry.status = _DONE
        if self.latest.get(entry.entity_id) is entry:
            del self.latest[entry.entity_id]

    def queued_entries(self) -> list[_Entry]:
        return [entry for entry in self.queue if entry.status == _QUEUED]

    def unprocessed(self, entity_id: str, delta: int) -> None:
        count = self.unprocessed_by_entity.get(entity_id, 0) + delta
        if count > 0:
            self.unprocessed_by_entity[entity_id] = count
        else:
            self.unprocessed_by_entity.pop(entity_id, None)

    def record_gap(self, entity_id: str, seq: int, old_state: Any, new_state: Any) -> bool:
        """Remember a given-up change for the house view of queued events.

        ``False`` when the change no longer fits ``MAX_GAPS``: the exact
        history is given up up to ``seq`` (``history_degraded_until``).
        """
        oldest = self.oldest_live_seq()
        if oldest is None:
            # Nothing queued precedes it: every later view already sees it.
            return True
        gap = self.gaps.get(entity_id)
        if gap is not None and gap.last_seq >= oldest:
            gap.extend(seq, old_state, new_state)
            return True
        if gap is None and len(self.gaps) >= MAX_GAPS and self.gaps_pruned_at != oldest:
            # Gaps entirely before the oldest queued event are no longer
            # needed; prune once per oldest event, not per change.
            self.gaps_pruned_at = oldest
            for stale in [key for key, item in self.gaps.items() if item.last_seq < oldest]:
                del self.gaps[stale]
        if gap is None and len(self.gaps) >= MAX_GAPS:
            if not self.history_degraded_until:
                self.history_degraded_from = seq
            self.history_degraded_until = max(self.history_degraded_until, seq)
            self.metrics.history_degraded += 1
            return False
        self.gaps[entity_id] = _Gap(seq, old_state, new_state)
        return True

    def carry(self, entity_id: str, previous: Any) -> None:
        """Carry an unevaluated previous state to the entity's next event."""
        if entity_id in self.carry_previous:
            return
        if len(self.carry_previous) >= MAX_CARRIED:
            # The next evaluation reports its own old state instead.
            self.metrics.history_degraded += 1
            return
        self.carry_previous[entity_id] = previous

    def note_retained(self) -> None:
        retained = (
            len(self.queue) + len(self.latest) + len(self.gaps) + len(self.carry_previous)
        )
        if retained > self.metrics.max_retained_entries:
            self.metrics.max_retained_entries = retained

    def compact(self) -> None:
        if self.tombstones > MAX_TOMBSTONES:
            self.queue = deque(entry for entry in self.queue if entry.status == _QUEUED)
            for priority, order in self.by_priority.items():
                self.by_priority[priority] = deque(
                    entry for entry in order if entry.status == _QUEUED
                )
            self.view_only = deque(entry for entry in self.view_only if entry.status == _QUEUED)
            self.tombstones = 0

    def release(self) -> None:
        """Empty every structure; the metrics stay readable."""
        self.queue = deque()
        self.by_priority = {priority: deque() for priority in EVICTION_ORDER}
        self.view_only = deque()
        self.latest = {}
        self.carry_previous = {}
        self.unprocessed_by_entity = {}
        self.gaps = {}
        self.live = 0
        self.tombstones = 0
        self.history_degraded_from = 0
        self.history_degraded_until = 0
        self.gaps_pruned_at = None

    def release_drained(self) -> bool:
        """After a full drain nothing earlier is needed any more (7.9.7).

        Synchronous, called by the worker of this generation right after it
        saw ``live == 0``: no event can arrive in between. Gaps and carried
        previous states only describe changes before every future event (the
        next worker builds a new snapshot), ``latest`` and the helper deques
        only refer to finished entries.
        """
        if self.live or self.stopped:
            return False
        self.release()
        self.metrics.cleanup_runs += 1
        return True

    def retention(self) -> dict[str, int]:
        """What this generation retains right now (diagnostics, tests)."""
        return {
            "retained_entries": len(self.queue),
            "retained_index": (
                sum(len(order) for order in self.by_priority.values())
                + len(self.view_only)
            ),
            "retained_latest": len(self.latest),
            "retained_gaps": len(self.gaps),
            "retained_carry": len(self.carry_previous),
            "retained_tombstones": self.tombstones,
            "retained_unprocessed": len(self.unprocessed_by_entity),
            "history_degraded_active": int(self.history_degraded_until > 0),
            "max_retained_entries": self.metrics.max_retained_entries,
            "retained_limit": MAX_RETAINED_ENTRIES,
        }


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

    def retention(self) -> dict[str, int]:
        """What the current generation retains right now (7.9.7)."""
        return self._gen.retention()

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
            generation.release()

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
        priority = self._interest.classify(entity_id, old_state, new_state)
        if priority is None:
            metrics.filtered_no_interest += 1
            if self._interest.category_active:
                metrics.filtered_by_category += 1
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
        order = gen.by_priority.get(priority)
        if order is not None:
            order.append(entry)
        metrics.queued += 1
        if gen.live > metrics.max_queue_depth:
            metrics.max_queue_depth = gen.live
        gen.note_retained()
        if self._worker is None or self._worker.done():
            metrics.worker_starts += 1
            # Not eager: the bus callback stays cheap and never re-enters.
            self._worker = self._hass.async_create_background_task(
                self._async_drain(gen),
                "HomeIntent situation events",
                eager_start=False,
            )

    def _make_room(self, gen: _Generation, priority: EventPriority) -> bool:
        """Evict one entry of lower rank for an incoming ``priority`` event.

        History-only entries go first (no evaluation is lost), then the
        oldest evaluation of the lowest rank below the incoming one:
        coalescible, category, routine and - for a critical event only, as
        the last resort - protected (7.9.7). Nothing displaces a critical
        event; nothing displaces an event of the same rank.
        """
        victim = _first_queued(gen.view_only)
        if victim is None:
            rank = PRIORITY_RANK[priority]
            for lower in EVICTION_ORDER:
                if PRIORITY_RANK[lower] >= rank:
                    break
                victim = _first_queued(gen.by_priority[lower], process=True)
                if victim is not None:
                    break
        if victim is None:
            return False
        self._evict(gen, victim)
        return True

    def _evict(self, gen: _Generation, victim: _Entry) -> None:
        metrics = gen.metrics
        victim.status = _EVICTED
        gen.live -= 1
        gen.tombstones += 1
        if not gen.record_gap(victim.entity_id, victim.seq, victim.old_state, victim.new_state):
            self._warn_degraded(gen)
        if gen.latest.get(victim.entity_id) is victim:
            del gen.latest[victim.entity_id]
            if victim.process and victim.priority is EventPriority.COALESCIBLE:
                gen.carry(victim.entity_id, victim.previous)
        if victim.process:
            gen.unprocessed(victim.entity_id, -1)
            self._count_drop(gen, victim.priority, victim.entity_id)
        else:
            metrics.view_evictions += 1
        victim.release()
        gen.compact()

    def _reject(self, gen: _Generation, entry: _Entry) -> None:
        if not gen.record_gap(entry.entity_id, entry.seq, entry.old_state, entry.new_state):
            self._warn_degraded(gen)
        if entry.priority is EventPriority.COALESCIBLE:
            latest = gen.latest.get(entry.entity_id)
            if latest is None or latest.status != _QUEUED:
                gen.carry(entry.entity_id, entry.previous)
        self._count_drop(gen, entry.priority, entry.entity_id)
        entry.release()
        gen.note_retained()

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
            if (
                gen.last_protected_warning is None
                or now - gen.last_protected_warning >= _DROP_WARNING_INTERVAL_SECONDS
            ):
                gen.last_protected_warning = now
                _LOGGER.error(
                    "HomeIntent dropped a watched state change of %s (expected effect, "
                    "monitor goal or thermal cycle) for a safety-critical event; "
                    "%s watched changes dropped so far",
                    entity_id, metrics.dropped_protected,
                )
            return
        if priority is EventPriority.ROUTINE:
            metrics.dropped_routine += 1
            metrics.dropped_lossless += 1
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
                "(%s coalescible, %s category, %s routine)",
                metrics.dropped_coalescible + metrics.dropped_category
                + metrics.dropped_routine,
                metrics.dropped_coalescible, metrics.dropped_category,
                metrics.dropped_routine,
            )

    def _warn_degraded(self, gen: _Generation) -> None:
        now = time.monotonic()
        if (
            gen.last_degraded_warning is None
            or now - gen.last_degraded_warning >= _DROP_WARNING_INTERVAL_SECONDS
        ):
            gen.last_degraded_warning = now
            _LOGGER.warning(
                "HomeIntent gave up the exact house history of %s skipped state "
                "changes (more than %s entities); events queued before them are "
                "evaluated without the context it can no longer reconstruct",
                gen.metrics.history_degraded, MAX_GAPS,
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
            # Events before a change whose history no longer fit the bound
            # get a view of what is known exactly, in batches of their own
            # (7.9.7); later events see the snapshot as before.
            oldest = gen.oldest_live_seq()
            partial = oldest is not None and oldest < gen.history_degraded_until
            if not partial:
                gen.history_degraded_from = gen.history_degraded_until = 0
            # Only now leave the queue: a failed build above lost nothing.
            batch = gen.take(
                MAX_BATCH_EVENTS,
                until_seq=gen.history_degraded_until - 1 if partial else None,
            )
            if partial:
                gen.metrics.history_degraded_batches += 1
            view = _HouseView.for_batch(entities, batch, gen, known_only=partial)
            for entry in batch:
                if not self._owns(gen):
                    return
                if entry.process:
                    current, by_id = view.advance(entry)
                    try:
                        if partial:
                            await self._async_process_state_changed(
                                _evaluated_event(entry), current, by_id, degraded=True,
                            )
                        else:
                            await self._async_process_state_changed(
                                _evaluated_event(entry), current, by_id
                            )
                    except Exception:  # noqa: BLE001 - one event must not stop the worker
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
        if self._owns(gen):
            # Nothing queued and no await since the check: release the
            # bookkeeping now, not only at unload (7.9.7).
            gen.release_drained()

    async def _async_snapshot_failed(self, gen: _Generation) -> bool:
        """Back off after a failed snapshot build; ``False`` ends the worker.

        The batch never left the queue. Up to ``SNAPSHOT_MAX_ATTEMPTS``
        builds in a row are tried with a growing pause (never a hot loop);
        then the head batch is evaluated without registry data so every
        event but a coalescible one still reaches its consumers (coalescible
        ones are counted as dropped), and the next batch starts a new series.
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
            if entry.process and entry.priority is EventPriority.COALESCIBLE:
                gen.unprocessed(entry.entity_id, -1)
                self._count_drop(gen, entry.priority, entry.entity_id)
            elif entry.process:
                current, by_id = view.advance(entry)
                try:
                    await self._async_process_state_changed(
                        _evaluated_event(entry), current, by_id, degraded=True
                    )
                except Exception:  # noqa: BLE001 - one event must not stop the worker
                    _LOGGER.exception("HomeIntent state change evaluation failed")
                if not self._owns(gen):
                    return
                metrics.processed += 1
                gen.unprocessed(entry.entity_id, -1)
            gen.finish(entry)
            await asyncio.sleep(0)

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
        degraded: bool = False,
    ) -> None:
        """Evaluate one event; ``degraded``: the view is incomplete.

        Inactive consumers are skipped before any per-event work (7.9.6):
        the thermal tracker without an active cycle, a disabled V12 context.
        A degraded view (no registry data after failed snapshots, or only the
        exactly known entities after the history bound was hit, 7.9.7) skips
        the thermal tracker - it would read missing sensors as a removed
        measurement source - and reports presence as unknown instead of
        "nobody home" from persons that are merely missing.
        """
        data = getattr(raw_event, "data", {})
        if not isinstance(data, dict):
            return
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        if not isinstance(entity_id, str) or new_state is None:
            return
        thermal_tracker = self._runtime_data.thermal_tracker
        if (
            thermal_tracker is not None
            and not degraded
            and getattr(thermal_tracker, "has_active_cycle", True)
        ):
            try:
                await thermal_tracker.async_observe_states(
                    tuple(entities),
                    occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
                )
            except Exception:  # noqa: BLE001 - learning must not break HA event flow
                _LOGGER.exception("HomeIntent thermal experience update failed")
        entity = by_id.get(entity_id)
        if entity is None:
            return
        self._runtime_data.effect_monitor.observe(entity_id, entity.state)
        previous = getattr(old_state, "state", None)
        monitor_runtime = self._runtime_data.monitor_runtime
        if (
            monitor_runtime is not None
            and entity.domain == "person"
            and isinstance(previous, str)
            and previous != entity.state
        ):
            try:
                event_context = getattr(raw_event, "context", None)
                context_id = getattr(event_context, "id", None)
                await monitor_runtime.async_process_person_transition(
                    entity.entity_id,
                    previous,
                    entity.state,
                    occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
                    occurrence_id=context_id if isinstance(context_id, str) else None,
                )
            except Exception:  # noqa: BLE001 - monitor failure must not break HA event flow
                _LOGGER.exception("HomeIntent monitor goal evaluation failed")
        if (
            monitor_runtime is not None
            and entity.domain == "sensor"
            and isinstance(previous, str)
            and previous != entity.state
        ):
            try:
                value = float(entity.state)
            except ValueError:
                value = None
            if value is not None:
                try:
                    # Changes by an amount (7.9 W3) are HomeIntent's own monitors.
                    await monitor_runtime.async_process_value_change(
                        entity.entity_id, value,
                        occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
                    )
                except Exception:  # noqa: BLE001 - monitor failure must not break HA event flow
                    _LOGGER.exception("HomeIntent value-change monitor evaluation failed")
        proactive = self._runtime_data.proactive_context
        if proactive is not None and getattr(proactive, "enabled", None) is not False:
            try:
                await proactive.async_observe_state(
                    entity,
                    previous if isinstance(previous, str) else None,
                    tuple(entities),
                )
            except Exception:  # noqa: BLE001 - V12 must never break HA event flow
                _LOGGER.exception("HomeIntent proactive context evaluation failed")
        categories = self._configured_categories()
        if not categories:
            return
        people = _people_home(entities, by_id)
        event = normalize_state_change(
            entity,
            previous if isinstance(previous, str) else None,
            occurred_at=getattr(raw_event, "time_fired", None) or dt_util.utcnow(),
            person_ids=people,
            occupied=None if degraded else bool(people),
            operating_mode="unknown" if degraded else "home" if people else "away",
        )
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        if len(self._seen) > 4096:
            self._seen = {event.event_id}
        situations = self._runtime_data.situation_evaluator.evaluate(event, entities)
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
        uncertain_from: int | None = None,
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
        for entity_id, (seq, old_state) in earliest.items():
            base = self._base.get(entity_id)
            if base is None:
                continue
            if old_state is None or (uncertain_from is not None and seq > uncertain_from):
                # Created by the event: it did not exist before. Or its state
                # before ``seq`` is not known exactly (degraded history): it
                # appears with its own change.
                self._by_id.pop(entity_id, None)
            else:
                self._by_id[entity_id] = snapshot_at_state(base, old_state)
        steps.sort(key=lambda step: step[0])
        self._steps = steps
        self._next = 0

    @classmethod
    def for_batch(
        cls, entities: list[EntitySnapshot], batch: list[_Entry], gen: _Generation,
        *, with_queue: bool = True, known_only: bool = False,
    ) -> "_HouseView":
        """The view for ``batch`` against a snapshot taken right before it.

        Entries still queued fired after the batch; the snapshot already
        contains their states, so they are rewound as well. Gaps entirely
        before the batch are already part of the snapshot and are dropped.

        ``known_only`` (history degraded, 7.9.7): changes after the batch
        were given up without a gap, so any other entity may show a later
        state. Only entities with a change in the batch, the queue or a gap
        take part; the rest of the house is left out rather than shown from
        the future. An entity whose earliest known change comes after the
        first change given up without a gap is left out until that change:
        its ``old_state`` may already contain a given-up later state.
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
        uncertain_from: int | None = None
        if known_only:
            known = {change[2] for change in changes}
            entities = [item for item in entities if item.entity_id in known]
            uncertain_from = gen.history_degraded_from
        return cls(entities, changes, apply_until=last, uncertain_from=uncertain_from)

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
