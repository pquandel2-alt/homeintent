"""Bounded central scheduler for long-running entity activity."""

from __future__ import annotations

import asyncio
import heapq
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from .agent_config_validation import parse_event_categories, parse_long_running_minutes
from .const import CONF_AGENT_EVENT_CATEGORIES, CONF_AGENT_LONG_RUNNING_MINUTES
from .entities import EntitySnapshot
from .event_runtime_queue import _Generation
from .long_running import activity_marker, has_reliable_last_changed


@dataclass
class LongRunningInterval:
    """One observed active interval represented in the deadline heap."""

    marker: str
    started_at: datetime
    due_at: datetime
    serial: int
    threshold_seconds: float
    fired: bool = False


class LongRunningScheduler:
    """Own one bounded heap and timer for all selected long-running states."""

    def __init__(
        self,
        *,
        hass: Any,
        entry: Any,
        owns: Callable[[_Generation], bool],
        is_selected: Callable[[str], bool],
        enqueue: Callable[[Any], None],
        snapshot_from_state: Callable[[str, Any], EntitySnapshot | None],
        utcnow: Callable[[], datetime],
        schedule_later: Callable[..., asyncio.TimerHandle],
        capacity: Callable[[], int],
        logger: logging.Logger,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._owns = owns
        self._is_selected = is_selected
        self._enqueue = enqueue
        self._snapshot_from_state = snapshot_from_state
        self._utcnow = utcnow
        self._schedule_later = schedule_later
        self._capacity = capacity
        self._logger = logger
        self.notified: dict[str, tuple[str, Any]] = {}
        self.intervals: dict[str, LongRunningInterval] = {}
        self.heap: list[tuple[float, int, str]] = []
        self._serial = 0
        self._timer: asyncio.TimerHandle | None = None
        self._init_task: asyncio.Task[None] | None = None
        self._last_capacity_warning: float | None = None

    def minutes(self) -> int:
        try:
            return parse_long_running_minutes(
                self._entry.options.get(CONF_AGENT_LONG_RUNNING_MINUTES, 0)
            )
        except ValueError as err:
            self._logger.warning(
                "Invalid long-running-state threshold; disabling it: %s", err
            )
            return 0

    def enabled(self) -> bool:
        try:
            categories = parse_event_categories(
                self._entry.options.get(CONF_AGENT_EVENT_CATEGORIES, "")
            )
        except ValueError:
            return False
        return "long_running_state" in categories

    def start(self, gen: _Generation) -> None:
        """Start bounded state seeding when the option and category are active."""
        if self.minutes() <= 0 or not self.enabled():
            return
        self._init_task = self._hass.async_create_background_task(
            self.initialize(gen),
            "HomeIntent long-running-state initialization",
            eager_start=False,
        )

    async def initialize(self, gen: _Generation) -> None:
        """Seed deadlines from HA state in bounded chunks, without registry I/O."""
        for attempt in range(1, 4):
            try:
                await self._initialize_once(gen)
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - heterogeneous HA state boundary
                gen.metrics.long_running_scheduler_failures += 1
                if not self._owns(gen):
                    return
                if attempt == 3:
                    self._logger.exception(
                        "HomeIntent long-running-state initialization failed "
                        "after %s attempts",
                        attempt,
                    )
                    return
                self._logger.warning(
                    "HomeIntent long-running-state initialization failed; "
                    "retrying (attempt %s of 3)",
                    attempt,
                    exc_info=True,
                )
                await asyncio.sleep(0.1 * (2 ** (attempt - 1)))

    async def _initialize_once(self, gen: _Generation) -> None:
        await asyncio.sleep(0)
        states = self._hass.states.async_all()
        now = _as_utc(self._utcnow())
        for index, state in enumerate(states):
            if not self._owns(gen):
                return
            entity_id = getattr(state, "entity_id", None)
            if not isinstance(entity_id, str) or not self._is_selected(entity_id):
                continue
            snapshot = self._snapshot_from_state(entity_id, state)
            if snapshot is not None and activity_marker(snapshot) is not None:
                self.sync(gen, snapshot, state, now, initial=True)
            if index and index % 256 == 0:
                await asyncio.sleep(0)

    def sync(
        self,
        gen: _Generation,
        entity: EntitySnapshot,
        old_state: Any,
        occurred_at: datetime,
        *,
        initial: bool = False,
    ) -> LongRunningInterval | None:
        """Create, retain, reschedule or cancel one interval."""
        entity_id = entity.entity_id
        marker = activity_marker(entity)
        existing = self.intervals.get(entity_id)
        if marker is None or not self.enabled():
            if existing is not None:
                del self.intervals[entity_id]
                gen.metrics.long_running_cancelled += 1
                self.notified.pop(entity_id, None)
                self._arm(gen)
            self._update_retention(gen)
            return None
        minutes = self.minutes()
        if minutes <= 0:
            if existing is not None:
                del self.intervals[entity_id]
                gen.metrics.long_running_cancelled += 1
                self.notified.pop(entity_id, None)
            self._arm(gen)
            self._update_retention(gen)
            return None
        threshold = float(minutes) * 60.0
        event_time = _as_utc(occurred_at)
        if (
            existing is not None
            and existing.marker == marker
            and existing.threshold_seconds == threshold
        ):
            return existing
        if existing is None and len(self.intervals) >= self._capacity():
            self._capacity_rejected(gen, entity_id, event_time)
            return None
        previous_marker = activity_marker(old_state)
        if existing is not None and existing.marker == marker:
            started_at = existing.started_at
        elif initial or previous_marker == marker:
            changed = entity.last_changed
            if (
                has_reliable_last_changed(entity)
                and isinstance(changed, datetime)
                and changed.tzinfo is not None
                and _as_utc(changed) <= event_time
            ):
                started_at = _as_utc(changed)
            else:
                started_at = event_time
        else:
            started_at = event_time
        self._serial += 1
        item = LongRunningInterval(
            marker,
            started_at,
            started_at + timedelta(seconds=threshold),
            self._serial,
            threshold,
        )
        self.intervals[entity_id] = item
        if existing is None:
            gen.metrics.long_running_scheduled += 1
        else:
            gen.metrics.long_running_rescheduled += 1
            self.notified.pop(entity_id, None)
        if len(self.heap) >= self._capacity():
            self._compact(exclude=entity_id)
        heapq.heappush(self.heap, (item.due_at.timestamp(), item.serial, entity_id))
        self._arm(gen)
        self._update_retention(gen)
        return item

    def _compact(self, *, exclude: str | None = None) -> None:
        self.heap[:] = [
            (item.due_at.timestamp(), item.serial, entity_id)
            for entity_id, item in self.intervals.items()
            if entity_id != exclude and not item.fired
        ]
        heapq.heapify(self.heap)

    def _arm(self, gen: _Generation) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        while self.heap:
            _stamp, serial, entity_id = self.heap[0]
            item = self.intervals.get(entity_id)
            if item is not None and item.serial == serial and not item.fired:
                break
            heapq.heappop(self.heap)
        if not self._owns(gen) or not self.heap:
            return
        delay = max(0.0, self.heap[0][0] - _as_utc(self._utcnow()).timestamp())
        self._timer = self._schedule_later(delay, self._timer_fired, gen)

    def _timer_fired(self, gen: _Generation) -> None:
        self._timer = None
        try:
            self._timer_fired_impl(gen)
        except Exception:  # noqa: BLE001 - HA state/timer callback boundary
            gen.metrics.long_running_scheduler_failures += 1
            self._logger.exception(
                "HomeIntent long-running-state deadline processing failed"
            )
            if self._owns(gen):
                self._compact()
                self._arm(gen)

    def _timer_fired_impl(self, gen: _Generation) -> None:
        if not self._owns(gen):
            return
        now = _as_utc(self._utcnow())
        minutes = self.minutes()
        if minutes <= 0 or not self.enabled():
            self.clear(gen)
            return
        expected_threshold = float(minutes) * 60.0
        while self.heap and self.heap[0][0] <= now.timestamp():
            _stamp, serial, entity_id = heapq.heappop(self.heap)
            item = self.intervals.get(entity_id)
            if item is None or item.serial != serial or item.fired:
                continue
            state = self._hass.states.get(entity_id)
            snapshot = (
                self._snapshot_from_state(entity_id, state)
                if state is not None
                else None
            )
            if snapshot is None or activity_marker(snapshot) != item.marker:
                del self.intervals[entity_id]
                gen.metrics.long_running_cancelled += 1
                self.notified.pop(entity_id, None)
                continue
            if item.threshold_seconds != expected_threshold:
                self.sync(gen, snapshot, state, now)
                continue
            item.fired = True
            gen.metrics.long_running_due += 1
            queued_before = gen.metrics.queued
            self._enqueue(
                SimpleNamespace(
                    data={
                        "entity_id": entity_id,
                        "old_state": state,
                        "new_state": state,
                    },
                    time_fired=now,
                    context=None,
                )
            )
            if gen.metrics.queued > queued_before:
                gen.metrics.long_running_enqueued += 1
            else:
                gen.metrics.long_running_capacity_rejected += 1
        self._arm(gen)
        self._update_retention(gen)

    def clear(self, gen: _Generation) -> None:
        """Cancel every timer/task and release all retained scheduler state."""
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        init_task, self._init_task = self._init_task, None
        if init_task is not None and not init_task.done():
            init_task.cancel()
        self.intervals.clear()
        self.heap.clear()
        self.notified.clear()
        self._update_retention(gen)

    def remember_notification(self, entity_id: str, token: tuple[str, Any]) -> bool:
        """Return false for a duplicate and keep the deduplication map bounded."""
        if self.notified.get(entity_id) == token:
            return False
        self.notified[entity_id] = token
        if len(self.notified) > self._capacity():
            self.notified.clear()
            self.notified[entity_id] = token
        return True

    def _update_retention(self, gen: _Generation) -> None:
        gen.metrics.retained_long_running = len(self.intervals)
        gen.metrics.retained_long_running_heap = len(self.heap)
        gen.metrics.retained_total_all = (
            gen.metrics.retained_total
            + gen.metrics.retained_long_running
            + gen.metrics.retained_long_running_heap
        )

    def _capacity_rejected(
        self, gen: _Generation, entity_id: str, now: datetime
    ) -> None:
        gen.metrics.long_running_capacity_rejected += 1
        stamp = now.timestamp()
        if (
            self._last_capacity_warning is not None
            and stamp - self._last_capacity_warning < 60.0
        ):
            return
        self._last_capacity_warning = stamp
        self._logger.warning(
            "HomeIntent long-running-state scheduler reached its %s-entity "
            "capacity; rejecting %s",
            self._capacity(),
            entity_id,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
