"""Closed, typed numeric measurements that live in entity *attributes*.

A cover's percentage is ``state_attr(cover, "current_position")``, not the
cover's ``open``/``closed`` state; a light's percentage is its ``brightness``
attribute (0-255) and a fan's is ``percentage``.  "50 Prozent" therefore has
no meaning until the grounded entity's domain selects one of these
properties (7.2.0 spec §9-§12, §33).

The mapping below is the *only* place an attribute name comes from.  Spoken
text never becomes an attribute name or a template fragment: the language
layer picks a :class:`MeasurementProperty` member, and the Home Assistant
expression is assembled here from that member, a validated entity id and a
number.  Nothing user supplied is ever interpolated.

Pure, Home-Assistant-free and strictly typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Mapping, Sequence


class MeasurementProperty(Enum):
    """A semantic, attribute-backed percentage property."""

    COVER_POSITION = auto()
    LIGHT_BRIGHTNESS = auto()
    FAN_PERCENTAGE = auto()


class TravelDirection(Enum):
    """Explicitly spoken cover travel direction ("beim Hochfahren")."""

    UP = auto()
    DOWN = auto()


@dataclass(frozen=True)
class MeasurementSpec:
    """How one property is read from Home Assistant."""

    property: MeasurementProperty
    domain: str
    attribute: str
    # Home Assistant stores brightness as 0-255; every property is spoken in
    # percent, so ``attribute_max`` converts the raw attribute to percent.
    attribute_max: float
    subject_noun: str  # "Position", used by explanations only
    minimum: float = 0.0
    maximum: float = 100.0


MEASUREMENTS: Mapping[MeasurementProperty, MeasurementSpec] = {
    MeasurementProperty.COVER_POSITION: MeasurementSpec(
        MeasurementProperty.COVER_POSITION, "cover", "current_position", 100.0, "Position"
    ),
    MeasurementProperty.LIGHT_BRIGHTNESS: MeasurementSpec(
        MeasurementProperty.LIGHT_BRIGHTNESS, "light", "brightness", 255.0, "Helligkeit"
    ),
    MeasurementProperty.FAN_PERCENTAGE: MeasurementSpec(
        MeasurementProperty.FAN_PERCENTAGE, "fan", "percentage", 100.0, "Geschwindigkeit"
    ),
}

_PERCENT_PROPERTY_BY_DOMAIN: Mapping[str, MeasurementProperty] = {
    spec.domain: spec.property for spec in MEASUREMENTS.values()
}

# Home Assistant entity ids: "<domain>.<object_id>", lowercase ascii, digits
# and underscores only.  Anything else is refused before it can reach a
# template string.
_ENTITY_ID_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")


def percent_property_for_domain(domain: str | None) -> MeasurementProperty | None:
    """The attribute-backed percentage a domain means, if it has one."""
    if domain is None:
        return None
    return _PERCENT_PROPERTY_BY_DOMAIN.get(domain)


def spec_for(prop: MeasurementProperty) -> MeasurementSpec:
    return MEASUREMENTS[prop]


def is_valid_value(prop: MeasurementProperty, value: float) -> bool:
    spec = MEASUREMENTS[prop]
    return spec.minimum <= value <= spec.maximum


def safe_entity_id(entity_id: str) -> bool:
    return _ENTITY_ID_RE.fullmatch(entity_id) is not None


def _number(value: float) -> str:
    return f"{value:g}"


def percent_expression(prop: MeasurementProperty, entity_id: str) -> str:
    """Jinja expression of the property in percent for one entity.

    ``None`` (attribute missing/unavailable) never compares true: callers
    wrap the comparison with :func:`comparison_expression`.
    """
    if not safe_entity_id(entity_id):
        raise ValueError("unsafe entity id")
    spec = MEASUREMENTS[prop]
    raw = f"state_attr('{entity_id}', '{spec.attribute}')"
    if spec.attribute_max == 100.0:
        return raw
    return f"(({raw} | float(0)) * 100 / {_number(spec.attribute_max)}) | round(0)"


_OPERATORS: Mapping[str, str] = {
    "EQUAL": "==",
    "ABOVE": ">",
    "BELOW": "<",
    "AT_LEAST": ">=",
    "AT_MOST": "<=",
}


def comparison_expression(
    prop: MeasurementProperty,
    entity_ids: Sequence[str],
    comparator_name: str,
    threshold: float,
) -> str:
    """``{{ ... }}`` template true while any entity satisfies the relation.

    A template trigger fires on the false -> true transition, which is
    exactly "reaches"/"rises above"/"falls below".
    """
    operator = _OPERATORS.get(comparator_name)
    if operator is None or not entity_ids:
        raise ValueError("unsupported comparison")
    spec = MEASUREMENTS[prop]
    parts: list[str] = []
    for entity_id in entity_ids:
        raw = f"state_attr('{entity_id}', '{spec.attribute}')"
        value = percent_expression(prop, entity_id)
        parts.append(f"({raw} is number and {value} {operator} {_number(threshold)})")
    return "{{ " + " or ".join(parts) + " }}"


def direction_condition(
    prop: MeasurementProperty, direction: TravelDirection
) -> str:
    """Condition template for a state trigger on the attribute: movement sense.

    Evaluated with Home Assistant's ``trigger.from_state``/``to_state``.
    """
    spec = MEASUREMENTS[prop]
    operator = ">" if direction is TravelDirection.DOWN else "<"
    return (
        "{{ trigger.from_state is not none and trigger.to_state is not none"
        f" and trigger.from_state.attributes.get('{spec.attribute}') is number"
        f" and trigger.to_state.attributes.get('{spec.attribute}') is number"
        f" and trigger.from_state.attributes.get('{spec.attribute}')"
        f" {operator} trigger.to_state.attributes.get('{spec.attribute}') }}}}"
    )


def arrival_condition(
    prop: MeasurementProperty, comparator_name: str, threshold: float
) -> str:
    """Condition: the *new* attribute value satisfies the relation and the old
    one did not - the same false -> true edge a template trigger has."""
    operator = _OPERATORS.get(comparator_name)
    if operator is None:
        raise ValueError("unsupported comparison")
    spec = MEASUREMENTS[prop]
    scale = "" if spec.attribute_max == 100.0 else f" * 100 / {_number(spec.attribute_max)}"

    def side(state: str) -> str:
        return f"(trigger.{state}.attributes.get('{spec.attribute}') | float(-1){scale})"

    new = side("to_state")
    old = side("from_state")
    return (
        "{{ trigger.to_state is not none"
        f" and trigger.to_state.attributes.get('{spec.attribute}') is number"
        f" and {new} {operator} {_number(threshold)}"
        f" and not (trigger.from_state is not none and {old} {operator} {_number(threshold)}) }}}}"
    )


__all__ = (
    "MEASUREMENTS",
    "MeasurementProperty",
    "MeasurementSpec",
    "TravelDirection",
    "arrival_condition",
    "comparison_expression",
    "direction_condition",
    "is_valid_value",
    "percent_expression",
    "percent_property_for_domain",
    "safe_entity_id",
    "spec_for",
)
