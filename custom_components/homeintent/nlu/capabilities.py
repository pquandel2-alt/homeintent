"""Capability System (v2 plan Phase 9): what an entity can actually do,
derived deterministically from its domain and its raw HA state attributes -
never guessed from its name or domain alone. A later phase (Command
Validator, Phase 11) reads the resulting set off ``EntitySnapshot.capabilities``
to reject a command the entity structurally can't perform (e.g. "mach Lampe y
blau" on a brightness-only light) as UNSUPPORTED_CAPABILITY instead of
silently building a wrong or no-op ServiceCall.

``derive_capabilities()`` takes plain domain/device_class/attributes rather
than a full ``EntitySnapshot`` because it runs *while* ``hass_entities.py``
builds one (its result fills the snapshot's own ``capabilities`` field, the
same "prepare inputs, construct once" shape already used there for aliases/
area). Kept free of Home Assistant/hassil imports (same boundary as
nlu/frame.py) so it stays unit-testable without mocking ``hass``.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Mapping

from .primitives import SemanticProperty


class Capability(Enum):
    TURN_ON = auto()
    TURN_OFF = auto()
    BRIGHTNESS = auto()
    COLOR = auto()
    COLOR_TEMPERATURE = auto()
    POSITION = auto()
    TEMPERATURE = auto()
    HUMIDITY = auto()
    FAN_SPEED = auto()
    POWER = auto()
    ENERGY = auto()
    CREATE_EVENT = auto()
    DELETE_EVENT = auto()
    UPDATE_EVENT = auto()
    SET_VALUE = auto()
    SELECT_OPTION = auto()
    PRESS = auto()
    OPEN = auto()
    CLOSE = auto()
    START = auto()
    PAUSE = auto()
    DOCK = auto()


# HA light ColorMode values (homeassistant.components.light.ColorMode) that
# imply each lighting capability. Referenced by their stable string values
# rather than importing the HA enum, to keep this module hass-free.
_LIGHT_COLOR_MODES = frozenset({"hs", "rgb", "rgbw", "rgbww", "xy"})
_LIGHT_COLOR_TEMP_MODES = frozenset({"color_temp"})
_LIGHT_BRIGHTNESS_MODES = frozenset({"brightness"}) | _LIGHT_COLOR_MODES | _LIGHT_COLOR_TEMP_MODES

# Stable Home Assistant CoverEntityFeature.SET_POSITION bit. The module
# deliberately stays hass-free, so it reads the integer feature mask that
# HA exposes in every State instead of importing Home Assistant's enum.
_COVER_SET_POSITION_FEATURE = 4

# Stable Home Assistant CalendarEntityFeature.CREATE_EVENT bit. Calendar
# entities may be read-only, so the domain alone is deliberately insufficient.
_CALENDAR_CREATE_EVENT_FEATURE = 1
_CALENDAR_DELETE_EVENT_FEATURE = 2
_CALENDAR_UPDATE_EVENT_FEATURE = 4


def derive_capabilities(
    domain: str, device_class: str | None, attributes: Mapping[str, Any]
) -> frozenset[Capability]:
    """Deterministic capability set for one entity.

    Every capability is read off a concrete HA signal - ``supported_color_modes``
    for lights, ``current_position``/``percentage`` presence for covers/fans,
    ``device_class`` for sensors. An entity that doesn't report a signal
    simply doesn't get the capability; nothing here is inferred from the
    domain alone beyond the baseline on/off contract HA itself guarantees
    for light/switch/fan.
    """
    caps: set[Capability] = set()

    if domain in ("light", "switch", "fan", "climate", "humidifier", "input_boolean"):
        caps.add(Capability.TURN_ON)
        caps.add(Capability.TURN_OFF)

    # "script" (Skript-Aktivierung, 2026-08-20): only TURN_ON - a script is
    # *run*, not "switched off" in the same sense a light/switch is; the
    # user's request was specifically activation, and HA's own
    # script.turn_off (cancel a running script) is a distinct, unrequested
    # operation not modelled here (see service_call.py's HassRunScript).
    if domain == "script":
        caps.add(Capability.TURN_ON)

    if domain == "light":
        color_modes = frozenset(attributes.get("supported_color_modes") or ())
        if color_modes & _LIGHT_BRIGHTNESS_MODES:
            caps.add(Capability.BRIGHTNESS)
        if color_modes & _LIGHT_COLOR_MODES:
            caps.add(Capability.COLOR)
        if color_modes & _LIGHT_COLOR_TEMP_MODES:
            caps.add(Capability.COLOR_TEMPERATURE)

    if domain == "cover":
        try:
            supported_features = int(attributes.get("supported_features", 0))
        except (TypeError, ValueError):
            supported_features = 0
        if (
            "current_position" in attributes
            or supported_features & _COVER_SET_POSITION_FEATURE
        ):
            caps.add(Capability.POSITION)

    if domain == "fan" and "percentage" in attributes:
        caps.add(Capability.FAN_SPEED)

    if domain == "sensor":
        if device_class == "temperature":
            caps.add(Capability.TEMPERATURE)
        if device_class == "humidity":
            caps.add(Capability.HUMIDITY)
        if device_class == "power":
            caps.add(Capability.POWER)
        if device_class == "energy":
            caps.add(Capability.ENERGY)

    # A climate entity's "temperature" attribute is its single-setpoint
    # target (climate.set_temperature's own parameter name) - present only
    # for HVAC modes that support one, same "read the real signal, don't
    # infer from domain alone" rule FAN_SPEED already follows above.
    if domain == "climate" and "temperature" in attributes:
        caps.add(Capability.TEMPERATURE)

    if domain == "calendar":
        try:
            supported_features = int(attributes.get("supported_features", 0))
        except (TypeError, ValueError):
            supported_features = 0
        if supported_features & _CALENDAR_CREATE_EVENT_FEATURE:
            caps.add(Capability.CREATE_EVENT)
        if supported_features & _CALENDAR_DELETE_EVENT_FEATURE:
            caps.add(Capability.DELETE_EVENT)
        if supported_features & _CALENDAR_UPDATE_EVENT_FEATURE:
            caps.add(Capability.UPDATE_EVENT)

    if domain in {"number", "input_number"} and "min" in attributes and "max" in attributes:
        caps.add(Capability.SET_VALUE)
    if domain == "select" and attributes.get("options"):
        caps.add(Capability.SELECT_OPTION)
    if domain == "button":
        caps.add(Capability.PRESS)
    if domain == "humidifier" and "humidity" in attributes:
        caps.add(Capability.HUMIDITY)
    if domain == "water_heater" and (
        "temperature" in attributes or "target_temp_high" in attributes
    ):
        caps.add(Capability.TEMPERATURE)

    if domain in {"valve", "lawn_mower"}:
        try:
            supported_features = int(attributes.get("supported_features", 0))
        except (TypeError, ValueError):
            supported_features = 0
        if domain == "valve":
            if supported_features & 1:
                caps.add(Capability.OPEN)
            if supported_features & 2:
                caps.add(Capability.CLOSE)
        else:
            if supported_features & 1:
                caps.add(Capability.START)
            if supported_features & 2:
                caps.add(Capability.PAUSE)
            if supported_features & 4:
                caps.add(Capability.DOCK)

    return frozenset(caps)


# Capability Reasoning (V6 architecture plan, V6.8): which Capability a
# SemanticProperty (nlu/primitives.py, V6.1) requires on a candidate entity -
# the generic, semantics-driven counterpart to validator.py's existing
# per-intent capability lookups (_PERCENT_CAPABILITY_BY_DOMAIN,
# LIGHT_EXTENDED_INTENTS[...].capability etc.), reusing the exact same
# Capability enum rather than a second one. 1:1 by name for every property
# that has a real Capability counterpart. SemanticProperty.BATTERY is
# deliberately absent - see its own docstring in primitives.py: query-only,
# always readable off a sensor's device_class="battery", never gated by a
# capability check.
_PROPERTY_TO_CAPABILITY: dict[SemanticProperty, Capability] = {
    SemanticProperty.BRIGHTNESS: Capability.BRIGHTNESS,
    SemanticProperty.COLOR: Capability.COLOR,
    SemanticProperty.COLOR_TEMPERATURE: Capability.COLOR_TEMPERATURE,
    SemanticProperty.TEMPERATURE: Capability.TEMPERATURE,
    SemanticProperty.POSITION: Capability.POSITION,
    SemanticProperty.POWER: Capability.POWER,
    SemanticProperty.ENERGY: Capability.ENERGY,
    SemanticProperty.HUMIDITY: Capability.HUMIDITY,
    SemanticProperty.FAN_SPEED: Capability.FAN_SPEED,
}


def required_capability_for_property(property: SemanticProperty) -> Capability | None:
    """The Capability a candidate entity must have to support ``property`` -
    ``None`` for properties with no capability-gating concept (BATTERY)."""
    return _PROPERTY_TO_CAPABILITY.get(property)


_ABILITY_PHRASES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"TURN_ON", "TURN_OFF"}), "ein- und ausschalten"),
    (frozenset({"BRIGHTNESS"}), "dimmen"),
    (frozenset({"COLOR"}), "farbig stellen"),
    (frozenset({"COLOR_TEMPERATURE"}), "in der Lichtfarbe verstellen"),
    (frozenset({"POSITION"}), "auf eine Position fahren"),
    (frozenset({"OPEN", "CLOSE"}), "öffnen und schließen"),
    (frozenset({"TEMPERATURE"}), "auf eine Temperatur stellen"),
    (frozenset({"HUMIDITY"}), "auf eine Luftfeuchte stellen"),
    (frozenset({"FAN_SPEED"}), "in der Stufe verstellen"),
    (frozenset({"SET_VALUE"}), "auf einen Wert stellen"),
    (frozenset({"SELECT_OPTION"}), "auf eine Option stellen"),
    (frozenset({"PRESS"}), "auslösen"),
    (frozenset({"START"}), "starten"),
    (frozenset({"PAUSE"}), "anhalten"),
    (frozenset({"DOCK"}), "zur Station schicken"),
)


def describe_abilities(name: str, capabilities: frozenset[str] | set[str]) -> str:
    """Honest reason from the device's real capabilities (7.3.3):
    „Flurlicht lässt sich nur ein- und ausschalten.“"""
    phrases: list[str] = []
    for needed, phrase in _ABILITY_PHRASES:
        if needed & set(capabilities) and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return f"{name} kann ich nicht steuern, nur abfragen."
    if len(phrases) == 1:
        listed = phrases[0]
    else:
        listed = ", ".join(phrases[:-1]) + " und " + phrases[-1]
    return f"{name} lässt sich nur {listed}."
