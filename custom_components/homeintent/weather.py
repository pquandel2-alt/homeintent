"""Weather (7.9.3 B1) and rain conditions (B4).

Read-only questions from ``weather.*`` and its forecast (Home Assistant's
``weather.get_forecasts``, daily or hourly depending on the question):
"Wie wird das Wetter morgen?", "Wie warm wird es heute?", "Regnet es heute
noch?", "Brauche ich einen Schirm?", "Wird es am Wochenende sonnig?", "Wie
viel Wind ist morgen?".  Probabilities and amounts are said only when the
forecast delivers them; nothing is guessed.

Rain in automations, as closed constructions over entity ids:

* trigger "Wenn Regen angesagt ist, …" – a ``time_pattern`` trigger every
  30 minutes, the forecast is fetched *in the automation*
  (``weather.get_forecasts`` with ``response_variable``) and a template
  condition decides from the next hours; a device action only runs when
  the device is not already in its end state;
* trigger "Wenn es regnet, …" – the rain sensor (``binary_sensor``
  ``moisture`` named Regen…), else the weather state (rainy, pouring …);
* guard "…, aber nur wenn es heute nicht regnet / nicht regnen soll /
  nicht geregnet hat" – the forecast for today, and/or the rain sensor or
  rain amount of the last 24 hours.

Every template is HomeIntent's own, built from validated entity ids and
fixed numbers only, never from the user's words.  Without a weather entity
(or rain sensor) the answer is honest.

Home-Assistant-free.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from .entities import EntitySnapshot, format_spoken_number, normalize_for_compare

__all__ = (
    "FORECAST_VARIABLE",
    "RAIN_CONDITIONS",
    "WeatherClause",
    "WeatherGuard",
    "WeatherQuery",
    "answer_weather",
    "describe_guard",
    "guard_steps",
    "parse_weather_clause",
    "parse_weather_query",
    "rain_amount_sensors",
    "rain_sensors",
    "weather_entities",
)

FORECAST_VARIABLE = "homeintent_vorhersage"
DAILY_VARIABLE = "homeintent_tagesvorhersage"
RAIN_CONDITIONS = ("rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail")
RAIN_PROBABILITY = 50  # % from which an hour/day counts as "Regen angesagt"
RAIN_AMOUNT = 0.5  # mm from which an hour/day counts as rain (when no probability)
SOON_HOURS = 6  # "angesagt" for a trigger: the next hours
CHECK_MINUTES = 30
RECENT_HOURS = 24
_CONDITIONS_DE = {
    "sunny": "sonnig", "clear-night": "klar", "partlycloudy": "teils bewölkt", "cloudy": "bewölkt",
    "rainy": "Regen", "pouring": "starker Regen", "lightning": "Gewitter", "lightning-rainy": "Gewitter mit Regen",
    "snowy": "Schnee", "snowy-rainy": "Schneeregen", "hail": "Hagel", "fog": "Nebel", "windy": "windig",
    "windy-variant": "windig und bewölkt", "exceptional": "außergewöhnliches Wetter",
}
_SUNNY = frozenset({"sunny", "clear-night", "partlycloudy"})
_SNOWY = frozenset({"snowy", "snowy-rainy"})
_WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
_WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

# --- word classes ----------------------------------------------------------------
_RAIN_WORDS = frozenset({
    "regen", "regnen", "regnet", "regnets", "geregnet", "schirm", "regenschirm", "regenjacke", "niederschlag",
    "schauer", "regenschauer", "gewitter", "nass", "regenwahrscheinlichkeit",
})
_SUN_WORDS = frozenset({"sonnig", "sonne", "sonnenschein", "heiter", "schoen", "schoenes"})
_WIND_WORDS = frozenset({"wind", "windig", "sturm", "stuermisch", "boeen", "windgeschwindigkeit"})
_SNOW_WORDS = frozenset({"schnee", "schneien", "schneit", "schneits"})
_TEMP_WORDS = frozenset({"warm", "kalt", "temperatur", "temperaturen", "grad", "heiss", "kuehl", "frisch", "waermer",
                         "kaelter"})
_OVERVIEW_WORDS = frozenset({"wetter", "wettervorhersage", "vorhersage", "wetterbericht"})
_FUTURE = frozenset({"wird", "werden", "wirds", "soll", "sollte", "kommt", "gibt", "gibts", "bleibt", "angesagt",
                     "vorhergesagt", "erwartet", "noch", "brauche", "brauchen", "braucht"})
_QUESTION_START = frozenset({
    "wie", "was", "wird", "werden", "regnet", "regnets", "brauche", "brauchen", "braucht", "gibt", "gibts", "ist",
    "soll", "kommt", "bleibt", "schneit", "wann", "muss", "sollte", "sag", "wieviel", "scheint", "welche",
    "welcher", "welches",
})
_CONDITIONAL = frozenset({"wenn", "sobald", "falls", "sofern", "nur"})
_IMPERATIVE = frozenset({
    "schalte", "schalt", "mach", "mache", "fahr", "fahre", "oeffne", "schliesse", "schliess", "stell", "stelle",
    "bewaessere", "starte", "schick", "schicke", "sende", "erinnere", "melde", "benachrichtige",
})


def _words(text: str) -> list[str]:
    folded = normalize_for_compare(text)
    return "".join(char if char.isalnum() else " " for char in folded).split()


# --- entities ----------------------------------------------------------------------

def weather_entities(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    return sorted((entity for entity in entities if entity.domain == "weather"), key=lambda item: item.entity_id)


def rain_sensors(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    """Binary rain sensors: class ``moisture`` and a rain name ("Regensensor")."""
    return sorted(
        (entity for entity in entities if entity.domain == "binary_sensor" and entity.device_class == "moisture"
         and "regen" in normalize_for_compare(f"{entity.friendly_name} {entity.entity_id}")),
        key=lambda item: item.entity_id,
    )


def rain_amount_sensors(entities: Sequence[EntitySnapshot]) -> list[EntitySnapshot]:
    """Rain amount sensors: ``precipitation`` (mm) or a rain name with mm."""
    return sorted(
        (entity for entity in entities if entity.domain == "sensor" and (
            entity.device_class == "precipitation"
            or (entity.unit in {"mm", "in"} and "regen" in normalize_for_compare(entity.friendly_name))
        )),
        key=lambda item: item.entity_id,
    )


# --- read-only questions --------------------------------------------------------------

@dataclass(frozen=True)
class WeatherQuery:
    topic: str  # "overview" | "rain" | "sun" | "wind" | "snow" | "temperature"
    days: tuple[int, ...]  # day offsets (0 today, 1 tomorrow); () = now
    rest_of_today: bool = False  # "heute noch", "heute Abend", "gleich": hourly
    label: str = ""


def _period(words: list[str], now: datetime) -> tuple[tuple[int, ...], bool, str] | None:
    present = set(words)
    pairs = list(zip(words, words[1:]))
    if "wochenende" in present:
        saturday = (5 - now.weekday()) % 7
        days = (saturday, saturday + 1) if now.weekday() != 6 else (0,)
        return days, False, "am Wochenende"
    if "uebermorgen" in present:
        return (2,), False, "übermorgen"
    if "morgen" in present and not ("heute" in present and ("heute", "morgen") in pairs):
        if not any(a in {"am", "heute"} and b == "morgen" for a, b in pairs) or ("am", "morgen") not in pairs:
            return (1,), False, "morgen"
    for index, word in enumerate(words):
        if word in _WEEKDAYS:
            offset = (_WEEKDAYS.index(word) - now.weekday()) % 7
            return (offset,), False, ("heute" if offset == 0 else f"am {_WEEKDAYS_DE[_WEEKDAYS.index(word)]}")
    if present & {"gleich", "nachher", "spaeter", "abend", "nachmittag", "heute"} and (
        present & {"noch", "gleich", "nachher", "spaeter", "abend", "nachmittag", "naechsten", "stunden"}
    ):
        return (0,), True, "heute noch"
    if "heute" in present or "tag" in present or "tagsueber" in present:
        return (0,), False, "heute"
    if present & {"jetzt", "gerade", "aktuell", "momentan", "draussen"}:
        return (), False, "gerade"
    return None


def parse_weather_query(text: str, now: datetime) -> WeatherQuery | None:
    """A read-only weather question, else ``None``."""
    words = _words(text)
    present = set(words)
    if not words or present & _CONDITIONAL or words[0] in _IMPERATIVE:
        return None
    question = text.rstrip().endswith("?") or words[0] in _QUESTION_START
    if not question:
        return None
    if present & _RAIN_WORDS:
        topic = "rain"
    elif present & _SNOW_WORDS:
        topic = "snow"
    elif present & _WIND_WORDS:
        topic = "wind"
    elif present & _SUN_WORDS and (
        present & _FUTURE or present & _OVERVIEW_WORDS or present & {"heute", "scheint", "morgen", "wochenende"}
    ):
        topic = "sun"
    elif present & _OVERVIEW_WORDS:
        topic = "overview"
    elif present & _TEMP_WORDS and present & (_FUTURE | {"morgen", "uebermorgen", "wochenende", *_WEEKDAYS}):
        topic = "temperature"  # "Wie warm wird es heute?" - never "Wie warm ist es im Bad?"
    else:
        return None
    period = _period(words, now)
    if period is None:
        if topic == "rain" and present & {"schirm", "regenschirm", "regenjacke"}:
            period = ((0,), True, "heute noch")
        elif topic in {"rain", "snow"} and present & {"noch"}:
            period = ((0,), True, "heute noch")
        elif topic in {"overview", "rain", "sun", "wind", "snow"}:
            period = ((0,), False, "heute") if present & _FUTURE else ((), False, "gerade")
        else:
            period = ((0,), False, "heute")
    days, rest, label = period
    if topic == "rain" and days == (0,) and present & {"schirm", "regenschirm", "regenjacke"}:
        rest, label = True, "heute noch"  # an umbrella is about the rest of the day
    if topic == "rain" and days == (0,) and present & {"regnet", "regnets"} and not present & {"noch", "heute"}:
        days, rest, label = (), False, "gerade"  # "Regnet es?" - now
    return WeatherQuery(topic, days, rest, label)


def _local(value: Any, tz: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz) if tz is not None else moment


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rainy(item: Mapping[str, Any]) -> bool:
    if item.get("condition") in RAIN_CONDITIONS:
        return True
    probability = _number(item.get("precipitation_probability"))
    if probability is not None:
        return probability >= RAIN_PROBABILITY
    amount = _number(item.get("precipitation"))
    return amount is not None and amount >= RAIN_AMOUNT


def _rain_detail(item: Mapping[str, Any], units: Mapping[str, str]) -> str:
    parts = []
    probability = _number(item.get("precipitation_probability"))
    if probability is not None:
        parts.append(f"Wahrscheinlichkeit {round(probability)} %")
    amount = _number(item.get("precipitation"))
    if amount is not None and amount > 0:
        parts.append(f"{format_spoken_number(round(amount, 1))} {units.get('precipitation', 'mm')}")
    return f" ({', '.join(parts)})" if parts else ""


def _temp(value: Any, units: Mapping[str, str]) -> str | None:
    number = _number(value)
    if number is None:
        return None
    unit = units.get("temperature", "°C")
    return f"{format_spoken_number(round(number))} {'Grad' if unit in {'°C', '°F'} else unit}"


def _day_items(forecast: Sequence[Mapping[str, Any]], day: date, tz: Any) -> list[Mapping[str, Any]]:
    found = []
    for item in forecast:
        moment = _local(item.get("datetime"), tz)
        if moment is not None and moment.date() == day:
            found.append(item)
    return found


def _day_name(offset: int, today: date) -> str:
    if offset == 0:
        return "heute"
    if offset == 1:
        return "morgen"
    if offset == 2:
        return "übermorgen"
    return f"am {_WEEKDAYS_DE[(today + timedelta(days=offset)).weekday()]}"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def needs_hourly(query: WeatherQuery) -> bool:
    return query.rest_of_today


def answer_weather(
    query: WeatherQuery,
    entity: EntitySnapshot,
    forecast: Sequence[Mapping[str, Any]] | None,
    now: datetime,
) -> str:
    """The spoken answer from the current state and the forecast (``None``:
    the forecast could not be read)."""
    attributes = entity.attributes
    units = {
        "temperature": str(attributes.get("temperature_unit") or "°C"),
        "precipitation": str(attributes.get("precipitation_unit") or "mm"),
        "wind": str(attributes.get("wind_speed_unit") or "km/h"),
    }
    source = f" (laut „{entity.friendly_name}“)"
    tz = now.tzinfo
    if not query.days:
        condition = _CONDITIONS_DE.get(entity.state)
        if condition is None:
            return f"„{entity.friendly_name}“ meldet gerade kein Wetter ({entity.state})."
        temperature = _temp(attributes.get("temperature"), units)
        if query.topic == "rain":
            raining = entity.state in RAIN_CONDITIONS
            return ("Ja, gerade regnet es" if raining else f"Nein, gerade regnet es nicht, es ist {condition}") + (
                f", {temperature}" if temperature and not raining else "") + f"{source}."
        if query.topic == "wind":
            speed = _number(attributes.get("wind_speed"))
            if speed is None:
                return f"„{entity.friendly_name}“ meldet keine Windgeschwindigkeit."
            return f"Gerade weht der Wind mit {format_spoken_number(round(speed))} {units['wind']}{source}."
        text = f"Gerade ist es {condition}" + (f" bei {temperature}" if temperature else "")
        return text + f"{source}."
    if forecast is None:
        return (
            f"Die Vorhersage von „{entity.friendly_name}“ kann ich gerade nicht lesen. Einen Wert rate ich nicht."
        )
    today = now.date()
    if query.rest_of_today:
        hours = [
            item for item in forecast
            if (moment := _local(item.get("datetime"), tz)) is not None and moment.date() == today and moment >= now
            - timedelta(minutes=59)
        ]
        if not hours:
            return f"Für den Rest von heute liefert „{entity.friendly_name}“ keine stündliche Vorhersage."
        if query.topic in {"rain", "snow"}:
            wet = [item for item in hours if (_rainy(item) if query.topic == "rain" else item.get("condition") in _SNOWY)]
            if not wet:
                word = "Regen" if query.topic == "rain" else "Schnee"
                schirm = " Einen Schirm brauchst du nicht." if query.topic == "rain" else ""
                return f"Nein, heute ist laut Vorhersage kein {word} mehr angesagt{source}.{schirm}"
            first = wet[0]
            moment = _local(first.get("datetime"), tz)
            assert moment is not None
            word = "Regen" if query.topic == "rain" else "Schnee"
            schirm = " Nimm besser einen Schirm mit." if query.topic == "rain" else ""
            return (
                f"Ja, ab etwa {moment:%H} Uhr ist {word} angesagt{_rain_detail(first, units)}{source}.{schirm}"
            )
        conditions = [_CONDITIONS_DE.get(str(item.get("condition")), "") for item in hours]
        temps = [value for item in hours if (value := _number(item.get("temperature"))) is not None]
        text = "Für den Rest von heute: " + ", ".join(dict.fromkeys(item for item in conditions if item))
        if temps:
            text += f", {_temp(min(temps), units)} bis {_temp(max(temps), units)}"
        return text + f"{source}."
    parts: list[str] = []
    for offset in query.days:
        day = today + timedelta(days=offset)
        items = _day_items(forecast, day, tz)
        name = _day_name(offset, today)
        if not items:
            parts.append(f"für {name} liefert „{entity.friendly_name}“ keine Vorhersage")
            continue
        item = items[0]
        condition = _CONDITIONS_DE.get(str(item.get("condition")), "")
        high = _temp(item.get("temperature"), units)
        low = _temp(item.get("templow"), units)
        if query.topic == "rain":
            if _rainy(item):
                parts.append(f"{name} ist Regen angesagt{_rain_detail(item, units)}")
            else:
                parts.append(f"{name} ist kein Regen angesagt" + (f" ({condition})" if condition else ""))
        elif query.topic == "snow":
            parts.append(f"{name} {'ist Schnee angesagt' if item.get('condition') in _SNOWY else 'ist kein Schnee angesagt'}")
        elif query.topic == "sun":
            sunny = item.get("condition") in _SUNNY
            parts.append(f"{name} wird es {'sonnig' if sunny else 'nicht sonnig'}" + (
                f" ({condition})" if condition and condition != "sonnig" else ""))
        elif query.topic == "wind":
            speed = _number(item.get("wind_speed"))
            parts.append(
                f"{name} weht der Wind mit bis zu {format_spoken_number(round(speed))} {units['wind']}"
                if speed is not None else f"für {name} liefert die Vorhersage keinen Wind"
            )
        elif query.topic == "temperature":
            if high is None:
                parts.append(f"für {name} liefert die Vorhersage keine Temperatur")
            else:
                parts.append(f"{name} wird es bis zu {high} warm" + (f", nachts {low}" if low else ""))
        else:
            detail = condition or "unbekannt"
            text = f"{name} {detail}"
            if high is not None:
                text += f", bis {high}" + (f", tiefstens {low}" if low else "")
            if _rainy(item) and item.get("condition") not in RAIN_CONDITIONS:
                text += f", Regen möglich{_rain_detail(item, units)}"
            elif item.get("condition") in RAIN_CONDITIONS:
                text += _rain_detail(item, units)
            parts.append(text)
    return _cap("; ".join(parts)) + f"{source}."


# --- rain in automations -----------------------------------------------------------------

@dataclass(frozen=True)
class WeatherClause:
    """A rain clause found in a request: its words are removed from the text."""

    kinds: tuple[str, ...]  # "rain_soon" | "raining" | "no_rain_today" | "rain_today" | "no_rain_recent"
    role: str  # "trigger" ("Wenn Regen angesagt ist, …") | "guard" ("…, aber nur wenn …")
    rest: str  # the request without the clause
    spoken: str  # the clause as said


@dataclass(frozen=True)
class WeatherGuard:
    """What an automation checks about rain, from which source."""

    kinds: tuple[str, ...]
    weather_entity_id: str | None = None
    rain_sensor_id: str | None = None
    rain_amount_id: str | None = None
    names: Mapping[str, str] = field(default_factory=dict)


_FORECAST_WORDS = frozenset({"angesagt", "vorhergesagt", "gemeldet", "erwartet", "vorhersage", "soll", "wird",
                             "kommt", "gibt"})
_CLAUSE_RE = re.compile(
    r"(?P<lead>(?:,\s*)?(?:\baber\s+|\bund\s+)?(?:\bnur\s+)?)(?P<conj>\bwenn|\bsofern|\bfalls|\bsobald|\bbei)\b"
    r"(?P<body>[^,.;!?]*)",
    re.IGNORECASE,
)


def _clause_kinds(body: list[str]) -> tuple[str, ...] | None:
    present = set(body)
    rain = bool(present & {"regen", "regnen", "regnet", "geregnet", "regnets", "niederschlag", "regenschauer"})
    if not rain:
        return None
    negated = bool(present & {"nicht", "kein", "keinen", "keine", "trocken"})
    past = bool(present & {"geregnet", "hat", "letzten", "gestern"}) and "geregnet" in present
    forecast = bool(present & _FORECAST_WORDS)  # "angesagt", "soll", "wird" … ("anfängt zu regnen" is now)
    kinds: list[str] = []
    if negated:
        if past:
            kinds.append("no_rain_recent")
        # "nicht geregnet hat bzw. nicht regnen soll": both parts.
        if forecast or not past or present & {"heute"} and not past:
            if "regnen" in present or present & _FORECAST_WORDS or "regnet" in present:
                kinds.append("no_rain_today")
        if not kinds:
            kinds.append("no_rain_today")
        return tuple(dict.fromkeys(kinds))
    if past:
        return None  # "wenn es geregnet hat" - not supported as a trigger
    return ("rain_soon",) if forecast else ("raining",)


def parse_weather_clause(text: str) -> WeatherClause | None:
    """The one rain clause of a request: "Wenn Regen angesagt ist, …" (a
    trigger at the start) or "…, aber nur wenn es nicht regnet" (a guard)."""
    text = re.sub(r"\bbzw\.", "bzw", text)
    for match in _CLAUSE_RE.finditer(text):
        body = match.group("body")
        body_words = _words(body)
        end = match.end()
        if match.group("conj").casefold() == "bei":
            # "bei Regen" is the clause itself, the command follows.
            first = re.match(r"\s*(regen|regenwetter|niederschlag)\b", body, re.IGNORECASE)
            if first is None:
                continue
            body_words = ["regnet"]
            end = match.start("body") + first.end()
        # A second clause joined by "bzw./oder" belongs to the same condition.
        tail = text[end:]
        extra = re.match(r"\s*(?:bzw|beziehungsweise|oder|und)\s+(?P<more>[^,.;!?]*)", tail, re.IGNORECASE)
        if extra is not None and set(_words(extra.group("more"))) & {"regnen", "regnet", "geregnet", "regen"}:
            body_words += _words(extra.group("more"))
            end += extra.end()
        kinds = _clause_kinds(body_words)
        if kinds is None:
            continue
        start = match.start()
        before = text[:start].strip()
        role = "trigger" if not before or before.endswith((".", "!", "?")) else "guard"
        if role == "trigger" and text[end:].strip(" .!?") == "":
            role = "guard"
        # "Bewässere nur, wenn …": the restricting particle belongs to the
        # condition, not to the action that is read again.
        head = re.sub(r"(?:\s*,)?\s*\b(?:aber\s+)?(?:nur|bloß|lediglich)(?:\s+dann)?\s*,?\s*$", "", text[:start],
                      flags=re.IGNORECASE)
        tail = text[end:].lstrip()
        joiner = " " if head and tail and not head.endswith(" ") and tail[0] not in ".,;!?" else ""
        rest = (head + joiner + tail).strip()
        rest = re.sub(r"^\s*,\s*", "", rest)
        rest = re.sub(r"\s+,", ",", rest).strip(" ,")
        if role == "trigger" and kinds[0].startswith("no_"):
            role = "guard"
        spoken = text[start:end].strip(" ,")
        return WeatherClause(kinds, role, rest[:1].upper() + rest[1:] if rest else rest, spoken)
    return None


def build_guard(
    kinds: Sequence[str], entities: Sequence[EntitySnapshot]
) -> tuple[WeatherGuard | None, str | None]:
    """The guard from the house's sources, or the honest reason why not.

    * ``rain_soon``/``no_rain_today`` need a weather entity (forecast);
    * ``raining`` uses the rain sensor, else the weather state;
    * ``no_rain_recent`` uses the rain sensor or rain amount; without them
      the forecast for today stands in only when the user named both.
    """
    weathers = weather_entities(entities)
    sensors = rain_sensors(entities)
    amounts = rain_amount_sensors(entities)
    names = {entity.entity_id: entity.friendly_name for entity in [*weathers, *sensors, *amounts]}
    if len(weathers) > 1 and any(kind in {"rain_soon", "no_rain_today", "rain_today"} for kind in kinds):
        listed = " oder ".join(f"„{entity.friendly_name}“" for entity in weathers)
        return None, f"Welche Wettervorhersage soll ich nehmen: {listed}?"
    weather = weathers[0].entity_id if weathers else None
    sensor = sensors[0].entity_id if len(sensors) == 1 else None
    amount = amounts[0].entity_id if len(amounts) == 1 and sensor is None else None
    wanted: list[str] = []
    for kind in kinds:
        if kind in {"rain_soon", "no_rain_today", "rain_today"}:
            if weather is None:
                continue
            wanted.append(kind)
        elif kind == "raining":
            if sensor is None and weather is None:
                continue
            wanted.append(kind)
        elif kind == "no_rain_recent":
            if sensor is None and amount is None:
                continue
            wanted.append(kind)
    if not wanted:
        missing = {
            "rain_soon": "keine Wettervorhersage (weather-Entität)",
            "no_rain_today": "keine Wettervorhersage (weather-Entität)",
            "rain_today": "keine Wettervorhersage (weather-Entität)",
            "raining": "weder einen Regensensor noch eine Wettervorhersage",
            "no_rain_recent": "weder einen Regensensor noch eine Regenmenge",
        }
        reason = " und ".join(dict.fromkeys(missing[kind] for kind in kinds))
        return None, f"Dafür habe ich {reason}."
    return WeatherGuard(
        tuple(wanted), weather if any(k in {"rain_soon", "no_rain_today", "rain_today"} or (k == "raining" and sensor is None)
                                      for k in wanted) else None,
        sensor if any(k in {"raining", "no_rain_recent"} for k in wanted) else None,
        amount if "no_rain_recent" in wanted else None,
        names,
    ), None


def _hourly_rain_template(entity_id: str) -> str:
    rainy = "[" + ", ".join(f"'{item}'" for item in RAIN_CONDITIONS) + "]"
    return (
        "{% set ns = namespace(rain=false) %}"
        f"{{% for item in ({FORECAST_VARIABLE}['{entity_id}'].forecast if '{entity_id}' in {FORECAST_VARIABLE} "
        f"else [])[:{SOON_HOURS}] %}}"
        f"{{% if item.condition in {rainy} or (item.precipitation_probability | default(0) | float(0)) >= "
        f"{RAIN_PROBABILITY} or (item.precipitation_probability is not defined and "
        f"(item.precipitation | default(0) | float(0)) >= {RAIN_AMOUNT}) %}}"
        "{% set ns.rain = true %}{% endif %}{% endfor %}"
    )


def _daily_rain_template(entity_id: str) -> str:
    rainy = "[" + ", ".join(f"'{item}'" for item in RAIN_CONDITIONS) + "]"
    return (
        "{% set ns = namespace(rain=false) %}"
        f"{{% for item in ({DAILY_VARIABLE}['{entity_id}'].forecast if '{entity_id}' in {DAILY_VARIABLE} "
        "else [])[:1] %}"
        f"{{% if item.condition in {rainy} or (item.precipitation_probability | default(0) | float(0)) >= "
        f"{RAIN_PROBABILITY} or (item.precipitation_probability is not defined and "
        f"(item.precipitation | default(0) | float(0)) >= {RAIN_AMOUNT}) %}}"
        "{% set ns.rain = true %}{% endif %}{% endfor %}"
    )


def _recent_rain_expression(guard: WeatherGuard) -> str:
    seconds = RECENT_HOURS * 3600
    if guard.rain_sensor_id is not None:
        entity = guard.rain_sensor_id
        return (
            f"(is_state('{entity}', 'off') and states.{entity} is not none and "
            f"(now() - states.{entity}.last_changed).total_seconds() > {seconds})"
        )
    assert guard.rain_amount_id is not None
    entity = guard.rain_amount_id
    return (
        f"(states.{entity} is not none and ((now() - states.{entity}.last_changed).total_seconds() > {seconds} "
        f"or (states('{entity}') | float(0)) == 0))"
    )


def guard_steps(guard: WeatherGuard) -> list[dict[str, Any]]:
    """The action steps that check the guard: fetch the forecast in the
    automation (``response_variable``), then one template condition."""
    steps: list[dict[str, Any]] = []
    prefix = ""
    parts: list[str] = []
    if "rain_soon" in guard.kinds:
        assert guard.weather_entity_id is not None
        steps.append({
            "action": "weather.get_forecasts", "target": {"entity_id": guard.weather_entity_id},
            "data": {"type": "hourly"}, "response_variable": FORECAST_VARIABLE,
        })
        prefix += _hourly_rain_template(guard.weather_entity_id)
        parts.append("ns.rain")
    daily = [kind for kind in guard.kinds if kind in {"no_rain_today", "rain_today"}]
    if daily:
        assert guard.weather_entity_id is not None
        steps.append({
            "action": "weather.get_forecasts", "target": {"entity_id": guard.weather_entity_id},
            "data": {"type": "daily"}, "response_variable": DAILY_VARIABLE,
        })
        prefix += _daily_rain_template(guard.weather_entity_id).replace("ns = namespace(rain=false)",
                                                                        "day = namespace(rain=false)").replace(
            "ns.rain = true", "day.rain = true")
        parts.append("not day.rain" if daily[0] == "no_rain_today" else "day.rain")
    if "raining" in guard.kinds and guard.rain_sensor_id is None and guard.weather_entity_id is not None:
        rainy = "[" + ", ".join(f"'{item}'" for item in RAIN_CONDITIONS) + "]"
        parts.append(f"states('{guard.weather_entity_id}') in {rainy}")
    if "no_rain_recent" in guard.kinds:
        parts.append(_recent_rain_expression(guard))
    if parts:
        steps.append({"condition": "template", "value_template": prefix + "{{ " + " and ".join(parts) + " }}"})
    return steps


# A device action under "Regen angesagt" runs only while the device is not
# already in its end state (the check repeats every 30 minutes).
_END_STATES = {
    ("cover", "close_cover"): "closed", ("cover", "open_cover"): "open", ("valve", "close_valve"): "closed",
    ("valve", "open_valve"): "open", ("light", "turn_off"): "off", ("light", "turn_on"): "on",
    ("switch", "turn_off"): "off", ("switch", "turn_on"): "on", ("fan", "turn_off"): "off",
    ("fan", "turn_on"): "on", ("homeassistant", "turn_off"): "off", ("homeassistant", "turn_on"): "on",
}


def not_done_condition(actions: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """``not all targets already in the end state`` for simple device actions."""
    checks: list[str] = []
    for step in actions:
        action = step.get("action")
        if not isinstance(action, str) or "." not in action:
            continue
        domain, service = action.split(".", 1)
        target = step.get("target")
        ids = target.get("entity_id") if isinstance(target, Mapping) else None
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        end = _END_STATES.get((domain, service))
        if end is None or not ids:
            return None
        checks.extend(f"is_state('{entity_id}', '{end}')" for entity_id in ids)
    if not checks:
        return None
    return {"condition": "template", "value_template": "{{ not (" + " and ".join(checks) + ") }}"}


def trigger_config(guard: WeatherGuard) -> dict[str, Any]:
    """"Wenn Regen angesagt ist": look every 30 minutes."""
    return {"trigger": "time_pattern", "minutes": f"/{CHECK_MINUTES}"}


def describe_guard(guard: WeatherGuard, role: str) -> str:
    """The preview names the source of every part."""
    def name(entity_id: str | None) -> str:
        return f"„{guard.names.get(entity_id or '', entity_id or '')}“"

    parts: list[str] = []
    for kind in guard.kinds:
        if kind == "rain_soon":
            parts.append(
                f"laut Wettervorhersage {name(guard.weather_entity_id)} in den nächsten {SOON_HOURS} Stunden Regen "
                f"angesagt ist (Regenwetter oder Regenwahrscheinlichkeit ab {RAIN_PROBABILITY} %; ich sehe alle "
                f"{CHECK_MINUTES} Minuten nach)"
            )
        elif kind == "rain_today":
            parts.append(
                f"laut Wettervorhersage {name(guard.weather_entity_id)} heute Regen angesagt ist "
                f"(Regenwetter oder Regenwahrscheinlichkeit ab {RAIN_PROBABILITY} %)"
            )
        elif kind == "no_rain_today":
            parts.append(
                f"laut Wettervorhersage {name(guard.weather_entity_id)} heute kein Regen angesagt ist "
                f"(kein Regenwetter, Regenwahrscheinlichkeit unter {RAIN_PROBABILITY} %)"
            )
        elif kind == "raining":
            parts.append(
                f"{name(guard.rain_sensor_id)} Regen meldet" if guard.rain_sensor_id is not None
                else f"{name(guard.weather_entity_id)} Regen meldet"
            )
        elif kind == "no_rain_recent":
            source = guard.rain_sensor_id or guard.rain_amount_id
            parts.append(f"{name(source)} in den letzten {RECENT_HOURS} Stunden keinen Regen gemeldet hat")
    text = " und ".join(parts)
    return text if role == "trigger" else f"aber nur, wenn {text}"


def validate_guard(guard: WeatherGuard, entities: Sequence[EntitySnapshot]) -> str | None:
    """Closed kinds, sources of the right domain that exist."""
    known = {entity.entity_id: entity for entity in entities}
    if not guard.kinds or not set(guard.kinds) <= {
        "rain_soon", "raining", "no_rain_today", "rain_today", "no_rain_recent"
    }:
        return "unbekannte Regenbedingung"
    for entity_id, domain in ((guard.weather_entity_id, "weather"), (guard.rain_sensor_id, "binary_sensor"),
                              (guard.rain_amount_id, "sensor")):
        if entity_id is None:
            continue
        if entity_id not in known or not entity_id.startswith(domain + ".") or not re.fullmatch(
            r"[a-z_]+\.[a-z0-9_]+", entity_id
        ):
            return f"unbekannte Quelle {entity_id}"
    if guard.rain_sensor_id is not None and guard.rain_sensor_id not in {e.entity_id for e in rain_sensors(entities)}:
        return f"{guard.rain_sensor_id} ist kein Regensensor"
    if guard.rain_amount_id is not None and guard.rain_amount_id not in {
        e.entity_id for e in rain_amount_sensors(entities)
    }:
        return f"{guard.rain_amount_id} misst keine Regenmenge"
    if {"rain_soon", "no_rain_today", "rain_today"} & set(guard.kinds) and guard.weather_entity_id is None:
        return "Vorhersage ohne Wetter-Entität"
    if "no_rain_recent" in guard.kinds and guard.rain_sensor_id is None and guard.rain_amount_id is None:
        return "Regen der letzten 24 Stunden ohne Sensor"
    return None


# --- the running turn ----------------------------------------------------------------

@dataclass(frozen=True)
class WeatherTurn:
    """A request whose rain clause was removed; the automation that the rest
    becomes gets this guard (or trigger) attached at its preview."""

    full_text: str
    role: str  # "guard" | "trigger"
    guard: WeatherGuard


_WEATHER_TURN: ContextVar[WeatherTurn | None] = ContextVar("homeintent_weather_turn", default=None)
# A rain clause whose automation is still being completed (a follow-up
# question such as "Welche Markise?"), per conversation; bounded.
_PENDING: dict[str, WeatherTurn] = {}
_PENDING_LIMIT = 64


def begin_weather_turn(turn: WeatherTurn | None, conversation_id: str | None = None) -> None:
    """Set the rain clause of the running turn (and keep it for the
    conversation until its automation is previewed)."""
    _WEATHER_TURN.set(turn)
    if conversation_id is None:
        return
    if turn is None:
        _PENDING.pop(conversation_id, None)
        return
    if len(_PENDING) >= _PENDING_LIMIT:
        _PENDING.pop(next(iter(_PENDING)))
    _PENDING[conversation_id] = turn


def resume_weather_turn(conversation_id: str | None) -> None:
    """At the start of a turn: the conversation's open rain clause, if any."""
    _WEATHER_TURN.set(_PENDING.get(conversation_id or "") if conversation_id else None)


