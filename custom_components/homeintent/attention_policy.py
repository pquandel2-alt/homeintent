"""V12 attention: cooldown, dedupe, budget, grouping and critical bypass.

``AttentionPolicy`` decides *whether and how often* a recipient is
interrupted; it never decides safety or execution (ATTENTION != SAFETY).
Anything at URGENT or above bypasses every ordinary attention limit.

``AttentionStateStore`` is bounded in every dimension and serializes to a
small JSON-safe document; malformed input is dropped, never trusted.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence, cast

from .proactive_model import (
    AttentionDecision,
    AttentionOutcome,
    PriorityLevel,
    SituationKind,
)


MAX_TRACKED_KEYS = 512
MAX_RECIPIENTS = 64
MAX_DELIVERIES_PER_RECIPIENT = 32
MAX_MUTES = 128
MAX_GROUP_ITEMS = 8


@dataclass(frozen=True)
class AttentionConfig:
    enabled: bool = True
    budget: int = 4
    budget_window: timedelta = timedelta(hours=1)
    group_window: timedelta = timedelta(minutes=2)
    dismiss_cooldown: timedelta = timedelta(hours=2)


@dataclass(frozen=True)
class GroupedItem:
    situation_id: str
    text: str


class AttentionStateStore:
    """Bounded attention memory; oldest entries are evicted first."""

    def __init__(self) -> None:
        self._deliveries: OrderedDict[str, deque[datetime]] = OrderedDict()
        self._key_last: OrderedDict[str, datetime] = OrderedDict()
        self._dismissed_until: OrderedDict[str, datetime] = OrderedDict()
        self._mutes: OrderedDict[str, datetime] = OrderedDict()
        self._groups: dict[str, list[GroupedItem]] = {}
        self._group_deadline: dict[str, datetime] = {}

    # -- recording -----------------------------------------------------------
    def record_delivery(
        self, recipient: str, dedupe_key: str, now: datetime, *, counts_for_budget: bool = True,
    ) -> None:
        """Remember a delivery for dedupe and, unless exempt, for the budget.

        A critical alarm bypasses every attention limit, so it must not use
        up the ordinary budget either: a washer finishing right after a
        smoke alarm is still delivered on its own, not grouped away (F28).
        """
        if counts_for_budget:
            history = self._deliveries.pop(recipient, None) or deque[datetime](
                maxlen=MAX_DELIVERIES_PER_RECIPIENT
            )
            history.append(now)
            self._deliveries[recipient] = history
            while len(self._deliveries) > MAX_RECIPIENTS:
                self._deliveries.popitem(last=False)
        self._key_last.pop(dedupe_key, None)
        self._key_last[dedupe_key] = now
        while len(self._key_last) > MAX_TRACKED_KEYS:
            self._key_last.popitem(last=False)

    def dismiss(self, dedupe_key: str, until: datetime) -> None:
        """Short-term suppression for the *current* situation only.

        Callers pass the situation occurrence id, never a stable subject key.
        """
        self._dismissed_until.pop(dedupe_key, None)
        self._dismissed_until[dedupe_key] = until
        while len(self._dismissed_until) > MAX_TRACKED_KEYS:
            self._dismissed_until.popitem(last=False)

    def mute(self, user_id: str, kind: SituationKind, confirmed_at: datetime) -> None:
        """Persistent mute; callers must only use it after explicit confirmation."""
        key = _mute_key(user_id, kind)
        self._mutes.pop(key, None)
        self._mutes[key] = confirmed_at
        while len(self._mutes) > MAX_MUTES:
            self._mutes.popitem(last=False)

    def unmute(self, user_id: str, kind: SituationKind) -> bool:
        return self._mutes.pop(_mute_key(user_id, kind), None) is not None

    def is_muted(self, user_id: str | None, kind: SituationKind) -> bool:
        return user_id is not None and _mute_key(user_id, kind) in self._mutes

    def mutes(self) -> tuple[tuple[str, SituationKind, datetime], ...]:
        """Read-only view of confirmed mutes as (user_id, kind, confirmed_at)."""
        result: list[tuple[str, SituationKind, datetime]] = []
        for key, confirmed_at in self._mutes.items():
            user_id, _separator, raw_kind = key.partition("\0")
            try:
                result.append((user_id, SituationKind(raw_kind), confirmed_at))
            except ValueError:
                continue
        return tuple(result)

    # -- grouping ------------------------------------------------------------
    def add_to_group(self, recipient: str, item: GroupedItem, deadline: datetime) -> bool:
        """Queue an item; returns True when a new flush must be scheduled."""
        items = self._groups.setdefault(recipient, [])
        if any(existing.situation_id == item.situation_id for existing in items):
            return False
        if len(items) >= MAX_GROUP_ITEMS:
            return False
        items.append(item)
        if recipient in self._group_deadline:
            return False
        self._group_deadline[recipient] = deadline
        return True

    def take_group(self, recipient: str) -> tuple[GroupedItem, ...]:
        self._group_deadline.pop(recipient, None)
        return tuple(self._groups.pop(recipient, ()))

    def group_size(self, recipient: str) -> int:
        return len(self._groups.get(recipient, ()))

    # -- queries --------------------------------------------------------------
    def deliveries_since(self, recipient: str, since: datetime) -> int:
        return sum(1 for item in self._deliveries.get(recipient, ()) if item >= since)

    def last_delivery(self, recipient: str) -> datetime | None:
        history = self._deliveries.get(recipient)
        return history[-1] if history else None

    def key_last(self, dedupe_key: str) -> datetime | None:
        return self._key_last.get(dedupe_key)

    def dismissed_until(self, dedupe_key: str) -> datetime | None:
        return self._dismissed_until.get(dedupe_key)

    @property
    def size(self) -> int:
        return (
            len(self._deliveries) + len(self._key_last)
            + len(self._dismissed_until) + len(self._mutes)
        )

    # -- persistence ------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "deliveries": {
                key: [item.isoformat() for item in value]
                for key, value in self._deliveries.items()
            },
            "key_last": {key: value.isoformat() for key, value in self._key_last.items()},
            "dismissed_until": {
                key: value.isoformat() for key, value in self._dismissed_until.items()
            },
            "mutes": {key: value.isoformat() for key, value in self._mutes.items()},
        }

    @classmethod
    def from_dict(cls, raw: object) -> "AttentionStateStore":
        store = cls()
        if not isinstance(raw, Mapping):
            return store
        document = cast(Mapping[str, object], raw)
        deliveries = document.get("deliveries")
        if isinstance(deliveries, Mapping):
            for key, values in cast(Mapping[object, object], deliveries).items():
                if not isinstance(key, str) or not isinstance(values, list):
                    continue
                stamps = [
                    stamp for item in cast(Sequence[object], values)
                    if (stamp := _aware(item)) is not None
                ][-MAX_DELIVERIES_PER_RECIPIENT:]
                store._deliveries[key] = deque(stamps, maxlen=MAX_DELIVERIES_PER_RECIPIENT)
        for name, target, limit in (
            ("key_last", store._key_last, MAX_TRACKED_KEYS),
            ("dismissed_until", store._dismissed_until, MAX_TRACKED_KEYS),
            ("mutes", store._mutes, MAX_MUTES),
        ):
            values = document.get(name)
            if not isinstance(values, Mapping):
                continue
            for key, value in cast(Mapping[object, object], values).items():
                stamp = _aware(value)
                if isinstance(key, str) and stamp is not None:
                    target[key] = stamp
            while len(target) > limit:
                target.popitem(last=False)
        while len(store._deliveries) > MAX_RECIPIENTS:
            store._deliveries.popitem(last=False)
        return store


class AttentionPolicy:
    """Priority-aware interruption control for one recipient."""

    def __init__(self, config: AttentionConfig | None = None) -> None:
        self.config = config or AttentionConfig()

    def decide(
        self,
        store: AttentionStateStore,
        *,
        recipient: str,
        dedupe_key: str,
        priority: PriorityLevel,
        requires_response: bool,
        now: datetime,
        occurrence_id: str | None = None,
    ) -> AttentionDecision:
        if priority >= PriorityLevel.URGENT:
            return AttentionDecision(AttentionOutcome.BYPASS, ("critical_bypasses_attention",))
        if not self.config.enabled:
            return AttentionDecision(AttentionOutcome.DELIVER, ("attention_budget_disabled",))
        # A dismissal belongs to one occurrence; the next time the garage is
        # left open is a new situation and may be reported again.
        dismissed = store.dismissed_until(occurrence_id or dedupe_key)
        if dismissed is not None and now < dismissed:
            return AttentionDecision(AttentionOutcome.SUPPRESS, ("recently_dismissed",))
        last_key = store.key_last(dedupe_key)
        if last_key is not None and now - last_key < self.config.group_window:
            return AttentionDecision(AttentionOutcome.SUPPRESS, ("duplicate_within_window",))
        used = store.deliveries_since(recipient, now - self.config.budget_window)
        if used >= self.config.budget:
            if priority >= PriorityLevel.IMPORTANT:
                return AttentionDecision(
                    AttentionOutcome.DELIVER, ("budget_exhausted_important_still_delivered",),
                )
            return AttentionDecision(AttentionOutcome.DEFER, ("attention_budget_exhausted",))
        last = store.last_delivery(recipient)
        if (
            not requires_response
            and priority <= PriorityLevel.SUGGESTION
            and last is not None
            and now - last < self.config.group_window
        ):
            return AttentionDecision(
                AttentionOutcome.GROUP, ("grouped_with_recent_message",), group_key=recipient,
            )
        return AttentionDecision(AttentionOutcome.DELIVER, ("within_attention_budget",))


def _mute_key(user_id: str, kind: SituationKind) -> str:
    return f"{user_id}\0{kind.value}"


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


__all__ = (
    "AttentionConfig",
    "AttentionPolicy",
    "AttentionStateStore",
    "GroupedItem",
    "MAX_GROUP_ITEMS",
    "MAX_TRACKED_KEYS",
)
