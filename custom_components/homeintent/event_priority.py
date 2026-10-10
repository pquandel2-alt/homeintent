"""How much one selected ``state_changed`` event may be shed under load (7.9.6).

The EventRuntime ranks every event it queues:

``CRITICAL``
    A state change of a safety device HomeIntent already treats as an alarm:
    the ``SituationEvaluator`` (``situation.SAFETY_CLASSES``), the V12
    detector (``situation_detection.SAFETY_CLASSES``) and the event summary
    (``event_summary.ALARM_DEVICE_CLASSES``: also safety, tamper and problem
    sensors). No new list - the union of those three. Never dropped because
    of ordinary events; it displaces them when the queue is full.

``LOSSLESS``
    An event a consumer needs exactly, intermediate states included: an
    entity with an expected effect, a monitor goal's sensor or person, an
    active thermal cycle's entities, persons, door and window edges, every
    event while routine detection is on, and every event that is not a plain
    number-to-number change. Never merged; dropped only for a critical event
    once nothing coalescible is left (a counted, logged loss).

``COALESCIBLE``
    A plain numeric value change (``21.3`` -> ``21.4``) of a measurement
    ``sensor`` no consumer watches individually, while only house-wide
    consumers listen: its own evaluation yields no situation
    (``SituationEvaluator`` has no rule for a number-to-number change) and no
    V12 detector rule reads it.
    Consecutive changes of the same entity may be merged into one evaluation
    (first ``old_state``, last ``new_state``); under overload it is the first
    thing to go.
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
    LOSSLESS = "lossless"
    COALESCIBLE = "coalescible"


CRITICAL_DEVICE_CLASSES = frozenset(
    {*SAFETY_CLASSES, *DETECTOR_SAFETY_CLASSES, *ALARM_DEVICE_CLASSES}
)
# The alarm states ``normalize_state_change`` maps to ``SAFETY_ALARM``.
_ALARM_STATES = frozenset({"on", "detected", "alarm"})


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
        _state_text(new_state) in _ALARM_STATES
        or _state_text(old_state) in _ALARM_STATES
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


def _state_text(state: Any) -> str | None:
    text = getattr(state, "state", None)
    return text.casefold() if isinstance(text, str) else None


__all__ = (
    "CRITICAL_DEVICE_CLASSES",
    "EventPriority",
    "device_class_of",
    "is_critical_change",
    "is_plain_number",
)
