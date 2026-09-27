"""Closed allow-list for direct device services used in automations.

This is deliberately not a generic ``domain.service`` escape hatch. A
service can be serialized only when registered here with target domains and
exact data keys. High-risk actions such as locks, alarms, arbitrary groups
and buttons stay absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..entities import format_spoken_number


@dataclass(frozen=True)
class AutomationOperationSpec:
    target_domains: frozenset[str]
    required_data: frozenset[str] = frozenset()
    optional_data: frozenset[str] = frozenset()


def _spec(domain: str, *required_data: str) -> AutomationOperationSpec:
    return AutomationOperationSpec(frozenset({domain}), frozenset(required_data))


REGISTERED_AUTOMATION_OPERATIONS: dict[tuple[str, str], AutomationOperationSpec] = {
    ("media_player", "media_play"): _spec("media_player"),
    ("media_player", "media_pause"): _spec("media_player"),
    ("media_player", "media_stop"): _spec("media_player"),
    ("media_player", "volume_mute"): _spec("media_player", "is_volume_muted"),
    ("media_player", "volume_set"): _spec("media_player", "volume_level"),
    ("media_player", "select_source"): _spec("media_player", "source"),
    ("vacuum", "start"): _spec("vacuum"),
    ("vacuum", "pause"): _spec("vacuum"),
    ("vacuum", "stop"): _spec("vacuum"),
    ("vacuum", "locate"): _spec("vacuum"),
    ("vacuum", "return_to_base"): _spec("vacuum"),
    ("vacuum", "set_fan_speed"): _spec("vacuum", "fan_speed"),
    ("scene", "turn_on"): _spec("scene"),
    ("fan", "set_preset_mode"): _spec("fan", "preset_mode"),
    ("fan", "oscillate"): _spec("fan", "oscillating"),
    ("fan", "set_direction"): _spec("fan", "direction"),
    ("climate", "set_hvac_mode"): _spec("climate", "hvac_mode"),
    ("climate", "set_temperature"): _spec("climate", "temperature"),
    ("climate", "set_preset_mode"): _spec("climate", "preset_mode"),
    ("climate", "set_fan_mode"): _spec("climate", "fan_mode"),
    ("climate", "set_swing_mode"): _spec("climate", "swing_mode"),
    ("cover", "set_cover_tilt_position"): _spec("cover", "tilt_position"),
    ("humidifier", "set_humidity"): _spec("humidifier", "humidity"),
    ("humidifier", "set_mode"): _spec("humidifier", "mode"),
    ("water_heater", "set_temperature"): _spec("water_heater", "temperature"),
    ("water_heater", "set_operation_mode"): _spec("water_heater", "operation_mode"),
    ("number", "set_value"): _spec("number", "value"),
    ("input_number", "set_value"): _spec("input_number", "value"),
    ("select", "select_option"): _spec("select", "option"),
    ("valve", "open_valve"): _spec("valve"),
    ("valve", "close_valve"): _spec("valve"),
    ("valve", "set_valve_position"): _spec("valve", "position"),
    ("lawn_mower", "start_mowing"): _spec("lawn_mower"),
    ("lawn_mower", "pause"): _spec("lawn_mower"),
    ("lawn_mower", "dock"): _spec("lawn_mower"),
    ("camera", "play_stream"): _spec("camera", "media_player"),
    ("notify", "send_message"): _spec("notify", "message"),
    # Home Assistant timer helpers (``timer.*``), not HomeIntent's own timers.
    ("timer", "start"): _spec("timer"),
    ("timer", "pause"): _spec("timer"),
    ("timer", "cancel"): _spec("timer"),
}

_OPERATION_LABELS_DE = {
    ("media_player", "media_play"): "die Wiedergabe starten",
    ("media_player", "media_pause"): "die Wiedergabe pausieren",
    ("media_player", "media_stop"): "die Wiedergabe stoppen",
    ("media_player", "volume_mute"): "die Stummschaltung einstellen",
    ("media_player", "volume_set"): "die Lautstärke einstellen",
    ("media_player", "select_source"): "die Quelle auswählen",
    ("vacuum", "start"): "die Reinigung starten",
    ("vacuum", "pause"): "die Reinigung pausieren",
    ("vacuum", "stop"): "die Reinigung stoppen",
    ("vacuum", "locate"): "ein Suchsignal ausgeben",
    ("vacuum", "return_to_base"): "zur Ladestation fahren",
    ("vacuum", "set_fan_speed"): "die Saugstufe einstellen",
    ("scene", "turn_on"): "die Szene aktivieren",
    ("fan", "set_preset_mode"): "das Preset einstellen",
    ("fan", "oscillate"): "die Oszillation einstellen",
    ("fan", "set_direction"): "die Richtung einstellen",
    ("climate", "set_hvac_mode"): "den Betriebsmodus einstellen",
    ("climate", "set_temperature"): "die Temperatur einstellen",
    ("climate", "set_preset_mode"): "das Preset einstellen",
    ("climate", "set_fan_mode"): "den Lüftermodus einstellen",
    ("climate", "set_swing_mode"): "den Schwenkmodus einstellen",
    ("cover", "set_cover_tilt_position"): "die Lamellenposition einstellen",
    ("humidifier", "set_humidity"): "die Zielfeuchte einstellen",
    ("humidifier", "set_mode"): "den Modus einstellen",
    ("water_heater", "set_temperature"): "die Warmwassertemperatur einstellen",
    ("water_heater", "set_operation_mode"): "den Betriebsmodus einstellen",
    ("number", "set_value"): "den Wert einstellen",
    ("input_number", "set_value"): "den Wert einstellen",
    ("select", "select_option"): "die Option auswählen",
    ("valve", "open_valve"): "das Ventil öffnen",
    ("valve", "close_valve"): "das Ventil schließen",
    ("valve", "set_valve_position"): "die Ventilposition einstellen",
    ("lawn_mower", "start_mowing"): "das Mähen starten",
    ("lawn_mower", "pause"): "das Mähen pausieren",
    ("lawn_mower", "dock"): "zur Ladestation fahren",
    ("camera", "play_stream"): "den Kamerastream anzeigen",
    ("notify", "send_message"): "eine Nachricht senden",
    ("timer", "start"): "den Timer starten",
    ("timer", "pause"): "den Timer pausieren",
    ("timer", "cancel"): "den Timer abbrechen",
}

_DATA_LABELS_DE = {
    "volume_level": "Lautstärke", "source": "Quelle", "fan_speed": "Saugstufe",
    "preset_mode": "Preset", "oscillating": "Oszillation", "direction": "Richtung",
    "hvac_mode": "Betriebsmodus", "fan_mode": "Lüftermodus", "swing_mode": "Schwenkmodus",
    "tilt_position": "Lamellenposition", "humidity": "Zielfeuchte", "mode": "Modus",
    "temperature": "Temperatur", "operation_mode": "Betriebsmodus", "value": "Wert",
    "option": "Option", "position": "Position", "media_player": "Wiedergabeziel",
    "message": "Nachricht", "is_volume_muted": "Stummschaltung",
}


def describe_registered_operation(
    service_domain: str | None, service_name: str | None, data: Mapping[str, object]
) -> str:
    """Return a German preview for an already validated operation."""
    label = _OPERATION_LABELS_DE.get((service_domain or "", service_name or ""), "die Aktion ausführen")
    details: list[str] = []
    for key, value in data.items():
        spoken_value: object = value
        if key == "volume_level" and isinstance(value, (int, float)):
            spoken_value = f"{round(value * 100)} Prozent"
        elif key in {"humidity", "position", "tilt_position"}:
            spoken_value = f"{value} Prozent"
        elif key == "temperature":
            spoken_value = f"{format_spoken_number(value)} Grad"
        elif isinstance(value, float):
            spoken_value = format_spoken_number(value)
        elif key == "message" and isinstance(value, str):
            spoken_value = f"„{value.strip().rstrip('.!')}“"
        elif isinstance(value, bool):
            spoken_value = "ein" if value else "aus"
        details.append(f"{_DATA_LABELS_DE.get(key, key)} {spoken_value}")
    return label + (" – " + ", ".join(details) if details else "")


def validate_registered_operation(
    service_domain: str | None,
    service_name: str | None,
    data: Mapping[str, object],
    target_domains: frozenset[str] | None = None,
) -> bool:
    """Validate one closed operation without importing Home Assistant."""
    if service_domain is None or service_name is None:
        return False
    spec = REGISTERED_AUTOMATION_OPERATIONS.get((service_domain, service_name))
    if spec is None:
        return False
    keys = frozenset(data)
    if not spec.required_data <= keys or not keys <= spec.required_data | spec.optional_data:
        return False
    if target_domains is not None and (not target_domains or not target_domains <= spec.target_domains):
        return False
    for value in data.values():
        if not isinstance(value, (str, int, float, bool)) or (
            isinstance(value, str) and len(value) > 1000
        ):
            return False
    volume = data.get("volume_level")
    if volume is not None and (not isinstance(volume, (int, float)) or not 0 <= volume <= 1):
        return False
    for key in ("humidity", "position", "tilt_position"):
        value = data.get(key)
        if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 100):
            return False
    return True


def operation_registry_is_consistent() -> bool:
    """Return whether allow-list, preview labels and data vocabulary agree."""
    registered = frozenset(REGISTERED_AUTOMATION_OPERATIONS)
    if frozenset(_OPERATION_LABELS_DE) != registered:
        return False
    data_keys = frozenset(
        key
        for spec in REGISTERED_AUTOMATION_OPERATIONS.values()
        for key in spec.required_data | spec.optional_data
    )
    if not data_keys <= frozenset(_DATA_LABELS_DE):
        return False
    high_risk_domains = frozenset({"alarm_control_panel", "button", "group", "lock"})
    return all(
        domain not in high_risk_domains
        and not spec.target_domains & high_risk_domains
        for (domain, _service), spec in REGISTERED_AUTOMATION_OPERATIONS.items()
    )
