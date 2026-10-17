"""Bounded queue, lease and history state for one event-runtime generation."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from .event_priority import EventPriority
from .event_runtime_metrics import EventRuntimeMetrics


_QUEUED = 0
_LEASED = 1
_EVICTED = 2
_DONE = 3


class _Entry:
    """One accepted state change in global order.

    ``process`` false keeps an entry only for an exact historical house view;
    its evaluation has been merged into a later event of the same entity.
    """

    __slots__ = (
        "seq", "event", "entity_id", "old_state", "new_state", "priority",
        "previous", "process", "status",
    )

    def __init__(
        self,
        seq: int,
        event: Any,
        entity_id: str,
        old_state: Any,
        new_state: Any,
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
    """Bounded history of one entity's transitions discarded under overload."""

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


class _Generation:
    """Queue and lease bookkeeping owned by one runtime start."""

    def __init__(self, max_history_entities: int = 4096) -> None:
        self.max_history_entities = max_history_entities
        self.queue: deque[_Entry] = deque()
        self.leased: deque[_Entry] = deque()
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
        self.worker_failures_in_row = 0
        self.worker_failure_signature: str | None = None
        self.rapid_worker_failures: deque[float] = deque()
        self.worker_circuit_open = False
        self.worker_next_retry_at: float | None = None
        self.worker_suspended = False
        # A control signal only: queue acceptance never wakes an open circuit.
        self.worker_wakeup = asyncio.Event()
        self.suppressed_worker_logs = 0
        self.last_worker_status_log: float | None = None
        self.last_drop_warning: float | None = None
        self.last_critical_warning: float | None = None
        self.last_snapshot_error: float | None = None
        self.last_history_warning: float | None = None
        self.last_suppression_warning: float | None = None
        self.history_degraded_through = 0
        self.unsafe_protected_entities: set[str] = set()
        self.unsafe_all_protected = False

    def oldest_live_seq(self) -> int | None:
        while self.queue and self.queue[0].status != _QUEUED:
            self.queue.popleft()
            self.tombstones -= 1
        leased_seq = self.leased[0].seq if self.leased else None
        queued_seq = self.queue[0].seq if self.queue else None
        if leased_seq is None:
            return queued_seq
        if queued_seq is None:
            return leased_seq
        return min(leased_seq, queued_seq)

    def lease(self, limit: int) -> list[_Entry]:
        """Lease one FIFO batch without ceasing to account it as live work."""
        if self.leased:
            raise RuntimeError("event generation already has a leased batch")
        batch: list[_Entry] = []
        while self.queue and len(batch) < limit:
            entry = self.queue.popleft()
            if entry.status != _QUEUED:
                self.tombstones -= 1
                continue
            entry.status = _LEASED
            batch.append(entry)
            self.leased.append(entry)
        while self.coalescible and self.coalescible[0].status != _QUEUED:
            self.coalescible.popleft()
        while self.category and self.category[0].status != _QUEUED:
            self.category.popleft()
        while self.protected and self.protected[0].status != _QUEUED:
            self.protected.popleft()
        while self.view_only and self.view_only[0].status != _QUEUED:
            self.view_only.popleft()
        return batch

    def rollback_lease(self) -> int:
        """Return every unfinished leased row to the FIFO head exactly once."""
        if not self.leased:
            return 0
        restored = list(self.leased)
        self.leased.clear()
        for entry in restored:
            if entry.status == _LEASED:
                entry.status = _QUEUED
        self.queue = deque((*restored, *self.queue))
        self._rebuild_priority_refs()
        self.update_retention()
        return len(restored)

    def _rebuild_priority_refs(self) -> None:
        self.coalescible = deque()
        self.view_only = deque()
        self.category = deque()
        self.protected = deque()
        for entry in self.queue:
            if entry.status != _QUEUED:
                continue
            if not entry.process:
                self.view_only.append(entry)
            elif entry.priority is EventPriority.COALESCIBLE:
                self.coalescible.append(entry)
            elif entry.priority is EventPriority.CATEGORY:
                self.category.append(entry)
            elif entry.priority is EventPriority.PROTECTED:
                self.protected.append(entry)

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
        if gap is None and len(self.gaps) >= self.max_history_entities:
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
        if len(self.carry_previous) >= self.max_history_entities:
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
        if not self.leased or self.leased[0] is not entry:
            raise RuntimeError("event batch finalized outside lease order")
        self.leased.popleft()
        self.live -= 1
        entry.status = _DONE
        if self.latest.get(entry.entity_id) is entry:
            del self.latest[entry.entity_id]
        entry.event = entry.old_state = entry.new_state = entry.previous = None

    def stable_cleanup(self) -> None:
        """Release every transient structure once no live work remains."""
        if self.live or self.leased:
            return
        self.queue.clear()
        self.leased.clear()
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

    def discard_pending(self) -> None:
        """Release queued and leased work when its generation is stopped."""
        for entry in (*self.leased, *self.queue):
            entry.status = _DONE
            entry.event = entry.old_state = entry.new_state = entry.previous = None
        self.queue.clear()
        self.leased.clear()
        self.coalescible.clear()
        self.view_only.clear()
        self.category.clear()
        self.protected.clear()
        self.latest.clear()
        self.carry_previous.clear()
        self.unprocessed_by_entity.clear()
        self.gaps.clear()
        self.unsafe_protected_entities.clear()
        self.unsafe_all_protected = False
        self.live = 0
        self.tombstones = 0
        self.history_degraded_through = 0
        self.update_retention()

    def update_retention(self) -> None:
        """Publish low-cardinality gauges without retaining metric objects."""
        metrics = self.metrics
        metrics.retained_live = self.live
        metrics.retained_leased = len(self.leased)
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
            + metrics.retained_leased
            + metrics.retained_latest
            + metrics.retained_gaps
            + metrics.retained_carry_previous
            + metrics.retained_priority_refs
            + metrics.retained_unprocessed
            + metrics.retained_unsafe_protected
        )
        metrics.retained_total_all = (
            metrics.retained_total
            + metrics.retained_long_running
            + metrics.retained_long_running_heap
        )
        metrics.max_retained_total = max(
            metrics.max_retained_total, metrics.retained_total
        )

    def compact(self) -> None:
        if self.tombstones > max(self.live, 1024):
            self.queue = deque(entry for entry in self.queue if entry.status == _QUEUED)
            self.tombstones = 0

    def prune_gaps_before(self, first_seq: int) -> None:
        """Commit history cleanup only after every batch view was built."""
        for entity_id in [
            key for key, gap in self.gaps.items() if gap.last_seq < first_seq
        ]:
            del self.gaps[entity_id]
