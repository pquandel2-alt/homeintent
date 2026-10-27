"""Phase 9 (v2 plan): Capability System - derive_capabilities(). Pins which
HA signal maps to which Capability per domain, and that entities without the
signal don't get the capability guessed."""

from __future__ import annotations

from homeintent.nlu.capabilities import Capability, derive_capabilities, required_capability_for_property
from homeintent.nlu.primitives import SemanticProperty


def test_light_without_color_modes_has_only_on_off():
    caps = derive_capabilities("light", None, {})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF}


def test_brightness_only_light_has_brightness_but_not_color():
    caps = derive_capabilities("light", None, {"supported_color_modes": ["brightness"]})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF, Capability.BRIGHTNESS}


def test_color_light_has_brightness_and_color():
    caps = derive_capabilities("light", None, {"supported_color_modes": ["hs"]})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF, Capability.BRIGHTNESS, Capability.COLOR}


def test_color_temp_light_has_brightness_and_color_temperature_not_color():
    caps = derive_capabilities("light", None, {"supported_color_modes": ["color_temp"]})
    assert caps == {
        Capability.TURN_ON,
        Capability.TURN_OFF,
        Capability.BRIGHTNESS,
        Capability.COLOR_TEMPERATURE,
    }


def test_switch_has_only_on_off():
    caps = derive_capabilities("switch", None, {})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF}


def test_script_has_only_turn_on_not_turn_off():
    # Skript-Aktivierung (2026-08-20): a script is *run*, not switched off
    # the way a light/switch is - script.turn_off (cancel) is a distinct,
    # unrequested operation, deliberately not modelled here.
    caps = derive_capabilities("script", None, {})
    assert caps == {Capability.TURN_ON}


def test_cover_with_position_attribute_has_position():
    caps = derive_capabilities("cover", None, {"current_position": 40})
    assert caps == {Capability.POSITION}


def test_cover_set_position_feature_has_position_without_current_value():
    caps = derive_capabilities("cover", None, {"supported_features": 4})
    assert caps == {Capability.POSITION}


def test_cover_without_position_attribute_has_no_capabilities():
    caps = derive_capabilities("cover", None, {})
    assert caps == frozenset()


def test_fan_with_percentage_has_fan_speed():
    caps = derive_capabilities("fan", None, {"percentage": 50})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF, Capability.FAN_SPEED}


def test_fan_without_percentage_has_only_on_off():
    caps = derive_capabilities("fan", None, {})
    assert caps == {Capability.TURN_ON, Capability.TURN_OFF}


def test_temperature_sensor_has_temperature_capability():
    caps = derive_capabilities("sensor", "temperature", {})
    assert caps == {Capability.TEMPERATURE}


def test_humidity_sensor_has_humidity_capability():
    caps = derive_capabilities("sensor", "humidity", {})
    assert caps == {Capability.HUMIDITY}


def test_power_sensor_has_power_capability():
    caps = derive_capabilities("sensor", "power", {})
    assert caps == {Capability.POWER}


def test_energy_sensor_has_energy_capability():
    caps = derive_capabilities("sensor", "energy", {})
    assert caps == {Capability.ENERGY}


def test_sensor_without_relevant_device_class_has_no_capabilities():
    caps = derive_capabilities("sensor", "battery", {})
    assert caps == frozenset()


def test_calendar_create_event_requires_the_real_supported_feature_bit():
    assert derive_capabilities(
        "calendar", None, {"supported_features": 1}
    ) == {Capability.CREATE_EVENT}
    assert derive_capabilities(
        "calendar", None, {"supported_features": 0}
    ) == frozenset()


def test_calendar_mutation_capabilities_use_the_real_feature_bits():
    assert derive_capabilities(
        "calendar", None, {"supported_features": 7}
    ) == {Capability.CREATE_EVENT, Capability.DELETE_EVENT, Capability.UPDATE_EVENT}


def test_extended_domain_capabilities_are_derived_from_concrete_attributes():
    assert Capability.SET_VALUE in derive_capabilities(
        "number", None, {"min": 0, "max": 10}
    )
    assert Capability.SELECT_OPTION in derive_capabilities(
        "select", None, {"options": ["Eco"]}
    )
    assert derive_capabilities("button", None, {}) == {Capability.PRESS}
    assert derive_capabilities(
        "valve", None, {"supported_features": 3}
    ) == {Capability.OPEN, Capability.CLOSE}
    assert derive_capabilities(
        "lawn_mower", None, {"supported_features": 7}
    ) == {Capability.START, Capability.PAUSE, Capability.DOCK}


def test_media_player_capabilities_come_only_from_supported_features():
    assert derive_capabilities("media_player", None, {}) == set()
    caps = derive_capabilities(
        "media_player", None, {"supported_features": 1 | 128 | 4096 | 16384}
    )
    assert caps == {
        Capability.PAUSE,
        Capability.TURN_ON,
        Capability.STOP,
        Capability.PLAY,
    }


def test_required_capability_for_property_covers_every_property_with_a_capability_counterpart():
    assert required_capability_for_property(SemanticProperty.BRIGHTNESS) is Capability.BRIGHTNESS
    assert required_capability_for_property(SemanticProperty.COLOR) is Capability.COLOR
    assert required_capability_for_property(SemanticProperty.COLOR_TEMPERATURE) is Capability.COLOR_TEMPERATURE
    assert required_capability_for_property(SemanticProperty.TEMPERATURE) is Capability.TEMPERATURE
    assert required_capability_for_property(SemanticProperty.POSITION) is Capability.POSITION
    assert required_capability_for_property(SemanticProperty.POWER) is Capability.POWER
    assert required_capability_for_property(SemanticProperty.ENERGY) is Capability.ENERGY
    assert required_capability_for_property(SemanticProperty.HUMIDITY) is Capability.HUMIDITY
    assert required_capability_for_property(SemanticProperty.FAN_SPEED) is Capability.FAN_SPEED


def test_required_capability_for_battery_property_is_none():
    """BATTERY is query-only (always readable off device_class="battery"),
    never gated by a capability check - see primitives.py's own docstring."""
    assert required_capability_for_property(SemanticProperty.BATTERY) is None
