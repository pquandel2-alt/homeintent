"""Which selected state changes any active HomeIntent consumer needs.

7.9.5 queued every state change of a selected entity, built a snapshot of
the whole house and called every consumer - even when none of them was
active. 7.9.6 added this index; 7.9.7 makes the agent event categories and
the V12 context *targeted*: a configured category no longer makes the whole
house a consumer, it asks only for the state changes its rule can turn into
a situation (or reads as context). The index answers in the event-bus
callback, synchronously and without disk or registry access, whether an
event is needed at all and with which rank (``event_priority``); the rank is
the highest any interested consumer asks for.

Consumers and what makes them interested:

- safety devices: always (``CRITICAL``, see ``event_priority``);
- ``PROTECTED`` (watched directly, pushed by the providers): the entities of
  pending expected effects (``EffectMonitor``), of active thermal cycles
  (``ThermalExperienceTracker``), the value-change sensors, trigger persons
  and nobody-home persons of monitor goals (``MonitorGoalStore``; all
  ``person.*`` when such a goal follows the configured household; before
  the store is loaded every person and sensor);
- ``ROUTINE``: routine statistics while they can raise or explain a
  situation of every entity (routine detection on and ``routine_anomaly``
  or ``device_unavailable`` configured) - every selected entity, a plain
  number-to-number sensor change only ``COALESCIBLE``; an enabled V12
  context's ``is_relevant_event()`` (detector gate and habit triggers);
- ``CATEGORY``: the per-category matrix below, derived from
  ``normalize_state_change()`` and ``SituationEvaluator.evaluate()`` as
  ``SituationRuntime`` calls them.

Category matrix (``CATEGORY_INTEREST``):

``safety`` / ``safety_alarm``
    Only ``SAFETY_ALARM`` events - a device class of
    ``situation.SAFETY_CLASSES`` in an alarm state. Every one of them is
    already ``CRITICAL``, so the categories add nothing; ordinary switches,
    numbers or media players are not needed.
``device_unavailable``
    The new state is ``unavailable`` (``EventQuality.UNAVAILABLE``;
    ``unknown`` yields no situation).
``opening_while_away``
    Edges of a device class in ``situation.OPEN_CLASSES`` (door, window,
    garage door, opening - any domain, covers included), and ``person.*``:
    the rule reads ``occupied`` from the persons at home *when the opening
    happened*; without their changes in the queue an opening evaluated a
    moment later would see a person's later state (7.9.5 future leak).
``window_heating``
    Edges of ``OPEN_CLASSES`` devices, and ``climate.*``: the rule reads the
    heating state of the room's climates at the time of the opening (same
    reason as the persons above; climates never trigger it themselves).
``light_unoccupied``
    ``light.*`` changes to an active state (``situation.ACTIVE_STATES``,
    attribute-only changes of a light that is on included - they are
    evaluated as an activation), and ``person.*`` (occupancy). With routine
    detection on every ``light.*`` change: the light's routine statistics
    explain its situation.

With routine detection on (and any category configured) ``person.*`` is
context for every category: each routine observation records presence and
operating mode at the time of the event.
``long_running_state``
    Nothing: ``SituationRuntime`` evaluates without ``long_state_seconds``,
    so the rule cannot fire from a state change.
``routine_anomaly``
    Nothing without routine detection; with it, every selected entity
    (``ROUTINE``, see above).
``expected_effect_missing``
    Nothing by itself: only the entities of pending expected effects, which
    are ``PROTECTED`` anyway; the timeout is handled by the
    ``EffectMonitor`` without a state change.

A runtime object that exists but is disabled is not a consumer. A consumer
object that does not declare its interest (a test double, a future
consumer) is treated as interested in everything (conservative).

Specific interests are pushed: each provider calls back when its watched
set changes and ``refresh()`` rebuilds one merged set, so the callback does
one membership test. The category flags follow the options mapping and the
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
    state_text,
)
from .situation import ACTIVE_STATES, OPEN_CLASSES


_LOGGER = logging.getLogger(__name__)

# What each agent event category needs besides critical and protected
# events (see the module docstring for the derivation).
UNAVAILABLE = "unavailable"
OPENINGS = "openings"
PERSONS = "persons"
CLIMATES = "climates"
ACTIVE_LIGHTS = "active_lights"
CATEGORY_INTEREST: Mapping[str, frozenset[str]] = {
    "safety": frozenset(),
    "safety_alarm": frozenset(),
    "device_unavailable": frozenset({UNAVAILABLE}),
    "opening_while_away": frozenset({OPENINGS, PERSONS}),
    "window_heating": frozenset({OPENINGS, CLIMATES}),
    "light_unoccupied": frozenset({ACTIVE_LIGHTS, PERSONS}),
    "long_running_state": frozenset(),
    "routine_anomaly": frozenset(),
    "expected_effect_missing": frozenset(),
}
# Categories whose situations routine statistics can raise or explain for
# any entity: with routine detection on they need every selected entity.
_ROUTINE_HOUSE_WIDE = frozenset({"routine_anomaly", "device_unavailable"})


class _Consumers:
    """Category and V12 flags derived from options and the V12 runtime."""

    __slots__ = (
        "categories", "routine", "proactive", "proactive_undeclared", "needs",
        "all_lights", "active",
    )

    def __init__(self, options: Mapping[str, Any], proactive: Any) -> None:
        categories: frozenset[str]
        try:
            categories = parse_event_categories(options.get(CONF_AGENT_EVENT_CATEGORIES, ""))
        except ValueError:
            categories = frozenset()
        self.categories = categories
        enabled = getattr(proactive, "enabled", None) if proactive is not None else None
        self.proactive_undeclared = proactive is not None and not isinstance(enabled, bool)
        self.proactive = enabled is True and callable(
            getattr(proactive, "is_relevant_event", None)
        )
        if enabled is True and not self.proactive:
            self.proactive_undeclared = True
        # Routine statistics run inside the category evaluation only.
        routine_on = bool(categories) and bool(
            options.get(CONF_ROUTINE_DETECTION_ENABLED, False)
        )
        self.routine = routine_on and bool(categories & _ROUTINE_HOUSE_WIDE)
        needs: set[str] = set()
        for category in categories:
            needs.update(CATEGORY_INTEREST.get(category, ()))
        if routine_on:
            # Every routine observation records presence and mode.
            needs.add(PERSONS)
        self.needs = frozenset(needs)
        self.all_lights = routine_on and ACTIVE_LIGHTS in needs
        self.active = (
            bool(self.needs) or self.routine or self.proactive or self.proactive_undeclared
        )

    def category_wants(self, entity_id: str, old_state: Any, new_state: Any) -> bool:
        """``True`` when a configured category needs this change."""
        needs = self.needs
        if not needs:
            return False
        domain = entity_id.partition(".")[0]
        if UNAVAILABLE in needs and state_text(new_state) == "unavailable":
            return True
        if PERSONS in needs and domain == "person":
            return True
        if CLIMATES in needs and domain == "climate":
            return True
        if ACTIVE_LIGHTS in needs and domain == "light" and (
            self.all_lights or state_text(new_state) in ACTIVE_STATES
        ):
            return True
        if OPENINGS in needs:
            device_class = device_class_of(new_state) or device_class_of(old_state)
            if device_class in OPEN_CLASSES:
                return True
        return False


class EventInterestIndex:
    """In-memory consumer index for the EventRuntime callback."""

    def __init__(self, entry: Any, runtime_data: Any) -> None:
        self._entry = entry
        self._data = runtime_data
        self._specific: frozenset[str] = frozenset()
        self._all_persons = False
        self._all_sensors = False
        self._everything = False
        self._consumers: _Consumers | None = None
        self._consumers_key: tuple[object, ...] | None = None
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
        """Rebuild the specific (protected) entity set from the providers."""
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
        """Any category or V12 consumer is active (not necessarily for all)."""
        return self._current_consumers().active

    @property
    def category_active(self) -> bool:
        """Configured categories filter events (``filtered_by_category``)."""
        return bool(self._current_consumers().categories)

    def classify(
        self, entity_id: str, old_state: Any, new_state: Any
    ) -> EventPriority | None:
        """The event's rank, or ``None`` when no consumer needs it.

        Cheap and synchronous: attribute reads on the event's own ``State``
        objects and set lookups - no disk, no registry, no snapshot, no
        scan of the selected entities.
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
        consumers = self._current_consumers()
        if not consumers.active:
            return None
        if consumers.proactive_undeclared:
            return EventPriority.ROUTINE
        if consumers.proactive:
            proactive = self._data.proactive_context
            if proactive.is_relevant_event(entity_id, device_class_of(new_state)):
                return EventPriority.ROUTINE
        plain_number = consumers.routine and (
            entity_id.startswith("sensor.")
            and old_state is not None
            and is_plain_number(old_state)
            and is_plain_number(new_state)
        )
        if consumers.routine and not plain_number:
            return EventPriority.ROUTINE
        if consumers.category_wants(entity_id, old_state, new_state):
            return EventPriority.CATEGORY
        if plain_number:
            return EventPriority.COALESCIBLE
        return None

    # -- helpers ----------------------------------------------------------
    def _current_consumers(self) -> _Consumers:
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
        consumers = self._consumers
        if consumers is None or key != self._consumers_key:
            consumers = _Consumers(options, proactive)
            self._consumers = consumers
            self._consumers_key = key
        return consumers

    def _monitor_store(self) -> Any:
        monitor = getattr(self._data, "monitor_runtime", None)
        store = getattr(monitor, "store", None) if monitor is not None else None
        return store if store is not None else None


__all__ = ("EventInterestIndex",)
