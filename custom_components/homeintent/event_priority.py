"""How much one selected ``state_changed`` event may be shed under load.

The EventRuntime ranks every event it queues (7.9.6, split in 7.9.7). The
rank is the highest one any interested consumer asks for; under overload an
incoming event may only displace queued events of a strictly lower rank:

``CRITICAL``
    A state change of a safety device HomeIntent already treats as an alarm:
    the ``SituationEvaluator`` (``situation.SAFETY_CLASSES``), the V12
    detector (``situation_detection.SAFETY_CLASSES``) and the event summary
    (``event_summary.ALARM_DEVICE_CLASSES``: also safety, tamper and problem
    sensors). No new list - the union of those three. Always kept, whether a
    consumer is configured or not; never displaced by any other event.

``PROTECTED``
    An event a concrete consumer watches directly and needs exactly,
    intermediate states included: the entity of a pending expected effect,
    a monitor goal's sensor or person, the persons of a nobody-home goal,
    the entities of an active thermal cycle. Never merged; displaced only by
    a critical event once nothing of a lower rank is left (the last-resort
    emergency bound, counted as ``dropped_protected``).

``ROUTINE``
    Broad but edge-dependent consumers: routine statistics (routine
    detection with its ``routine_anomaly`` category) and events the enabled
    V12 context calls relevant (``is_relevant_event``, habit triggers
    included). Never merged.

``CATEGORY``
    An event needed only because a configured agent event category can
    derive a situation from it, or reads it as house context (persons for
    the presence rules, climates for ``window_heating``). See
    ``event_interest`` for the per-category matrix. Never merged.

``COALESCIBLE``
    A plain numeric value change (``21.3`` -> ``21.4``) of a ``sensor`` that
    only routine statistics read: ``SituationEvaluator`` has no rule for a
    number-to-number change and no V12 detector rule reads it. Consecutive
    changes of the same entity may be merged into one evaluation (first
    ``old_state``, last ``new_state``); under overload it goes first.

7.9.6 had one ``LOSSLESS`` rank for the three middle ranks: an expected
effect could be dropped behind 4096 ordinary category events, and a critical
event displaced the oldest lossless entry even if that was the expected
effect. ``dropped_lossless`` remains as the sum of the three middle ranks.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from .event_summary import ALARM_DEVICE_CLASSES
from .situation import SAFETY_CLASSES
from .situation_detection import SAFETY_CLASSES as DETECTOR_SAFETY_CLASSES


class EventPriority(StrEnum):
    CRITICAL = "critical"
    PROTECTED = "protected"
    ROUTINE = "routine"
    CATEGORY = "category"
    COALESCIBLE = "coalescible"


# Higher is more important; an event displaces only strictly lower ranks.
PRIORITY_RANK: dict[EventPriority, int] = {
    EventPriority.COALESCIBLE: 0,
    EventPriority.CATEGORY: 1,
    EventPriority.ROUTINE: 2,
    EventPriority.PROTECTED: 3,
    EventPriority.CRITICAL: 4,
}
# The ranks that may be displaced, lowest first (critical never is).
EVICTION_ORDER: tuple[EventPriority, ...] = (
    EventPriority.COALESCIBLE,
    EventPriority.CATEGORY,
    EventPriority.ROUTINE,
    EventPriority.PROTECTED,
)

CRITICAL_DEVICE_CLASSES = frozenset(
    {*SAFETY_CLASSES, *DETECTOR_SAFETY_CLASSES, *ALARM_DEVICE_CLASSES}
)
# The alarm states ``normalize_state_change`` maps to ``SAFETY_ALARM``.
_ALARM_STATES = frozenset({"on", "detected", "alarm"})


def higher(first: EventPriority | None, second: EventPriority | None) -> EventPriority | None:
    """The more important of two ranks (``None`` = no interest)."""
    if first is None:
        return second
    if second is None:
        return first
    return first if PRIORITY_RANK[first] >= PRIORITY_RANK[second] else second


def device_class_of(state: Any) -> str | None:
    """The ``device_class`` attribute of a HA ``State`` (no registry read)."""
    attributes = getattr(state, "attributes", None)
    if attributes is None:
        return None
    try:
        value = attributes.get("device_class")
    except AttributeError:
        return None
    return value.casefold() if isinstance(value, str) else None


def is_critical_change(entity_id: str, old_state: Any, new_state: Any) -> bool:
    """A safety device's state change (either edge, so "cleared" too).

    A binary sensor of a critical class always counts; another domain only
    when one side is an alarm state, so a gas *meter* (``sensor``, class
    ``gas``, numeric) is not mistaken for a gas detector.
    """
    device_class = device_class_of(new_state) or device_class_of(old_state)
    if device_class not in CRITICAL_DEVICE_CLASSES:
        return False
    if entity_id.startswith("binary_sensor."):
        return True
    return (
        state_text(new_state) in _ALARM_STATES
        or state_text(old_state) in _ALARM_STATES
    )


def is_plain_number(state: Any) -> bool:
    """``state`` is a finite number (not ``unknown``/``unavailable``)."""
    text = getattr(state, "state", None)
    if not isinstance(text, str) or not text:
        return False
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def state_text(state: Any) -> str | None:
    """The casefolded state string of a HA ``State`` (``None`` if absent)."""
    text = getattr(state, "state", None)
    return text.casefold() if isinstance(text, str) else None


__all__ = (
    "CRITICAL_DEVICE_CLASSES",
    "EVICTION_ORDER",
    "EventPriority",
    "PRIORITY_RANK",
    "device_class_of",
    "higher",
    "is_critical_change",
    "is_plain_number",
    "state_text",
)
