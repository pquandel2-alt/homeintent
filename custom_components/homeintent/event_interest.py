"""Which selected state changes an active HomeIntent consumer needs.

7.9.5 queued every state change of a selected entity, built a snapshot of
the whole house and called every consumer - even when none of them was
active (no event categories, V12 context off, no expected effect, no
monitor goal, no thermal cycle). The index answers in the event-bus
callback, synchronously and without disk or registry access, whether an
event is needed at all and how much it may be shed under load
(``event_priority.EventPriority``).

Consumers and what makes them interested:

- safety devices: always (``CRITICAL``, see ``event_priority``);
- ``EffectMonitor``: the entities of pending expected effects (``PROTECTED``);
- ``ThermalExperienceTracker``: the entities of active cycles;
- ``MonitorGoalStore``: value-change sensors, trigger persons and the
  persons of nobody-home goals (all ``person.*`` when such a goal follows
  the configured household); before the store is loaded every person and
  sensor counts;
- configured situation categories: only their explicit domain/state matrix;
- enabled V12 context: only events accepted by ``is_relevant_event``.

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


_OPEN_DEVICE_CLASSES = frozenset({"door", "window", "garage_door", "opening"})
_OPEN_STATES = frozenset({"on", "open", "opening"})
_ACTIVE_STATES = frozenset({"on", "open", "opening", "heating", "cooling", "playing"})
_LONG_RUNNING_DOMAINS = frozenset({
    "climate", "cover", "fan", "humidifier", "light", "media_player",
    "switch", "vacuum", "water_heater",
})


class _InterestConfig:
    """Cached category and V12 flags derived from entry options/runtime."""

    __slots__ = ("active", "categories", "routine", "proactive", "proactive_undeclared")

    def __init__(self, options: Mapping[str, Any], proactive: Any) -> None:
        categories: frozenset[str]
        try:
            categories = parse_event_categories(options.get(CONF_AGENT_EVENT_CATEGORIES, ""))
        except ValueError:
            categories = frozenset()
        enabled = getattr(proactive, "enabled", None) if proactive is not None else None
        self.categories = categories
        self.proactive_undeclared = proactive is not None and not isinstance(enabled, bool)
        self.proactive = enabled is True and callable(
            getattr(proactive, "is_relevant_event", None)
        )
        if enabled is True and not self.proactive:
            self.proactive_undeclared = True
        # Routine statistics are intentionally broad, but only when their
        # own output category is enabled (not merely any category).
        self.routine = "routine_anomaly" in categories and bool(
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
        self._house_wide: _InterestConfig | None = None
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

    @property
    def category_filter_active(self) -> bool:
        """Whether a negative result came through an active broad filter."""
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
            return EventPriority.PROTECTED
        house = self._current_house_wide()
        if not house.active:
            return None
        if house.routine:
            return EventPriority.CATEGORY
        if house.proactive:
            proactive = self._data.proactive_context
            if proactive.is_relevant_event(entity_id, device_class_of(new_state)):
                return EventPriority.CATEGORY
        if _category_relevant(house.categories, entity_id, old_state, new_state):
            return EventPriority.CATEGORY
        if house.proactive_undeclared:
            if (
                entity_id.startswith("sensor.")
                and old_state is not None
                and is_plain_number(old_state)
                and is_plain_number(new_state)
            ):
                return EventPriority.COALESCIBLE
            return EventPriority.CATEGORY
        return None

    # -- helpers ----------------------------------------------------------
    def _current_house_wide(self) -> _InterestConfig:
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
            house = _InterestConfig(options, proactive)
            self._house_wide = house
            self._house_key = key
        return house

    def _monitor_store(self) -> Any:
        monitor = getattr(self._data, "monitor_runtime", None)
        store = getattr(monitor, "store", None) if monitor is not None else None
        return store if store is not None else None


def _state_text(state: Any) -> str | None:
    value = getattr(state, "state", None)
    return value.casefold() if isinstance(value, str) else None


def _is_transition(old_state: Any, new_state: Any, values: frozenset[str]) -> bool:
    new = _state_text(new_state)
    return new in values and _state_text(old_state) != new


def _category_relevant(
    categories: frozenset[str], entity_id: str, old_state: Any, new_state: Any
) -> bool:
    """Return category interest using only event-local, cached information.

    Person and climate edges are support events: they preserve the historical
    occupancy/heating view for a later opening/light event in the same burst.
    ``expected_effect_missing`` deliberately has no matrix row; active effects
    enter through the concrete-consumer index above.
    """
    domain = entity_id.split(".", 1)[0]
    device_class = device_class_of(new_state) or device_class_of(old_state)
    if "device_unavailable" in categories and _is_transition(
        old_state, new_state, frozenset({"unavailable"})
    ):
        return True
    opening_edge = (
        device_class in _OPEN_DEVICE_CLASSES
        and _is_transition(old_state, new_state, _OPEN_STATES)
    )
    if "opening_while_away" in categories and (opening_edge or domain == "person"):
        return True
    if "window_heating" in categories and (opening_edge or domain == "climate"):
        return True
    if "light_unoccupied" in categories and (
        (domain == "light" and _is_transition(old_state, new_state, _ACTIVE_STATES))
        or domain == "person"
    ):
        return True
    if "long_running_state" in categories and domain in _LONG_RUNNING_DOMAINS:
        return True
    # ``safety``/``safety_alarm`` are handled by ``is_critical_change``.
    return False


__all__ = ("EventInterestIndex",)
