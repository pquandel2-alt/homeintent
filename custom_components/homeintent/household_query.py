"""Read-only household knowledge composed from the current HA snapshot."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Mapping, cast

from .device_result import DeviceControlResult
from .entities import (
    ACTIVATION_TIMESTAMP_DOMAINS,
    EntitySnapshot,
    format_spoken_number,
    is_outdoor_entity,
    normalize_for_compare,
)
from .nlu.domain_operations import DOMAIN_WORDS
from .nlu.german_morphology import dative_location_phrase, sentence_initial
from .nlu.grounded_answer import join_german
from .nlu.registered_operation_compiler import climate_in_named_area
from .nlu.language_frontend import LanguageDocument
from .nlu.normalize import normalize
from .nlu.semantic_utterance import SpeechAct
from .query_target import list_attribute_values, mentioned_entities, resolve_query_targets
from .world_model import WorldModel
from .appliance_lifecycle import match_appliance_lifecycle_query


_WEEKDAYS_DE = (
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"
)
_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)
_WEATHER_DE = {
    "clear-night": "klar", "cloudy": "bewölkt", "exceptional": "außergewöhnlich",
    "fog": "neblig", "hail": "Hagel", "lightning": "Gewitter",
    "lightning-rainy": "Gewitter mit Regen", "partlycloudy": "teilweise bewölkt",
    "pouring": "starker Regen", "rainy": "regnerisch", "snowy": "Schnee",
    "snowy-rainy": "Schneeregen", "sunny": "sonnig", "windy": "windig",
    "windy-variant": "windig und bewölkt",
}

_CAPABILITY_LABELS = {
    "light": "ein- und ausschalten sowie – falls gemeldet – Helligkeit und Farbe einstellen",
    "switch": "ein-, aus- und umschalten",
    "cover": "öffnen, schließen und positionieren",
    "fan": "ein-, ausschalten und Geschwindigkeit oder Preset einstellen",
    "climate": "ein-, ausschalten sowie Temperatur und angebotene Modi einstellen",
    "media_player": "Wiedergabe steuern sowie Lautstärke und angebotene Quellen wählen",
    "vacuum": "starten, pausieren, stoppen und zur Ladestation schicken",
    "scene": "aktivieren",
    "script": "starten",
    "humidifier": "ein-, ausschalten sowie Zielfeuchte und Modus einstellen",
    "water_heater": "Temperatur und angebotenen Betriebsmodus einstellen",
    "number": "einen Wert innerhalb der gemeldeten Grenzen einstellen",
    "input_number": "einen Wert innerhalb der gemeldeten Grenzen einstellen",
    "input_boolean": "ein-, aus- und umschalten",
    "select": "eine der angebotenen Optionen auswählen",
    "valve": "nach Bestätigung öffnen, schließen oder positionieren",
    "lawn_mower": "starten, pausieren und zur Ladestation schicken",
    "camera": "auf einem Medienplayer anzeigen",
    "lock": "verriegeln und nach Bestätigung entriegeln",
    "button": "nach Bestätigung drücken",
}


def _read_only(text: str) -> DeviceControlResult:
    return DeviceControlResult(None, text, is_query=True)


def _format_datetime(value: object, now: datetime) -> str | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None and now.tzinfo is not None:
        value = value.astimezone(now.tzinfo)
    day = "heute" if value.date() == now.date() else (
        "morgen" if value.date() == now.date() + timedelta(days=1)
        else f"am {value.day}. {_MONTHS_DE[value.month - 1]}"
    )
    return f"{day} um {value:%H:%M} Uhr"


def _target_for_capability_query(
    text: str, entities: list[EntitySnapshot]
) -> tuple[EntitySnapshot, ...]:
    return resolve_query_targets(text, entities, frozenset(_CAPABILITY_LABELS))


def _numeric_state(entity: EntitySnapshot) -> float | None:
    try:
        return float(str(entity.state).replace(",", "."))
    except (TypeError, ValueError):
        return None


_COMPLETION_SENSOR_RE = re.compile(
    r"(?:fertigstellungs?zeit|fertig(?:\s*um)?|programmende|programm\s*ende|"
    r"endzeit|completion(?:\s*time)?)"
)
_COMPLETION_QUESTION_RE = re.compile(
    r"\bwann\s+(?:ist|wird|waere|wäre)\s+(?:die|der|das|mein(?:e|er|es)?)?\s*"
    r"(?P<target>.+?)\s+(?:fertig|beendet|durch)\s*[?.!]*$"
)


def _completion_time_query(
    key: str,
    entities: list[EntitySnapshot],
    now: datetime,
    world_model: WorldModel | None,
) -> DeviceControlResult | None:
    """Resolve an appliance completion timestamp without inferring an action."""
    match = _COMPLETION_QUESTION_RE.search(key)
    if match is None:
        return None
    target = match.group("target").strip()
    candidates = [
        entity
        for entity in entities
        if entity.domain == "sensor"
        and entity.device_class == "timestamp"
        and _COMPLETION_SENSOR_RE.search(normalize_for_compare(entity.friendly_name))
    ]

    def target_names(entity: EntitySnapshot) -> tuple[str, ...]:
        names = [entity.friendly_name, *entity.aliases]
        if world_model is not None:
            device = world_model.device_for_entity(entity.entity_id)
            if device is not None:
                names.append(device.name)
        return tuple(normalize_for_compare(name) for name in names)

    matched = [
        entity for entity in candidates
        if any(
            target == name
            or name.startswith(f"{target} ")
            or target.startswith(f"{name} ")
            for name in target_names(entity)
        )
    ]
    if not matched:
        return _read_only(
            "Ich finde keine eindeutig zur genannten Maschine gehörende, "
            "für Assist freigegebene Fertigstellungszeit."
        )
    if len(matched) != 1:
        return _read_only("Welche Maschine meinst du? Ich habe nichts ausgeführt.")
    entity = matched[0]
    if normalize_for_compare(entity.state) in {"unknown", "unavailable", "none", ""}:
        return _read_only(
            f"Die Fertigstellungszeit von {target} ist derzeit nicht verfügbar."
        )
    rendered = _format_datetime(entity.state, now)
    if rendered is None:
        return _read_only(
            f"{entity.friendly_name} liefert derzeit keine gültige Fertigstellungszeit."
        )
    return _read_only(
        f"Laut {entity.friendly_name} ist die Fertigstellung {rendered}."
    )


_ACTIVE_STATES: Mapping[str, frozenset[str]] = {
    "light": frozenset({"on"}), "switch": frozenset({"on"}), "fan": frozenset({"on"}),
    "input_boolean": frozenset({"on"}), "humidifier": frozenset({"on"}),
    "media_player": frozenset({"on", "playing", "paused", "buffering"}),
    "vacuum": frozenset({"cleaning", "returning"}), "lawn_mower": frozenset({"mowing"}),
}
_AREA_ON_RE = re.compile(
    r"^(?:und\s+)?was\s+(?:ist|laeuft|sind)\s+(?:gerade\s+|noch\s+|alles\s+|jetzt\s+)*"
    r"(?:im|in\s+der|in\s+dem|am|auf\s+dem|auf\s+der)\s+(?P<area>.+?)"
    r"(?:\s+(?:gerade|noch|alles|jetzt))*(?:\s+(?:eingeschaltet|an|aktiv|in\s+betrieb|ein))?$"
)


def _area_on_query(key: str, entities: list[EntitySnapshot]) -> DeviceControlResult | None:
    """ "Was ist im Badezimmer eingeschaltet?" - every active device there (F12)."""
    match = _AREA_ON_RE.search(key.strip(" ?.!"))
    if match is None:
        return None
    spoken = match.group("area").strip()
    area_names = {
        normalize_for_compare(name): (entity.area_id, entity.area_name or name)
        for entity in entities if entity.area_id is not None
        for name in (entity.area_name or "", *entity.area_aliases) if name
    }
    area = area_names.get(spoken)
    if area is None:
        return None
    area_id, area_name = area
    active = [
        entity.friendly_name for entity in entities
        if entity.area_id == area_id
        and entity.state in _ACTIVE_STATES.get(entity.domain, frozenset())
    ]
    location = dative_location_phrase(area_name)
    if not active:
        return _read_only(f"{sentence_initial(location)} ist gerade nichts eingeschaltet.")
    verb = "ist" if len(active) == 1 else "sind"
    return _read_only(f"{sentence_initial(location)} {verb} eingeschaltet: {join_german(tuple(active))}.")


def _is_outdoor(entity: EntitySnapshot) -> bool:
    return is_outdoor_entity(entity)


def _outdoor_temperature(entities: list[EntitySnapshot]) -> EntitySnapshot | None:
    sensors = [
        entity for entity in entities
        if entity.domain == "sensor" and entity.device_class == "temperature"
        and _is_outdoor(entity) and _numeric_state(entity) is not None
    ]
    return sensors[0] if len(sensors) == 1 else None


def _house_power(entities: list[EntitySnapshot]) -> EntitySnapshot | None:
    """The whole-house power sensor: named "Haus"/"Gesamt"/"Netz", else unassigned."""
    power = [
        entity for entity in entities
        if entity.domain == "sensor" and entity.device_class == "power"
        and _numeric_state(entity) is not None
    ]
    named = [
        entity for entity in power
        if re.search(r"\b(?:haus|gesamt\w*|netz\w*|hausverbrauch)\b", normalize_for_compare(entity.friendly_name))
    ]
    if len(named) == 1:
        return named[0]
    unassigned = [entity for entity in power if entity.area_id is None]
    return unassigned[0] if len(unassigned) == 1 else None


def _power_text(entity: EntitySnapshot) -> str:
    value = _numeric_state(entity)
    unit = entity.unit or "W"
    spoken_unit = {"W": "Watt", "kW": "Kilowatt"}.get(unit, unit)
    return f"{format_spoken_number(value)} {spoken_unit}"


def match_household_query(
    text: str,
    entities: list[EntitySnapshot],
    now: datetime,
    document: LanguageDocument | None = None,
    world_model: WorldModel | None = None,
) -> DeviceControlResult | None:
    """Answer deterministic household questions without creating a plan."""
    if document is not None and document.utterance.speech_act is not SpeechAct.QUERY:
        return None
    value = document.normalized_text if document is not None else normalize(text)
    key = normalize_for_compare(value)

    if (completion := _completion_time_query(key, entities, now, world_model)) is not None:
        return completion

    if (lifecycle := match_appliance_lifecycle_query(
        value, entities, now, world_model
    )) is not None:
        return _read_only(lifecycle.text)

    if re.search(r"\b(?:wie\s+spaet|wieviel\s+uhr|welche\s+uhrzeit)\b", key):
        return _read_only(f"Es ist {now:%H:%M} Uhr.")
    if re.search(r"\b(?:welches\s+datum|welcher\s+tag|was\s+haben\s+wir\s+heute)\b", key):
        return _read_only(
            f"Heute ist {_WEEKDAYS_DE[now.weekday()]}, der {now.day}. {_MONTHS_DE[now.month - 1]} {now.year}."
        )

    if re.search(r"\bwas\s+kannst\s+du\b", key):
        return _read_only(
            "Ich kann Geräte steuern und abfragen, Automationen und Erinnerungen erstellen, "
            "Kalender, Aufgabenlisten und Timer verwalten sowie Zustände, Verlauf, "
            "Anwesenheit, Wetter und Gerätefähigkeiten erklären."
        )

    if re.search(r"\bwer\s+ist\s+(?:zu\s*hause|daheim|anwesend)\b", key):
        persons = [entity for entity in entities if entity.domain == "person"]
        if not persons:
            # Without exposed persons "nobody is home" would be a guess (F12).
            return _read_only(
                "Mir sind keine Personen freigegeben, deshalb kann ich nicht sagen, "
                "wer zuhause ist. Gib die Personen in Home Assistant für Assist frei."
            )
        people = [entity.friendly_name for entity in persons if entity.state == "home"]
        if not people:
            return _read_only("Laut Home Assistant ist derzeit niemand zuhause.")
        return _read_only("Zuhause: " + ", ".join(people) + ".")

    presence = re.search(r"\bist\s+(.+?)\s+(?:zu\s*hause|daheim|anwesend)\b", key)
    if presence is not None:
        people = mentioned_entities(value, [e for e in entities if e.domain == "person"])
        if len(people) == 1:
            person = people[0]
            return _read_only(
                f"{person.friendly_name} ist laut Home Assistant "
                f"{'zuhause' if person.state == 'home' else 'nicht zuhause'}."
            )
        if len(people) > 1:
            return _read_only("Welche Person meinst du?")

    if (area_on := _area_on_query(key, entities)) is not None:
        return area_on

    if re.search(r"\b(?:co2|kohlendioxid)\b", key) and re.search(r"\b(?:wie\s+(?:hoch|viel)|wert|gehalt|stand)\b", key):
        sensors = [
            entity for entity in entities
            if entity.domain == "sensor" and entity.device_class == "carbon_dioxide"
        ]
        located = [
            entity for entity in sensors
            if any(
                name and re.search(rf"\b{re.escape(normalize_for_compare(name))}\b", key)
                for name in (entity.area_name, *entity.area_aliases)
            )
        ] or (sensors if len(sensors) == 1 else [])
        if len(located) == 1:
            sensor = located[0]
            reading = _numeric_state(sensor)
            if reading is None:
                return _read_only(f"{sensor.friendly_name} meldet gerade keinen gültigen Wert.")
            return _read_only(
                f"{sensor.friendly_name}: {format_spoken_number(reading)} {sensor.unit or 'ppm'}."
            )

    if re.search(
        r"\bwie\s+viel\s+(?:strom|leistung|energie)\s+(?:verbraucht|braucht|zieht)\s+"
        r"(?:das\s+haus|der\s+haushalt|das\s+ganze\s+haus|alles)\b|"
        r"\b(?:aktuelle[rn]?\s+)?(?:haus|gesamt)(?:strom)?verbrauch\b",
        key,
    ):
        house = _house_power(entities)
        if house is not None:
            return _read_only(f"Das Haus verbraucht gerade {_power_text(house)} ({house.friendly_name}).")
        return _read_only("Ich finde keinen eindeutigen Leistungssensor für das ganze Haus.")

    if re.search(r"\b(?:durchschnittliche|mittlere)\s+temperatur\s+(?:im|in\s+dem)\s+(?:ganzen\s+|gesamten\s+)?haus\b", key):
        indoor = [
            value for entity in entities
            if entity.domain == "sensor" and entity.device_class == "temperature"
            and not _is_outdoor(entity) and (value := _numeric_state(entity)) is not None
        ]
        if not indoor:
            return _read_only("Ich finde keine Innentemperatursensoren.")
        mean = round(sum(indoor) / len(indoor), 1)
        return _read_only(
            f"Die durchschnittliche Temperatur im Haus liegt bei {format_spoken_number(mean)} Grad "
            f"(aus {len(indoor)} Sensoren)."
        )

    setpoint = re.search(
        r"\b(?:auf\s+(?:wie\s+viel|welche)\s+(?:grad|temperatur)|welche\s+(?:soll|ziel)temperatur|"
        r"wie\s+hoch\s+ist\s+die\s+(?:soll|ziel)temperatur)\b", key,
    )
    if setpoint is not None:
        climates = [entity for entity in entities if entity.domain == "climate"]
        target = climate_in_named_area(value, climates) or (
            named[0] if len(named := mentioned_entities(value, climates)) == 1 else None
        )
        if target is not None:
            temperature = target.attributes.get("temperature")
            if isinstance(temperature, (int, float)):
                return _read_only(
                    f"{target.friendly_name} ist auf {format_spoken_number(temperature)} Grad eingestellt."
                )
            return _read_only(f"{target.friendly_name} meldet keine Solltemperatur.")

    if re.search(r"^(?:heizt|kuehlt)\b", key):
        climates = [entity for entity in entities if entity.domain == "climate"]
        target = climate_in_named_area(value, climates) or (
            named[0] if len(named := mentioned_entities(value, climates)) == 1 else None
        )
        if target is not None:
            action = target.attributes.get("hvac_action")
            cooling = key.startswith("kuehlt")
            if isinstance(action, str):
                active = action == ("cooling" if cooling else "heating")
                verb = "kühlt" if cooling else "heizt"
                return _read_only(
                    f"Ja, {target.friendly_name} {verb} gerade." if active
                    else f"Nein, {target.friendly_name} {verb} gerade nicht."
                )

    if re.search(r"\b(?:gibt\s+es\s+)?(?:probleme|stoerungen|fehler)\s+(?:im|zu\s+hause|daheim)\b", key):
        unavailable = [
            entity.friendly_name for entity in entities
            if normalize_for_compare(entity.state) == "unavailable"
            or (
                normalize_for_compare(entity.state) == "unknown"
                and entity.domain not in ACTIVATION_TIMESTAMP_DOMAINS
            )
        ]
        weak_batteries = [
            f"{entity.friendly_name} ({value:g} Prozent)"
            for entity in entities
            if entity.domain == "sensor" and entity.device_class == "battery"
            and (value := _numeric_state(entity)) is not None and value < 20
        ]
        if not unavailable and not weak_batteries:
            return _read_only("Ich sehe derzeit keine nicht verfügbaren Geräte oder schwachen Batterien.")
        parts: list[str] = []
        if unavailable:
            parts.append("Nicht verfügbar: " + ", ".join(unavailable[:8]))
        if weak_batteries:
            parts.append("Schwache Batterien: " + ", ".join(weak_batteries[:8]))
        return _read_only(". ".join(parts) + ".")

    battery_limit = re.search(
        r"\b(?:welche|zeige|gibt\s+es).*\bbatter(?:ie|ien)\b.*?"
        r"(?:unter|kleiner\s+als|weniger\s+als)\s*(\d{1,3})\s*(?:prozent|%)",
        key,
    )
    if battery_limit is not None:
        limit = min(100, int(battery_limit.group(1)))
        matches = [
            (entity, value) for entity in entities
            if entity.domain == "sensor" and entity.device_class == "battery"
            and (value := _numeric_state(entity)) is not None and value < limit
        ]
        if not matches:
            return _read_only(f"Keine bekannte Batterie liegt unter {limit} Prozent.")
        return _read_only(
            f"Unter {limit} Prozent: " + ", ".join(
                f"{entity.friendly_name} mit {value:g} Prozent"
                for entity, value in sorted(matches, key=lambda item: item[1])
            ) + "."
        )

    if re.search(r"\bwann\s+geht\s+die\s+sonne\s+(?:auf|unter)\b", key):
        sun = next((entity for entity in entities if entity.entity_id == "sun.sun"), None)
        if sun is None:
            return _read_only("Ich finde in Home Assistant keine Sonneninformationen.")
        rising = re.search(r"\bauf\b", key) is not None
        rendered = _format_datetime(
            sun.attributes.get("next_rising" if rising else "next_setting"), now
        )
        if rendered is None:
            return _read_only("Die nächste Sonnenzeit ist derzeit nicht verfügbar.")
        return _read_only(f"Die Sonne geht {rendered} {'auf' if rising else 'unter'}.")

    # A specifically named outdoor measurement belongs to HassGetState,
    # not to the broader weather summary below. Prefer the live temperature
    # sensor when one is available; the engine then uses the stated property
    # to distinguish it from battery/humidity/link-quality siblings.
    if "aussentemperatur" in key and any(
        entity.domain == "sensor" and entity.device_class == "temperature"
        for entity in entities
    ):
        return None

    if re.search(
        r"\b(?:wie\s+ist\s+das\s+wetter|welches\s+wetter|aussen(?:temperatur)?|"
        r"wie\s+(?:warm|kalt)\s+ist\s+es\s+(?:draussen|aussen))\b",
        key,
    ):
        weather_entities = [e for e in entities if e.domain == "weather"]
        mentioned = mentioned_entities(value, weather_entities)
        choices = list(mentioned) or weather_entities
        if len(choices) > 1:
            return _read_only("Welche Wetter-Entität meinst du?")
        if not choices:
            # No weather entity: an outdoor temperature sensor answers "Wie
            # warm ist es draußen?" just as well (F12).
            outdoor = _outdoor_temperature(entities)
            if outdoor is not None and re.search(r"\b(?:warm|kalt|temperatur|grad)\b", key):
                return _read_only(
                    f"Draußen sind es {format_spoken_number(round(_numeric_state(outdoor) or 0.0, 1))} Grad "
                    f"({outdoor.friendly_name})."
                )
            return _read_only("Ich finde in Home Assistant keine Wetter-Entität.")
        weather = choices[0]
        condition = _WEATHER_DE.get(weather.state, weather.state)
        temperature = weather.attributes.get("temperature")
        humidity = weather.attributes.get("humidity")
        details = [condition]
        if temperature is not None:
            details.append(f"{format_spoken_number(temperature)} Grad")
        if humidity is not None:
            details.append(f"{format_spoken_number(humidity)} Prozent Luftfeuchtigkeit")
        return _read_only(f"{weather.friendly_name}: " + ", ".join(details) + ".")

    if re.search(r"\b(?:wie\s+wird|wetter|vorhersage)\b.*\b(?:morgen|uebermorgen)\b", key):
        weather_entities = [e for e in entities if e.domain == "weather"]
        if len(weather_entities) != 1:
            return _read_only("Welche Wetter-Entität meinst du?") if weather_entities else None
        forecast = weather_entities[0].attributes.get("forecast")
        if not isinstance(forecast, (list, tuple)) or not forecast:
            return _read_only("Die Wetter-Entität stellt aktuell keine Vorhersage bereit.")
        day_offset = 2 if "uebermorgen" in key else 1
        target_date = now.date() + timedelta(days=day_offset)
        forecast_values = cast(list[object] | tuple[object, ...], forecast)
        rows = [
            cast(Mapping[str, object], item)
            for item in forecast_values if isinstance(item, dict)
        ]
        row = next((
            item for item in rows
            if str(item.get("datetime", "")).startswith(target_date.isoformat())
        ), None)
        if row is None:
            return _read_only("Für diesen Tag ist keine Wettervorhersage verfügbar.")
        condition = _WEATHER_DE.get(str(row.get("condition", "")), str(row.get("condition", "")))
        temperatures: list[str] = []
        if row.get("temperature") is not None:
            temperatures.append(f"bis {format_spoken_number(row['temperature'])} Grad")
        if row.get("templow") is not None:
            temperatures.append(f"mindestens {format_spoken_number(row['templow'])} Grad")
        label = "übermorgen" if day_offset == 2 else "morgen"
        return _read_only(
            f"{label.capitalize()} wird es {condition}"
            + ((", " + ", ".join(temperatures)) if temperatures else "") + "."
        )

    catalogue = re.search(r"\bwelche\s+(szenen|skripte)\s+(?:gibt\s+es|sind\s+verfuegbar)\b", key)
    if catalogue is not None:
        domain = "scene" if catalogue.group(1) == "szenen" else "script"
        names = sorted(e.friendly_name for e in entities if e.domain == domain)
        if not names:
            return _read_only(f"Es sind keine {'Szenen' if domain == 'scene' else 'Skripte'} verfügbar.")
        return _read_only(
            f"Verfügbare {'Szenen' if domain == 'scene' else 'Skripte'}: "
            + ", ".join(names[:12])
            + (f" und {len(names) - 12} weitere" if len(names) > 12 else "")
            + "."
        )

    option_query = re.search(
        r"\bwelche\s+(quellen|optionen|modi|betriebsarten|presets|stufen)\b", key
    )
    if option_query is not None:
        # Query nouns such as "Modi" are also legitimate entity nouns for
        # helpers. Remove the requested property before resolving the target
        # so "Modi der Heizung" cannot create a fake input_boolean domain.
        target_text = re.sub(
            r"\bwelche\s+(?:quellen|optionen|modi|betriebsarten|presets|stufen)\b",
            "",
            value,
            flags=re.I,
        )
        targets = _target_for_capability_query(target_text, entities)
        if len(targets) != 1:
            return _read_only("Welches Gerät meinst du?") if targets else None
        entity = targets[0]
        attribute_keys = {
            "quellen": ("source_list",),
            "optionen": ("options",),
            "modi": ("hvac_modes", "available_modes", "operation_list"),
            "betriebsarten": ("hvac_modes", "operation_list"),
            "presets": ("preset_modes",),
            "stufen": ("fan_speed_list", "preset_modes"),
        }[option_query.group(1)]
        options = list_attribute_values(entity, attribute_keys)
        if not options:
            return _read_only(f"{entity.friendly_name} meldet dafür keine Auswahlmöglichkeiten.")
        return _read_only(f"{entity.friendly_name} bietet: " + ", ".join(options) + ".")

    if re.search(r"\bwas\s+kann\b", key):
        targets = _target_for_capability_query(value, entities)
        if len(targets) > 1:
            return _read_only("Welches Gerät meinst du?")
        if len(targets) == 1:
            entity = targets[0]
            label = _CAPABILITY_LABELS.get(entity.domain)
            if label is not None:
                return _read_only(f"{entity.friendly_name} kann ich {label}.")

    return None


QUERYABLE_HOUSEHOLD_DOMAINS = frozenset(DOMAIN_WORDS) | frozenset({
    "person", "weather", "sun", "scene", "script",
})
