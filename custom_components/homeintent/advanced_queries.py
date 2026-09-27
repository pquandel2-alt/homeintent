"""Compositional household-health queries over the per-turn snapshot."""

from __future__ import annotations

import re
from datetime import datetime

from .entities import EntitySnapshot, format_spoken_number, normalize_for_compare
from .productivity import parse_duration_seconds


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " und " + names[-1]


def _elapsed_seconds(now: datetime, changed: datetime) -> float:
    if now.tzinfo is None and changed.tzinfo is not None:
        now = now.replace(tzinfo=changed.tzinfo)
    elif now.tzinfo is not None and changed.tzinfo is None:
        changed = changed.replace(tzinfo=now.tzinfo)
    return (now - changed).total_seconds()


def match_advanced_query(
    text: str, entities: list[EntitySnapshot], now: datetime
) -> str | None:
    value = normalize_for_compare(text)
    if re.search(r"\b(?:nicht erreichbar|nicht verfuegbar|unverfuegbar|offline)\b", value):
        affected = [e.friendly_name for e in entities if e.state in {"unavailable", "unknown"}]
        return (
            "Alle für HomeIntent ausgewählten Geräte sind erreichbar."
            if not affected else "Nicht erreichbar sind: " + _join(affected) + "."
        )

    battery = re.search(r"\b(?:batterie|batterien|akkus?)\b.*?\b(?:unter|weniger als)\s+(\d{1,3})", value)
    if battery:
        threshold = min(100, int(battery.group(1)))
        affected = []
        for entity in entities:
            if entity.device_class != "battery":
                continue
            try:
                level = float(entity.state)
            except ValueError:
                continue
            if level < threshold:
                affected.append(f"{entity.friendly_name} mit {level:g} Prozent")
        return (
            f"Keine Batterie liegt unter {threshold} Prozent."
            if not affected else "Unter dem Grenzwert liegen: " + _join(affected) + "."
        )

    if re.search(r"\b(?:meisten|hoechsten)\b.*\b(?:strom|leistung|verbrauch)\b|"
                 r"\b(?:strom|leistung|verbrauch)\b.*\b(?:meisten|hoechsten)\b", value):
        readings: list[tuple[float, EntitySnapshot]] = []
        device_question = re.search(r"\b(?:geraet|verbraucher)\b", value) is not None
        for entity in entities:
            # Current consumption is power (W/kW); an energy meter (kWh) is a
            # cumulative counter and never "the highest current value" (F12).
            if entity.device_class != "power" and entity.unit not in {"W", "kW"}:
                continue
            if entity.device_class == "energy" or entity.unit in {"Wh", "kWh", "MWh"}:
                continue
            if device_question and re.search(
                r"\b(?:haus|gesamt\w*|netz\w*)\b", normalize_for_compare(entity.friendly_name)
            ):
                continue
            try:
                number = float(entity.state.replace(",", "."))
            except ValueError:
                continue
            watts = number * 1000 if entity.unit == "kW" else number
            readings.append((watts, entity))
        if not readings:
            return "Ich finde keinen ausgewählten Leistungssensor mit einem Zahlenwert."
        watts, entity = max(readings, key=lambda item: item[0])
        return (
            f"Den höchsten aktuellen Wert hat {entity.friendly_name} mit "
            f"{format_spoken_number(round(watts, 1))} Watt."
        )

    if re.search(r"\bfenster\b", value) and re.search(r"\b(?:seit|laenger als|wie lange)\b", value):
        seconds = parse_duration_seconds(text)
        open_windows = [
            entity for entity in entities
            if entity.domain == "binary_sensor" and entity.device_class == "window"
            and entity.state == "on"
        ]
        if seconds:
            open_windows = [
                entity for entity in open_windows
                if entity.last_changed is not None
                and _elapsed_seconds(now, entity.last_changed) >= seconds
            ]
        if not open_windows:
            return "Kein ausgewähltes Fenster erfüllt diese Bedingung."
        details = []
        for entity in open_windows:
            if entity.last_changed is None:
                details.append(entity.friendly_name)
            else:
                minutes = max(0, int(_elapsed_seconds(now, entity.last_changed) // 60))
                details.append(f"{entity.friendly_name} seit {minutes} Minuten")
        return "Offen sind: " + _join(details) + "."
    return None