def weather_turn() -> WeatherTurn | None:
    return _WEATHER_TURN.get()


def consume_weather_turn(conversation_id: str | None) -> WeatherTurn | None:
    """The clause for the preview being built - used once."""
    turn = _WEATHER_TURN.get()
    _WEATHER_TURN.set(None)
    if conversation_id is not None:
        _PENDING.pop(conversation_id, None)
    return turn


def attach(model: Any, turn: WeatherTurn) -> Any:
    """The automation model with the rain trigger/guard of this turn."""
    from dataclasses import replace

    from .nlu.automation_model import TriggerModel, TriggerTarget, TriggerType
    from .nlu.semantic_state import SemanticState

    guard = turn.guard
    # The automation is named by what was said, with its rain clause.
    model = replace(model, source_text=turn.full_text)
    if turn.role == "guard":
        note = "Das passiert nur, " + describe_guard(guard, "guard").removeprefix("aber nur, ") + "."
        return replace(model, weather_guard=guard, notes=(*model.notes, note))
    if "rain_soon" in guard.kinds:
        assert guard.weather_entity_id is not None
        note = f"Ich sehe dafür alle {CHECK_MINUTES} Minuten in der Vorhersage der nächsten {SOON_HOURS} Stunden nach " \
               f"(Regenwetter oder Regenwahrscheinlichkeit ab {RAIN_PROBABILITY} %)."
        if not any(getattr(action, "type", None) is not None and action.type.name == "NOTIFY"
                   for action in model.actions):
            note += " Ist es schon so, passiert nichts."
        return replace(
            model, triggers=(TriggerModel(type=TriggerType.WEATHER, target=TriggerTarget(
                entity_id=guard.weather_entity_id)),),
            weather_guard=guard, notes=(*model.notes, note), situation=None,
        )
    if "rain_today" in guard.kinds:
        note = "Das passiert nur, " + describe_guard(guard, "trigger") + "."
        return replace(model, weather_guard=guard, notes=(*model.notes, note))
    # "Wenn es regnet": the rain sensor, else the weather state.
    if guard.rain_sensor_id is not None:
        trigger = TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id=guard.rain_sensor_id),
                               state=SemanticState.ON, raw_to=("on",))
    else:
        assert guard.weather_entity_id is not None
        trigger = TriggerModel(type=TriggerType.STATE, target=TriggerTarget(entity_id=guard.weather_entity_id),
                               state=SemanticState.ON, raw_to=RAIN_CONDITIONS)
    return replace(model, triggers=(trigger,), situation=None)


