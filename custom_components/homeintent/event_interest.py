"""Which selected state changes any active HomeIntent consumer needs (7.9.6).

7.9.5 queued every state change of a selected entity, built a snapshot of
the whole house and called every consumer - even when none of them was
active (no event categories, V12 context off, no expected effect, no
monitor goal, no thermal cycle). The index answers in the event-bus
callback, synchronously and without disk or registry access, whether an
event is needed at all and how much it may be shed under load
(``event_priority.EventPriority``).

Consumers and what makes them interested:

- safety devices: always (``CRITICAL``, see ``event_priority``);
- ``EffectMonitor``: the entities of pending expected effects;
- ``ThermalExperienceTracker``: the entities of active cycles;
- ``MonitorGoalStore``: value-change sensors, trigger persons and the
  persons of nobody-home goals (all ``person.*`` when such a goal follows
  the configured household); before the store is loaded every person and
  sensor counts;
- house-wide consumers - configured agent event categories (situations and,
  with them, routine detection) and an *enabled* V12 context: every
  selected entity, ranked by ``event_priority``.

A runtime object that exists but is disabled is not a consumer. A consumer
object that does not declare its interest (a test double, a future
consumer) is treated as interested in everything (conservative).

Specific interests are pushed: each provider calls back when its watched
set changes and ``refresh()`` rebuilds one merged set, so the callback does
one membership test. House-wide flags follow the options mapping and the
V12 runtime object by identity and are recomputed when either changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, cast

from .agent_config_validation import parse_event_categories
from .const import (
    CONF_AGENT_EVENT_CATEGORIES,
    CONF_ROUTINE_DETECTION_ENABLED,
)
from .event_priority import (
    EventPriority,
    device_class_of,
    is_critical_change,
    is_plain_number,
)


_LOGGER = logging.getLogger(__name__)


class _HouseWide:
    """House-wide consumer flags derived from options and the V12 runtime."""

    __slots__ = ("active", "routine", "proactive", "proactive_undeclared")

    def __init__(self, options: Mapping[str, Any], proactive: Any) -> None:
        categories: frozenset[str]
        try:
            categories = parse_event_categories(options.get(CONF_AGENT_EVENT_CATEGORIES, ""))
        except ValueError:
            categories = frozenset()
        enabled = getattr(proactive, "enabled", None) if proactive is not None else None
        self.proactive_undeclared = proactive is not None and not isinstance(enabled, bool)
        self.proactive = enabled is True and callable(
            getattr(proactive, "is_relevant_event", None)
        )
        if enabled is True and not self.proactive:
            self.proactive_undeclared = True
        # Routine statistics run inside the category evaluation only.
        self.routine = bool(categories) and bool(
            options.get(CONF_ROUTINE_DETECTION_ENABLED, False)
        )
        self.active = bool(categories) or self.proactive or self.proactive_undeclared


class EventInterestIndex:
    """In-memory consumer index for the EventRuntime callback."""

    def __init__(self, entry: Any, runtime_data: Any) -> None:
        self._entry = entry
        self._data = runtime_data
        self._specific: frozenset[str] = frozenset()
        self._all_persons = False
        self._all_sensors = False
        self._everything = False
        self._house_wide: _HouseWide | None = None
        self._house_key: tuple[object, ...] | None = None
        self._unsubscribe: list[Callable[[], None]] = []
        # Bumped by every ``refresh()``; tests and diagnostics read it.
        self.version = 0

    # -- lifecycle --------------------------------------------------------
    def bind(self) -> None:
        """Follow the providers' watched sets and build the index."""
        self.unbind()
        bound: set[int] = set()
        for provider, method in (
            (getattr(self._data, "effect_monitor", None), "add_watch_listener"),
            (getattr(self._data, "thermal_tracker", None), "add_watch_listener"),
            (getattr(self._data, "monitor_goals", None), "add_change_listener"),
            (self._monitor_store(), "add_change_listener"),
        ):
            add = getattr(provider, method, None) if provider is not None else None
            if callable(add) and id(provider) not in bound:
                bound.add(id(provider))
                remove: Any = add(self.refresh)
                if callable(remove):
                    self._unsubscribe.append(cast(Callable[[], None], remove))
        self.refresh()

    def unbind(self) -> None:
        for remove in self._unsubscribe:
            remove()
        self._unsubscribe = []

    def refresh(self) -> None:
        """Rebuild the specific (lossless) entity set from the providers."""
        specific: set[str] = set()
        everything = False
        effects = getattr(self._data, "effect_monitor", None)
        if effects is not None:
            watched = getattr(effects, "watched_entity_ids", None)
            if isinstance(watched, frozenset):
                specific.update(cast(frozenset[str], watched))
            else:
                everything = True
        thermal = getattr(self._data, "thermal_tracker", None)
        if thermal is not None:
            watched = getattr(thermal, "watched_entity_ids", None)
            if isinstance(watched, frozenset):
                specific.update(cast(frozenset[str], watched))
            else:
                everything = True
        all_persons = False
        all_sensors = False
        if getattr(self._data, "monitor_runtime", None) is not None:
            store = self._monitor_store()
            if store is None or not isinstance(getattr(store, "loaded", None), bool):
                # Undeclared: it reads person transitions and sensor values.
                all_persons = all_sensors = True
            elif not store.loaded:
                all_persons = all_sensors = True
            else:
                specific.update(store.watched_value_entity_ids)
                specific.update(store.watched_person_entity_ids)
                specific.update(store.watched_nobody_home_person_ids)
                all_persons = bool(store.nobody_home_uses_household)
        self._specific = frozenset(specific)
        self._all_persons = all_persons
        self._all_sensors = all_sensors
        self._everything = everything
        self.version += 1

    # -- queries ----------------------------------------------------------
    @property
    def specific_entity_ids(self) -> frozenset[str]:
        return self._specific

    @property
    def house_wide(self) -> bool:
        return self._current_house_wide().active

    def classify(
        self, entity_id: str, old_state: Any, new_state: Any
    ) -> EventPriority | None:
        """The event's priority, or ``None`` when no consumer needs it.

        Cheap and synchronous: attribute reads on the event's own ``State``
        objects and set lookups - no disk, no registry, no snapshot.
        """
        if is_critical_change(entity_id, old_state, new_state):
            return EventPriority.CRITICAL
        if (
            self._everything
            or entity_id in self._specific
            or (self._all_persons and entity_id.startswith("person."))
            or (self._all_sensors and entity_id.startswith("sensor."))
        ):
            return EventPriority.LOSSLESS
        house = self._current_house_wide()
        if not house.active:
            return None
        if house.routine or house.proactive_undeclared or entity_id.startswith("person."):
            return EventPriority.LOSSLESS
        if house.proactive:
            proactive = self._data.proactive_context
            if proactive.is_relevant_event(entity_id, device_class_of(new_state)):
                return EventPriority.LOSSLESS
        if (
            entity_id.startswith("sensor.")
            and old_state is not None
            and is_plain_number(old_state)
            and is_plain_number(new_state)
        ):
            return EventPriority.COALESCIBLE
        return EventPriority.LOSSLESS

    # -- helpers ----------------------------------------------------------
    def _current_house_wide(self) -> _HouseWide:
        # Keyed on what the flags derive from, so an options update (a new
        # mapping or an in-place change) or a V12 runtime appearing later
        # never leaves the index stale; parsing happens only on a change.
        options = self._entry.options
        proactive = getattr(self._data, "proactive_context", None)
        key = (
            id(options),
            options.get(CONF_AGENT_EVENT_CATEGORIES),
            options.get(CONF_ROUTINE_DETECTION_ENABLED),
            id(proactive),
            getattr(proactive, "enabled", None) if proactive is not None else None,
        )
        house = self._house_wide
        if house is None or key != self._house_key:
            house = _HouseWide(options, proactive)
            self._house_wide = house
            self._house_key = key
        return house

    def _monitor_store(self) -> Any:
        monitor = getattr(self._data, "monitor_runtime", None)
        store = getattr(monitor, "store", None) if monitor is not None else None
        return store if store is not None else None


__all__ = ("EventInterestIndex",)
