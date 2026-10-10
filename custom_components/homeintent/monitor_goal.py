"""Persistent event-driven monitor goals built on HA state transitions.

The runtime does not poll or sleep.  It receives a concrete transition from
Home Assistant, rebuilds a fresh entity snapshot through the supplied
adapter, evaluates typed conditions, and sends typed notifications through
the existing delivery boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Sequence, cast

from .entities import EntitySnapshot
from .goal_model import GoalKind, GoalModel, NotificationSeverity
from .goal_run import (
    FailureCode,
    GoalRun,
    GoalRunStatus,
    GoalRunStore,
    NotificationRecord,
)
from .rate_monitor import ChangeDirection, RateRule, evaluate_rate, finding_message
from .user_context import BindingStatus, NotificationTargetKind, UserContextStore


_LOGGER = logging.getLogger(__name__)


class NotificationCategory(StrEnum):
    OPEN_WINDOWS_WARNING = "open_windows_warning"
    LIGHTS_ON_AWAY = "lights_on_away"
    UNLOCKED_DOOR_WARNING = "unlocked_door_warning"
    GARAGE_OPEN_WARNING = "garage_open_warning"
    APPLIANCE_FINISHED = "appliance_finished"
    GENERIC_MONITOR = "generic_monitor"
    VALUE_CHANGE = "value_change"


@dataclass(frozen=True)
class NotificationModel:
    recipient_person_id: str
    target_id: str
    target_kind: NotificationTargetKind | None
    category: NotificationCategory
    severity: NotificationSeverity
    entity_ids: tuple[str, ...]
    entity_names: tuple[str, ...]
    area_names: tuple[str, ...] = ()
    occurred_at: str = ""
    goal_id: str = ""
    run_id: str = ""
    dedupe_key: str = ""
    detail: str = ""  # a complete, already rendered sentence (VALUE_CHANGE)


@dataclass(frozen=True)
class RenderedNotification:
    title: str
    message: str


@dataclass(frozen=True)
class MonitorRecord:
    goal: GoalModel
    enabled: bool = True
    cooldown_seconds: int = 300
    last_delivery_at: str | None = None
    last_dedupe_key: str | None = None


class MonitorGoalStore:
    """Monitor goals on disk, read once and then served from memory (7.9.6).

    7.9.5 read the JSON file again on every ``async_load()`` - once per
    person transition and per watched sensor change. Now the first load
    (single flight) fills an immutable record cache; ``async_load()`` returns
    it without touching the disk until ``async_reload()`` asks for a fresh
    read. ``async_save()``/``async_delete()`` serialize on one write lock,
    write the file atomically and only then replace the cache and the
    derived interest sets, so a failed write leaves both unchanged and two
    concurrent writers never lose each other's records.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._records: tuple[MonitorRecord, ...] | None = None
        # Single flight for reads (7.9.4 P0 for the watched set; 7.9.6 for
        # the records themselves).
        self._load_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._interest = _MonitorInterest.of(())
        self._listeners: list[Callable[[], None]] = []
        # File reads so far (tests and the EventRuntime metrics read it).
        self.read_count = 0

    # -- cache ------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._records is not None

    async def async_load(self) -> tuple[MonitorRecord, ...]:
        """The cached records; the first call reads the file exactly once.

        An unreadable file (other than a missing one) is reported as empty
        like before, but it is not cached: the next call reads again.
        """
        if self._records is not None:
            return self._records
        try:
            return await self._async_ensure_loaded()
        except OSError:
            _LOGGER.warning(
                "HomeIntent monitor goals could not be read from %s", self.path, exc_info=True
            )
            return ()

    async def async_reload(self) -> tuple[MonitorRecord, ...]:
        """Read the file again on purpose (e.g. after an external change)."""
        async with self._write_lock:
            async with self._load_lock:
                records = tuple(await asyncio.to_thread(self._read))
                self._replace_cache(records)
                return records

    async def async_watched_entities(self) -> frozenset[str]:
        """Sensors watched by value-change goals (7.9 W3)."""
        await self.async_load()
        return self._interest.value_entity_ids

    # Synchronous, disk-free views for the EventRuntime interest index.
    @property
    def watched_value_entity_ids(self) -> frozenset[str]:
        return self._interest.value_entity_ids

    @property
    def watched_person_entity_ids(self) -> frozenset[str]:
        return self._interest.person_entity_ids

    @property
    def watched_nobody_home_person_ids(self) -> frozenset[str]:
        return self._interest.nobody_home_person_ids

    @property
    def nobody_home_uses_household(self) -> bool:
        """An enabled nobody-home goal follows the configured household.

        Its persons come from ``UserContextStore`` and may change at any
        time, so every ``person.*`` counts as watched.
        """
        return self._interest.nobody_home_uses_household

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after the cache (and interest sets) changed."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # -- writes -----------------------------------------------------------
    async def async_save(self, record: MonitorRecord) -> None:
        if record.goal.kind is not GoalKind.MONITOR_AND_NOTIFY:
            raise ValueError("Only monitor-and-notify goals belong in this store")
        if not record.goal.goal_id:
            raise ValueError("Persistent goals require a goal id")
        async with self._write_lock:
            records = await self._async_ensure_loaded()
            updated = tuple(
                item for item in records if item.goal.goal_id != record.goal.goal_id
            ) + (record,)
            await asyncio.to_thread(self._write, updated)
            self._replace_cache(updated)

    async def async_delete(self, goal_id: str) -> bool:
        async with self._write_lock:
            records = await self._async_ensure_loaded()
            remaining = tuple(item for item in records if item.goal.goal_id != goal_id)
            if len(remaining) == len(records):
                return False
            await asyncio.to_thread(self._write, remaining)
            self._replace_cache(remaining)
            return True

    async def _async_ensure_loaded(self) -> tuple[MonitorRecord, ...]:
        """The cached records, reading them once; raises if unreadable.

        Writers use this instead of ``async_load()``: writing after a failed
        read would replace the stored goals with the new one alone.
        """
        if self._records is not None:
            return self._records
        async with self._load_lock:
            if self._records is None:
                records = tuple(await asyncio.to_thread(self._read))
                self._replace_cache(records)
            assert self._records is not None
            return self._records

    def _replace_cache(self, records: tuple[MonitorRecord, ...]) -> None:
        self._records = records
        self._interest = _MonitorInterest.of(records)
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - bookkeeping must not fail a write
                _LOGGER.exception("HomeIntent monitor interest listener failed")

    def _read(self) -> list[MonitorRecord]:
        """The stored records; raises on an I/O error other than a missing
        file (7.9.6: a writer must not replace unreadable goals)."""
        self.read_count += 1
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[MonitorRecord]:
        try:
            raw: object = json.loads(text)
        except json.JSONDecodeError:
            return []
        values = cast(Mapping[str, object], raw).get("goals", ()) if isinstance(raw, Mapping) else ()
        result: list[MonitorRecord] = []
        for item in _mapping_sequence(values):
            try:
                goal_raw = cast(Mapping[str, object], item["goal"])
                result.append(
                    MonitorRecord(
                        GoalModel.from_dict(goal_raw), bool(item.get("enabled", True)),
                        _bounded_cooldown(item.get("cooldown_seconds")),
                        _text(item.get("last_delivery_at")),
                        _text(item.get("last_dedupe_key")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def _write(self, records: Sequence[MonitorRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "goals": [
                {
                    "goal": item.goal.to_dict(), "enabled": item.enabled,
                    "cooldown_seconds": item.cooldown_seconds,
                    "last_delivery_at": item.last_delivery_at,
                    "last_dedupe_key": item.last_dedupe_key,
                }
                for item in records
            ],
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homeintent_goals_", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


FreshEntities = Callable[[], Awaitable[list[EntitySnapshot]]]
DeliverNotification = Callable[[NotificationModel, RenderedNotification], Awaitable[bool]]
# (entity_id, start, end) -> recorded (time, numeric value) samples.
ReadHistory = Callable[[str, datetime, datetime], Awaitable[list[tuple[datetime, float]]]]


def rate_rule_of(goal: GoalModel) -> RateRule | None:
    """The value-change rule of a monitor goal (7.9 W3), if it is one."""
    trigger = goal.trigger
    if (
        trigger is None or trigger.kind != "value_change" or trigger.entity_id is None
        or trigger.delta is None or trigger.window_seconds is None or trigger.direction is None
    ):
        return None
    try:
        direction = ChangeDirection(trigger.direction)
    except ValueError:
        return None
    return RateRule(
        trigger.entity_id, trigger.delta, trigger.unit or "", direction, trigger.window_seconds
    )


@dataclass(frozen=True)
class _MonitorInterest:
    """What the enabled goals react to, derived with the record cache."""

    value_entity_ids: frozenset[str]
    person_entity_ids: frozenset[str]
    nobody_home_person_ids: frozenset[str]
    nobody_home_uses_household: bool

    @classmethod
    def of(cls, records: Iterable[MonitorRecord]) -> "_MonitorInterest":
        values: set[str] = set()
        persons: set[str] = set()
        nobody: set[str] = set()
        household = False
        for record in records:
            if not record.enabled:
                continue
            rule = rate_rule_of(record.goal)
            if rule is not None:
                values.add(rule.entity_id)
            trigger = record.goal.trigger
            if trigger is None:
                continue
            if trigger.kind in {"person_leaves_zone", "person_arrives_zone"}:
                if trigger.person_entity_id:
                    persons.add(trigger.person_entity_id)
            elif trigger.kind == "nobody_home":
                if trigger.household_person_ids:
                    nobody.update(trigger.household_person_ids)
                else:
                    household = True
        return cls(frozenset(values), frozenset(persons), frozenset(nobody), household)


@dataclass
class MonitorGoalRuntime:
    store: MonitorGoalStore
    run_store: GoalRunStore
    user_contexts: UserContextStore
    refresh_entities: FreshEntities
    deliver: DeliverNotification
    read_history: ReadHistory | None = None
    _locks: dict[str, asyncio.Lock] = field(default_factory=lambda: _empty_locks(), init=False)

    async def async_process_value_change(
        self,
        entity_id: str,
        value: float,
        *,
        occurred_at: datetime | None = None,
    ) -> tuple[GoalRun, ...]:
        """A watched sensor changed (7.9 W3): compare with its window."""
        if entity_id not in await self.store.async_watched_entities() or self.read_history is None:
            return ()
        now = occurred_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Monitor events require timezone-aware timestamps")
        results: list[GoalRun] = []
        for record in await self.store.async_load():
            rule = rate_rule_of(record.goal)
            if not record.enabled or rule is None or rule.entity_id != entity_id:
                continue
            if record.last_delivery_at is not None and now - datetime.fromisoformat(
                record.last_delivery_at
            ) < timedelta(seconds=rule.window_seconds):
                continue  # at most one message per window
            samples = await self.read_history(
                entity_id, now - timedelta(seconds=rule.window_seconds), now
            )
            finding = evaluate_rate(rule, samples, value, now)
            if finding is None:
                continue
            lock = self._locks.setdefault(record.goal.goal_id, asyncio.Lock())
            async with lock:
                entities = await self.refresh_entities()
                name = next(
                    (item.friendly_name for item in entities if item.entity_id == entity_id), entity_id
                )
                detail = finding_message(rule, f"„{name}“", finding)
                result = await self._async_notify(
                    record, f"{entity_id}:{now.isoformat()}", now,
                    NotificationCategory.VALUE_CHANGE, (entity_id,), (name,), detail,
                )
                if result is not None:
                    results.append(result)
        return tuple(results)

    async def _async_notify(
        self,
        record: MonitorRecord,
        occurrence: str,
        now: datetime,
        category: "NotificationCategory",
        entity_ids: tuple[str, ...],
        names: tuple[str, ...],
        detail: str,
    ) -> GoalRun | None:
        goal = record.goal
        dedupe_base = f"{goal.goal_id}:{occurrence}"
        if await self.run_store.async_seen_idempotency_key(dedupe_base):
            return None
        run = GoalRun.start(
            goal, user_id=goal.provenance.user_id, person_entity_id=None,
            idempotency_key=dedupe_base, now=now,
        )
        notifications: list[NotificationRecord] = []
        for person_id in goal.recipient_person_ids:
            binding = self.user_contexts.resolve_notification_targets(person_id)
            if binding.status is not BindingStatus.RESOLVED:
                code = (
                    FailureCode.NOTIFICATION_TARGET_AMBIGUOUS
                    if binding.status is BindingStatus.AMBIGUOUS
                    else FailureCode.NOTIFICATION_TARGET_MISSING
                )
                failed = replace(
                    run, updated_at=now.isoformat(), status=GoalRunStatus.FAILURE,
                    failures=(code,), evidence=(binding.reason or code.value,),
                )
                await self.run_store.async_append(failed)
                return failed
            for target in binding.targets:
                dedupe = f"{dedupe_base}:{category.value}:{person_id}:{target.target_id}"
                model = NotificationModel(
                    person_id, target.target_id, target.kind, category,
                    goal.notification_severity, entity_ids, names, (), now.isoformat(),
                    goal.goal_id, run.run_id, dedupe, detail,
                )
                delivered = await self.deliver(model, render_notification(model))
                notifications.append(NotificationRecord(
                    person_id, target.target_id, target.channel,
                    goal.notification_severity.value, delivered, dedupe, category.value,
                ))
        delivered_all = bool(notifications) and all(item.delivered for item in notifications)
        completed = replace(
            run, updated_at=now.isoformat(), notifications=tuple(notifications),
            status=GoalRunStatus.SUCCESS if delivered_all else GoalRunStatus.FAILURE,
            failures=() if delivered_all else (FailureCode.SERVICE_ERROR,),
            selected_targets=entity_ids, evidence=("recorder_window", detail),
        )
        await self.run_store.async_append(completed)
        if delivered_all:
            await self.store.async_save(
                replace(record, last_delivery_at=now.isoformat(), last_dedupe_key=dedupe_base)
            )
        return completed

    async def async_process_person_transition(
        self,
        person_entity_id: str,
        old_state: str,
        new_state: str,
        *,
        occurred_at: datetime | None = None,
        occurrence_id: str | None = None,
    ) -> tuple[GoalRun, ...]:
        now = occurred_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Monitor events require timezone-aware timestamps")
        records = await self.store.async_load()
        if not records:
            return ()
        occurrence = occurrence_id or f"{person_entity_id}:{old_state}:{new_state}:{now.isoformat()}"
        results: list[GoalRun] = []
        for record in records:
            if not record.enabled or not _trigger_matches(
                record.goal, person_entity_id, old_state, new_state, self.user_contexts
            ):
                continue
            lock = self._locks.setdefault(record.goal.goal_id, asyncio.Lock())
            async with lock:
                result = await self._async_evaluate(record, occurrence, now)
                if result is not None:
                    results.append(result)
        return tuple(results)

    async def _async_evaluate(
        self, record: MonitorRecord, occurrence: str, now: datetime
    ) -> GoalRun | None:
        goal = record.goal
        dedupe_base = f"{goal.goal_id}:{occurrence}"
        if record.last_dedupe_key == dedupe_base:
            return None
        if record.last_delivery_at is not None:
            previous = datetime.fromisoformat(record.last_delivery_at)
            if now - previous < timedelta(seconds=record.cooldown_seconds):
                return None
        idempotency_key = dedupe_base
        if await self.run_store.async_seen_idempotency_key(idempotency_key):
            return None
        run = GoalRun.start(
            goal,
            user_id=goal.provenance.user_id,
            person_entity_id=goal.trigger.person_entity_id if goal.trigger else None,
            idempotency_key=idempotency_key,
            now=now,
        )
        entities = await self.refresh_entities()
        if goal.trigger is not None and goal.trigger.kind == "nobody_home":
            household = (
                goal.trigger.household_person_ids
                or self.user_contexts.household.person_entity_ids
            )
            states = {
                item.entity_id: item.state for item in entities if item.domain == "person"
            }
            if not household or any(states.get(person) == "home" for person in household):
                return None
        matching, category = _evaluate_dynamic_condition(goal, entities)
        if not matching:
            completed = replace(
                run, updated_at=now.isoformat(), status=GoalRunStatus.SUCCESS,
                evidence=("fresh_runtime_query_empty",),
            )
            await self.run_store.async_append(completed)
            return completed
        recipients = goal.recipient_person_ids
        if not recipients and goal.trigger and goal.trigger.person_entity_id:
            recipients = (goal.trigger.person_entity_id,)
        notifications: list[NotificationRecord] = []
        names = tuple(item.friendly_name for item in matching)
        areas = tuple(dict.fromkeys(item.area_name for item in matching if item.area_name))
        bindings = tuple(
            (person_id, self.user_contexts.resolve_notification_targets(person_id))
            for person_id in recipients
        )
        unresolved = next(
            (binding for _person_id, binding in bindings if binding.status is not BindingStatus.RESOLVED),
            None,
        )
        if unresolved is not None:
            code = (
                FailureCode.NOTIFICATION_TARGET_AMBIGUOUS
                if unresolved.status is BindingStatus.AMBIGUOUS
                else FailureCode.NOTIFICATION_TARGET_MISSING
            )
            failed = replace(
                run, updated_at=now.isoformat(), status=GoalRunStatus.FAILURE,
                failures=(code,), evidence=(unresolved.reason or code.value,),
            )
            await self.run_store.async_append(failed)
            return failed
        for person_id, binding in bindings:
            for target in binding.targets:
                dedupe = f"{dedupe_base}:{category.value}:{person_id}:{target.target_id}"
                model = NotificationModel(
                    person_id, target.target_id, target.kind, category,
                    goal.notification_severity, tuple(item.entity_id for item in matching),
                    names, areas, now.isoformat(), goal.goal_id, run.run_id, dedupe,
                )
                delivered = await self.deliver(model, render_notification(model))
                notifications.append(
                    NotificationRecord(
                        person_id, target.target_id, target.channel,
                        goal.notification_severity.value, delivered, dedupe, category.value,
                    )
                )
        delivered_all = bool(notifications) and all(item.delivered for item in notifications)
        completed = replace(
            run, updated_at=now.isoformat(), notifications=tuple(notifications),
            status=GoalRunStatus.SUCCESS if delivered_all else GoalRunStatus.FAILURE,
            failures=() if delivered_all else (FailureCode.SERVICE_ERROR,),
            selected_targets=tuple(item.entity_id for item in matching),
            evidence=("fresh_runtime_query", f"matches={len(matching)}"),
        )
        await self.run_store.async_append(completed)
        if delivered_all:
            await self.store.async_save(
                replace(
                    record, last_delivery_at=now.isoformat(), last_dedupe_key=dedupe_base
                )
            )
        return completed


def render_notification(model: NotificationModel) -> RenderedNotification:
    names = _german_list(model.entity_names)
    count = len(model.entity_names)
    if model.category is NotificationCategory.OPEN_WINDOWS_WARNING:
        title = "Fenster noch offen"
        suffix = "ist noch offen" if count == 1 else "sind noch offen"
        return RenderedNotification(title, f"Du hast das Haus verlassen. {names} {suffix}.")
    if model.category is NotificationCategory.LIGHTS_ON_AWAY:
        title = "Licht noch an"
        suffix = "ist noch an" if count == 1 else "sind noch an"
        return RenderedNotification(title, f"Niemand ist mehr zuhause. {names} {suffix}.")
    if model.category is NotificationCategory.UNLOCKED_DOOR_WARNING:
        return RenderedNotification("Tür nicht verriegelt", f"{names} ist nicht verriegelt.")
    if model.category is NotificationCategory.GARAGE_OPEN_WARNING:
        return RenderedNotification("Garage noch offen", f"{names} ist noch offen.")
    if model.category is NotificationCategory.VALUE_CHANGE and model.detail:
        return RenderedNotification("HomeIntent", model.detail)
    return RenderedNotification("HomeIntent Hinweis", f"Aktueller Hinweis: {names}.")


def _trigger_matches(
    goal: GoalModel,
    person_id: str,
    old_state: str,
    new_state: str,
    users: UserContextStore,
) -> bool:
    trigger = goal.trigger
    if trigger is None:
        return False
    if trigger.kind == "person_leaves_zone":
        return (
            trigger.person_entity_id == person_id
            and old_state == (trigger.zone_id or "home")
            and new_state != (trigger.zone_id or "home")
        )
    if trigger.kind == "person_arrives_zone":
        return (
            trigger.person_entity_id == person_id
            and old_state != (trigger.zone_id or "home")
            and new_state == (trigger.zone_id or "home")
        )
    if trigger.kind == "nobody_home":
        household = trigger.household_person_ids or users.household.person_entity_ids
        return person_id in household and old_state == "home" and new_state != "home"
    return False


def _evaluate_dynamic_condition(
    goal: GoalModel, entities: Iterable[EntitySnapshot]
) -> tuple[tuple[EntitySnapshot, ...], NotificationCategory]:
    snapshots = tuple(entities)
    condition = goal.conditions[0] if goal.conditions else None
    if condition is None:
        return (), NotificationCategory.GENERIC_MONITOR
    scope = condition.scope
    candidates = [
        item for item in snapshots
        if (scope.domain is None or item.domain == scope.domain)
        and (scope.device_class is None or item.device_class == scope.device_class)
        and (scope.area_id is None or item.area_id == scope.area_id)
        and item.entity_id not in set(scope.excluded_entity_ids)
        and item.area_id not in set(scope.excluded_area_ids)
    ]
    value = str(condition.value)
    if condition.kind == "open_entities":
        matches = tuple(item for item in candidates if item.state in {"on", "open", "opening"})
        category = (
            NotificationCategory.OPEN_WINDOWS_WARNING
            if scope.device_class == "window"
            else NotificationCategory.GARAGE_OPEN_WARNING
        )
        return matches, category
    if condition.kind == "lights_on":
        return tuple(item for item in candidates if item.domain == "light" and item.state == "on"), NotificationCategory.LIGHTS_ON_AWAY
    if condition.kind == "state_not_equals":
        return tuple(item for item in candidates if item.state != value), NotificationCategory.UNLOCKED_DOOR_WARNING
    if condition.kind == "state_equals":
        return tuple(item for item in candidates if item.state == value), NotificationCategory.GENERIC_MONITOR
    return (), NotificationCategory.GENERIC_MONITOR


def _german_list(values: Sequence[str]) -> str:
    if not values:
        return "Nichts"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " und " + values[-1]


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(Sequence[object], value)
    return tuple(cast(Mapping[str, object], item) for item in values if isinstance(item, Mapping))


def _empty_locks() -> dict[str, asyncio.Lock]:
    return {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_cooldown(value: object) -> int:
    return max(0, min(value, 86400)) if isinstance(value, int) else 300


__all__ = (
    "MonitorGoalRuntime", "MonitorGoalStore", "MonitorRecord", "rate_rule_of",
    "NotificationCategory", "NotificationModel", "RenderedNotification",
    "render_notification",
)
