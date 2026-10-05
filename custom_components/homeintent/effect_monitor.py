"""Bounded verification deadlines for successful HomeIntent service calls."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .entities import normalize_for_compare
from .service_call import ServiceCallPlan


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpectedEffect:
    effect_id: str
    entity_id: str
    expected_state: str
    registered_at: datetime
    deadline: datetime
    operator_id: str | None = None


ExpiredHandler = Callable[[ExpectedEffect], Awaitable[None]]
TimeoutResolver = Callable[[ServiceCallPlan], timedelta | None]
ActionObserver = Callable[[ServiceCallPlan, datetime], None]


class EffectMonitor:
    """Track observable effects; it never retries or performs an action."""

    def __init__(
        self,
        *,
        timeout: timedelta = timedelta(seconds=10),
        absolute_max_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        self.timeout = timeout
        self.absolute_max_timeout = max(timeout, absolute_max_timeout)
        self._pending: dict[str, ExpectedEffect] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._handler: ExpiredHandler | None = None
        self._timeout_resolver: TimeoutResolver | None = None
        self._action_observer: ActionObserver | None = None

    def set_expired_handler(self, handler: ExpiredHandler | None) -> None:
        self._handler = handler

    def set_timeout_resolver(self, resolver: TimeoutResolver | None) -> None:
        """Install advisory timing; the absolute deadline remains authoritative."""
        self._timeout_resolver = resolver

    def set_action_observer(self, observer: ActionObserver | None) -> None:
        """Observe accepted actions without granting execution authority."""
        self._action_observer = observer

    def register(
        self, plan: ServiceCallPlan, *, now: datetime | None = None
    ) -> tuple[ExpectedEffect, ...]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Wirkungsprüfung benötigt eine Zeitzone")
        if self._action_observer is not None:
            try:
                self._action_observer(plan, current)
            except Exception:  # noqa: BLE001 - advisory learning cannot break execution
                _LOGGER.exception("HomeIntent action observation failed")
        expected = _expected_state(plan)
        if expected is None:
            return ()
        resolved_timeout = (
            self._timeout_resolver(plan) if self._timeout_resolver is not None else None
        )
        effective_timeout = self.timeout
        if resolved_timeout is not None:
            effective_timeout = min(
                max(timedelta(milliseconds=100), resolved_timeout),
                self.absolute_max_timeout,
            )
        entity_ids = (
            (plan.entity_id,)
            if isinstance(plan.entity_id, str)
            else tuple(plan.entity_id)
        )
        registered: list[ExpectedEffect] = []
        for entity_id in entity_ids:
            effect = ExpectedEffect(
                f"effect_{uuid.uuid4().hex}",
                entity_id,
                expected,
                current,
                current + effective_timeout,
                f"{plan.domain}.{plan.service}".upper().replace(".", "_"),
            )
            previous = next(
                (
                    item_id
                    for item_id, item in self._pending.items()
                    if item.entity_id == entity_id
                ),
                None,
            )
            if previous is not None:
                self._discard(previous)
            self._pending[effect.effect_id] = effect
            self._tasks[effect.effect_id] = asyncio.create_task(
                self._expire_after(effect),
                name=f"homeintent-effect-{effect.effect_id}",
            )
            registered.append(effect)
        return tuple(registered)

    def observe(self, entity_id: str, state: str) -> bool:
        normalized = normalize_for_compare(state)
        matched = [
            effect_id
            for effect_id, effect in self._pending.items()
            if effect.entity_id == entity_id
            and normalize_for_compare(effect.expected_state) == normalized
        ]
        for effect_id in matched:
            self._discard(effect_id)
        return bool(matched)

    @property
    def pending(self) -> tuple[ExpectedEffect, ...]:
        return tuple(sorted(self._pending.values(), key=lambda item: item.effect_id))

    async def async_close(self) -> None:
        tasks = tuple(self._tasks.values())
        self._tasks.clear()
        self._pending.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _expire_after(self, effect: ExpectedEffect) -> None:
        delay = max(
            0.0,
            (effect.deadline - datetime.now(timezone.utc)).total_seconds(),
        )
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._pending.pop(effect.effect_id, None) is None:
            return
        self._tasks.pop(effect.effect_id, None)
        if self._handler is not None:
            await self._handler(effect)

    def _discard(self, effect_id: str) -> None:
        self._pending.pop(effect_id, None)
        task = self._tasks.pop(effect_id, None)
        if task is not None:
            task.cancel()


def expected_state(plan: ServiceCallPlan) -> str | None:
    """The end state a successful write leaves its targets in, if known."""
    return _expected_state(plan)


def _expected_state(plan: ServiceCallPlan) -> str | None:
    return {
        "turn_on": "on",
        "turn_off": "off",
        "open_cover": "open",
        "open_valve": "open",
        "close_cover": "closed",
        "close_valve": "closed",
        "lock": "locked",
        "unlock": "unlocked",
        "alarm_disarm": "disarmed",
    }.get(plan.service)


__all__ = ("ActionObserver", "EffectMonitor", "ExpectedEffect", "TimeoutResolver", "expected_state")
