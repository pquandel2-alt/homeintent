"""Pure validation for privacy- and risk-relevant agent options."""

from __future__ import annotations

from datetime import time
from pathlib import PurePath
from typing import Any, Iterable, cast


EVENT_CATEGORIES = frozenset(
    {
        "safety",
        "safety_alarm",
        "device_unavailable",
        "opening_while_away",
        "window_heating",
        "light_unoccupied",
        "long_running_state",
        "routine_anomaly",
        "expected_effect_missing",
    }
)

MAX_LONG_RUNNING_MINUTES = 10080


def validate_quiet_time(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Ruhezeit muss HH:MM sein")
    try:
        hour, minute = value.split(":", 1)
        time(int(hour), int(minute))
    except (TypeError, ValueError) as err:
        raise ValueError("Ruhezeit muss HH:MM sein") from err
    return value


def validate_documents_directory(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Dokumentverzeichnis muss relativ sein")
    path = PurePath(value.strip())
    if path.is_absolute() or not path.parts or ".." in path.parts or str(path) in {"", "."}:
        raise ValueError("Dokumentverzeichnis muss unterhalb des HA-Konfigurationsordners liegen")
    return str(path)


def validate_mqtt_topic(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("MQTT-Thema muss Text sein")
    topic = value.strip()
    if not topic or len(topic) > 256 or "#" in topic or "+" in topic or "\x00" in topic:
        raise ValueError("MQTT-Thema muss konkret und ohne Wildcards sein")
    return topic


def parse_event_categories(value: object) -> frozenset[str]:
    if value is None or value == "":
        return frozenset()
    items: Iterable[object]
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = cast(Iterable[object], value)
    else:
        raise ValueError("Ereigniskategorien müssen eine Liste sein")
    categories = frozenset(str(item).strip() for item in items if str(item).strip())
    if not categories <= EVENT_CATEGORIES:
        raise ValueError("Unbekannte Ereigniskategorie")
    return categories


def parse_long_running_minutes(value: object) -> int:
    """Normalize the stored option exactly like the options-flow integer field.

    Runtime callers deliberately fail closed (disabled) when old or manually
    edited storage contains a value the options flow would reject.
    """
    if isinstance(value, bool):
        raise ValueError("Langläufer-Schwelle muss eine ganze Zahl sein")
    try:
        # Stored HA options are an untyped persistence boundary.  ``int`` is
        # intentionally the same coercion as the options flow; the exception
        # below turns everything else into a stable validation error.
        minutes = int(cast(Any, value))
    except (TypeError, ValueError) as err:
        raise ValueError("Langläufer-Schwelle muss eine ganze Zahl sein") from err
    if not 0 <= minutes <= MAX_LONG_RUNNING_MINUTES:
        raise ValueError("Langläufer-Schwelle liegt außerhalb des erlaubten Bereichs")
    return minutes