def guard_holds_now(
    guard: WeatherGuard,
    states: Mapping[str, str],
    held_seconds: Mapping[str, float],
    hourly: Sequence[Mapping[str, Any]] | None,
    daily: Sequence[Mapping[str, Any]] | None,
) -> tuple[bool, str]:
    """The guard evaluated right now (an immediate command with a rain
    condition): (holds, the reason in words).  Missing forecast data is
    never read as "kein Regen"."""
    names = guard.names
    for kind in guard.kinds:
        if kind in {"no_rain_today", "rain_today"}:
            if not daily:
                return False, f"Die Vorhersage von „{names.get(guard.weather_entity_id or '', '')}“ kann ich gerade nicht lesen"
            rainy = _rainy(daily[0])
            detail = _rain_detail(daily[0], {"precipitation": "mm"})
            if kind == "no_rain_today" and rainy:
                return False, f"Heute ist laut „{names.get(guard.weather_entity_id or '', '')}“ Regen angesagt{detail}"
            if kind == "rain_today" and not rainy:
                return False, "Heute ist kein Regen angesagt"
        elif kind == "rain_soon":
            if not hourly:
                return False, "Die stündliche Vorhersage kann ich gerade nicht lesen"
            if not any(_rainy(item) for item in hourly[:SOON_HOURS]):
                return False, f"In den nächsten {SOON_HOURS} Stunden ist kein Regen angesagt"
        elif kind == "no_rain_recent":
            source = guard.rain_sensor_id or guard.rain_amount_id or ""
            state = states.get(source)
            held = held_seconds.get(source, -1.0)
            if state is None:
                return False, f"„{names.get(source, source)}“ meldet gerade nichts"
            if guard.rain_sensor_id is not None:
                recent = state != "off" or held < RECENT_HOURS * 3600
            else:
                recent = held < RECENT_HOURS * 3600 and (_number(state) or 0) > 0
            if recent:
                return False, f"„{names.get(source, source)}“ hat in den letzten {RECENT_HOURS} Stunden Regen gemeldet"
        elif kind == "raining":
            source = guard.rain_sensor_id or guard.weather_entity_id or ""
            state = states.get(source)
            if guard.rain_sensor_id is not None and state != "on" or (
                guard.rain_sensor_id is None and state not in RAIN_CONDITIONS
            ):
                return False, "Es regnet gerade nicht"
    return True, ""
