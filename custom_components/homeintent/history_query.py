"""Bounded natural-language adapter for Home Assistant recorder statistics."""

from __future__ import annotations

import logging
import re
from functools import partial
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto

from .entities import (
    EntitySnapshot,
    format_spoken_number,
    is_outdoor_entity,
    normalize_for_compare,
    spoken_unit,
)
from .nlu.entity_resolution import ResolutionStatus, resolve_mentioned_target
from .execution_context import call_context

_LOGGER = logging.getLogger(__name__)


class HistoryMetric(Enum):
    MEAN = auto()
    MIN = auto()
    MAX = auto()
    CHANGE = auto()


class StateHistoryMetric(Enum):
    COUNT = auto()
    DURATION = auto()
    OCCURRED = auto()
    LAST = auto()


@dataclass(frozen=True)
class HistoryQuery:
    entity: EntitySnapshot
    metric: HistoryMetric
    start: datetime
    end: datetime
    period: str
    period_label: str


@dataclass(frozen=True)
class StateHistoryQuery:
    entity: EntitySnapshot
    metric: StateHistoryMetric
    target_states: frozenset[str]
    target_label: str
    start: datetime
    end: datetime
    period_label: str


@dataclass(frozen=True)
class ComparativeHistoryQuery:
    entity: EntitySnapshot
    metric: HistoryMetric
    first: tuple[datetime, datetime, str, str]
    second: tuple[datetime, datetime, str, str]


@dataclass(frozen=True)
class TransitionEvidence:
    """Bounded recorder evidence for one requested state transition."""

    available: bool
    occurred: bool | None
    observed_states: tuple[str, ...] = ()


_HISTORY_CUE_RE = re.compile(
    r"\b(?:durchschnitt|mittelwert|minimum|minimal|niedrigst|kleinst|maximum|"
    r"maximal|hoechst|groesst|verbraucht|verbrauch|erzeugt|produziert|"
    r"veraendert|veraenderung|im\s+schnitt)\w*\b"
)
_MEAN_RE = re.compile(r"\b(?:durchschnitt\w*|mittelwert\w*|im\s+schnitt)\b")
_MIN_RE = re.compile(r"\b(?:minimum|minimal|niedrigst|kleinst|tiefst)\w*\b")
_MAX_RE = re.compile(r"\b(?:maximum|maximal|hoechst|groesst)\w*\b")
_CHANGE_RE = re.compile(
    r"\b(?:verbraucht|verbrauch|erzeugt|produziert|veraendert|veraenderung)\w*\b"
)

# Spoken measurement cue -> HA sensor device classes. Lets "die Temperatur im
# Wohnzimmer" or "wie kalt war es draussen" find the sensor through its area
# and measurement even when the spoken words are not the sensor's name.
_MEASUREMENT_CUES: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    # ``\w*`` before the noun also covers compounds ("Durchschnittstemperatur").
    (re.compile(r"\b(?:\w*temperatur\w*|warm|waermst\w*|kalt|kaelt\w*|grad)\b"), frozenset({"temperature"})),
    (re.compile(r"\b(?:\w*feuchtigkeit|feucht\w*)\b"), frozenset({"humidity"})),
    (re.compile(r"\b(?:co2|kohlendioxid|luftqualitaet)\b"), frozenset({"carbon_dioxide"})),
    (re.compile(r"\b(?:energie\w*|energiezaehler|kilowattstunden|kwh)\b"), frozenset({"energy"})),
    (re.compile(r"\b(?:strom\w*|leistung\w*|watt)\b"), frozenset({"power"})),
    (re.compile(r"\b(?:helligkeit|hell|dunkel)\b"), frozenset({"illuminance"})),
)
_OUTDOOR_RE = re.compile(r"\b(?:draussen|aussen|im freien|vor dem haus)\b")

_STATE_HISTORY_DOMAINS = frozenset({
    "binary_sensor", "switch", "cover", "input_boolean", "light", "fan",
    "humidifier", "media_player", "vacuum", "lawn_mower", "valve",
})


def _history_state_target(
    value: str, domain: str
) -> tuple[frozenset[str], str] | None:
    """Map a spoken historical predicate to raw HA states per domain."""
    if re.search(r"\b(?:spielte|gespielt|wiedergabe)\b", value):
        return (
            (frozenset({"playing", "buffering"}), "bei der Wiedergabe")
            if domain == "media_player" else None
        )
    if re.search(r"\b(?:lief|gelaufen|arbeitete|gearbeitet|reinigte|gereinigt|saugte|maehte)\b", value):
        states = {
            "media_player": frozenset({"playing", "buffering"}),
            "vacuum": frozenset({"cleaning", "returning"}),
            "lawn_mower": frozenset({"mowing", "returning"}),
            "fan": frozenset({"on"}),
            "humidifier": frozenset({"on"}),
        }.get(domain)
        return (states, "aktiv") if states is not None else None
    if re.search(r"\b(?:offen|geoeffnet)\b", value):
        return frozenset({"on", "open", "opening"}), "offen"
    if re.search(r"\b(?:geschlossen|zu)\b", value):
        return frozenset({"off", "closed", "closing"}), "geschlossen"
    if re.search(r"\b(?:an|aktiv)\b", value):
        return frozenset({"on"}), "aktiv"
    if re.search(r"\b(?:aus|inaktiv)\b", value):
        return frozenset({"off"}), "inaktiv"
    return None


def _mentioned_entity(text: str, entities: list[EntitySnapshot]) -> EntitySnapshot | None:
    domains = frozenset(entity.domain for entity in entities)
    result = resolve_mentioned_target(text, entities, domains)
    return result.entity if result.status is ResolutionStatus.RESOLVED else None


def _is_outdoor(entity: EntitySnapshot) -> bool:
    return is_outdoor_entity(entity)


def _mentions_area(value: str, entity: EntitySnapshot) -> bool:
    for name in (entity.area_name, *entity.area_aliases):
        if name and re.search(
            rf"\b{re.escape(normalize_for_compare(name))}\b", value
        ):
            return True
    return False


def _measured_sensor(text: str, sensors: list[EntitySnapshot]) -> EntitySnapshot | None:
    """Resolve a sensor by its name, or else by spoken area plus measurement.

    Only a unique match is returned: two temperature sensors in one room stay
    unresolved rather than being guessed.
    """
    named = _mentioned_entity(text, sensors)
    if named is not None:
        return named
    value = normalize_for_compare(text)
    classes: frozenset[str] | None = None
    for pattern, device_classes in _MEASUREMENT_CUES:
        if pattern.search(value):
            classes = device_classes
            break
    if classes is None:
        return None
    candidates = [item for item in sensors if item.device_class in classes]
    if _OUTDOOR_RE.search(value):
        located = [item for item in candidates if _is_outdoor(item)]
    else:
        located = [item for item in candidates if _mentions_area(value, item)]
        if not located and len(candidates) == 1:
            located = candidates
    return located[0] if len(located) == 1 else None


def _time_range(value: str, now: datetime) -> tuple[datetime, datetime, str, str] | None:
    if re.search(r"\bvorgestern\b", value):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return end - timedelta(days=1), end, "hour", "vorgestern"
    if re.search(r"\bgestern\b", value):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return end - timedelta(days=1), end, "hour", "gestern"
    if re.search(r"\b(?:heute|heutigen)\b", value):
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now, "hour", "heute"
    if re.search(r"\b(?:diese|dieser|aktuellen?)\s+woche\b", value):
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, "day", "diese Woche"
    if re.search(r"\b(?:letzte|letzter|vergangenen?)\s+woche\b", value):
        this_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return this_week - timedelta(days=7), this_week, "day", "letzte Woche"
    if re.search(r"\b(?:dieser|diesen|aktuellen?)\s+monat\b", value):
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now, "day", "diesen Monat"
    if re.search(r"\b(?:letzte[snm]?|vergangene[snm]?)\s+24\s+stunden\b", value):
        return now - timedelta(hours=24), now, "hour", "in den letzten 24 Stunden"
    if re.search(r"\b(?:zuletzt|letzte[snm]?\s+sieben\s+tage)\b", value):
        return now - timedelta(days=7), now, "hour", "in den letzten sieben Tagen"
    return None


def parse_history_query(
    text: str, entities: list[EntitySnapshot], now: datetime
) -> HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery | None:
    """Parse only explicit statistic questions with one named sensor."""
    value = normalize_for_compare(text)
    comparison = re.search(r"\b(?:vergleich|verglichen|hoeher|niedriger|mehr|weniger)\b", value)
    if comparison is not None:
        entity = _measured_sensor(text, [item for item in entities if item.domain == "sensor"])
        today = _time_range("heute", now)
        day_before = _time_range("vorgestern", now)
        yesterday = _time_range("gestern", now)
        this_week = _time_range("diese woche", now)
        last_week = _time_range("letzte woche", now)
        periods = (
            (yesterday, day_before)
            if re.search(r"\bgestern\b", value) and "vorgestern" in value
            else (today, yesterday)
            if "heute" in value and re.search(r"\bgestern\b", value)
            else (this_week, last_week)
            if "woche" in value and re.search(r"\b(?:diese|aktuelle)\w*\b", value)
            else None
        )
        if (
            entity is not None
            and periods is not None
            and periods[0] is not None
            and periods[1] is not None
        ):
            metric = (
                HistoryMetric.CHANGE
                if _CHANGE_RE.search(value)
                else HistoryMetric.MEAN
            )
            return ComparativeHistoryQuery(entity, metric, periods[0], periods[1])
    time_range = _time_range(value, now)
    state_metric = (
        StateHistoryMetric.COUNT
        if re.search(r"\b(?:wie oft|anzahl)\b", value)
        else StateHistoryMetric.DURATION
        if re.search(r"\b(?:wie lange|dauer)\b", value)
        else StateHistoryMetric.LAST
        if re.search(r"\b(?:wann\b.*\bzuletzt|zuletzt\b.*\bwann|wann\s+wurde)\b", value)
        else StateHistoryMetric.OCCURRED
        if re.search(r"^(?:war|waren|hat|haben)\b", value)
        else None
    )
    if state_metric is not None and time_range is not None:
        entity = _mentioned_entity(
            text, [item for item in entities if item.domain in _STATE_HISTORY_DOMAINS]
        )
        target = _history_state_target(value, entity.domain) if entity is not None else None
        if entity is not None and target is not None:
            start, end, _period, label = time_range
            return StateHistoryQuery(
                entity, state_metric, target[0], target[1], start, end, label
            )
    if _HISTORY_CUE_RE.search(value) is None:
        return None
    entity = _measured_sensor(text, [item for item in entities if item.domain == "sensor"])
    if entity is None or time_range is None:
        return None
    if _MEAN_RE.search(value):
        metric = HistoryMetric.MEAN
    elif _MIN_RE.search(value):
        metric = HistoryMetric.MIN
    elif _MAX_RE.search(value):
        metric = HistoryMetric.MAX
    elif _CHANGE_RE.search(value):
        metric = HistoryMetric.CHANGE
    else:
        return None
    start, end, period, label = time_range
    return HistoryQuery(entity, metric, start, end, period, label)


def _numeric_values(rows: list[dict], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw = row.get(key)
        if isinstance(raw, (int, float)):
            values.append(float(raw))
    return values


def _aggregate_rows(metric: HistoryMetric, rows: list[dict]) -> float | None:
    key = {
        HistoryMetric.MEAN: "mean",
        HistoryMetric.MIN: "min",
        HistoryMetric.MAX: "max",
        HistoryMetric.CHANGE: "change",
    }[metric]
    values = _numeric_values(rows, key)
    if not values and metric is HistoryMetric.CHANGE:
        sums = _numeric_values(rows, "sum")
        if len(sums) >= 2:
            values = [sums[-1] - sums[0]]
    if not values:
        return None
    return (
        sum(values) / len(values)
        if metric is HistoryMetric.MEAN
        else min(values) if metric is HistoryMetric.MIN
        else max(values) if metric is HistoryMetric.MAX
        else sum(values)
    )


def statistic_rows(response: object, statistic_id: str) -> list[dict]:
    """Rows for one statistic from ``recorder.get_statistics``.

    Home Assistant answers ``{"statistics": {statistic_id: [...]}}``; the
    flat ``{statistic_id: [...]}`` form is accepted as well.
    """
    mapping = response if isinstance(response, dict) else {}
    nested = mapping.get("statistics")
    if isinstance(nested, dict):
        mapping = nested
    rows = mapping.get(statistic_id, [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _speak_value(number: float, entity: EntitySnapshot) -> str:
    rounded = round(number, 1) if abs(number) < 1000 else round(number)
    unit = spoken_unit(entity.unit)
    return f"{format_spoken_number(rounded)} {unit}" if unit else format_spoken_number(rounded)


def render_history_result(query: HistoryQuery, response: object) -> str:
    """Render recorder.get_statistics' response without assuming every key."""
    rows = statistic_rows(response, query.entity.entity_id)
    if not rows:
        return f"Für {query.entity.friendly_name} liegen {query.period_label} keine Statistikdaten vor."
    number = _aggregate_rows(query.metric, rows)
    if number is None:
        return f"Für {query.entity.friendly_name} liegt {query.period_label} kein passender Zahlenwert vor."
    label = {
        HistoryMetric.MEAN: "Der Durchschnitt",
        HistoryMetric.MIN: "Das Minimum",
        HistoryMetric.MAX: "Das Maximum",
        HistoryMetric.CHANGE: "Die Veränderung",
    }[query.metric]
    return (
        f"{label} von {query.entity.friendly_name} betrug {query.period_label} "
        f"{_speak_value(number, query.entity)}."
    )


async def async_execute_history_query(
    hass, query: HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery
) -> str:
    """Call HA's recorder response service only for a parsed history query."""
    if isinstance(query, StateHistoryQuery):
        return await _async_execute_state_history_query(hass, query)
    if isinstance(query, ComparativeHistoryQuery):
        return await _async_execute_comparative_history_query(hass, query)
    service_data = {
        "start_time": query.start,
        "end_time": query.end,
        "statistic_ids": [query.entity.entity_id],
        "period": query.period,
        "types": [
            {
                HistoryMetric.MEAN: "mean",
                HistoryMetric.MIN: "min",
                HistoryMetric.MAX: "max",
                HistoryMetric.CHANGE: "change",
            }[query.metric]
        ],
    }
    try:
        # ``recorder.get_statistics`` is exposed as a HA service, but with
        # ``return_response=True`` this is a bounded read and never mutates HA.
        result = await hass.services.async_call(
            "recorder",
            "get_statistics",
            service_data,
            blocking=True,
            return_response=True,
            context=call_context(),
        )
    except Exception as err:  # Recorder absent/disabled or API not supported.
        _LOGGER.warning("Recorder statistics query failed: %s", err, exc_info=True)
        return "Die Home-Assistant-Verlaufsdaten sind momentan nicht verfügbar."
    return render_history_result(query, result)


async def _async_execute_comparative_history_query(
    hass, query: ComparativeHistoryQuery
) -> str:
    """Compare two equally bounded recorder-statistics periods."""
    statistic_type = "change" if query.metric is HistoryMetric.CHANGE else "mean"

    async def fetch(period: tuple[datetime, datetime, str, str]):
        return await hass.services.async_call(
            "recorder",
            "get_statistics",
            {
                "start_time": period[0],
                "end_time": period[1],
                "statistic_ids": [query.entity.entity_id],
                "period": period[2],
                "types": [statistic_type],
            },
            blocking=True,
            return_response=True,
            context=call_context(),
        )

    try:
        first_response = await fetch(query.first)
        second_response = await fetch(query.second)
    except Exception as err:
        _LOGGER.warning("Recorder comparison query failed: %s", err, exc_info=True)
        return "Die Home-Assistant-Verlaufsdaten sind momentan nicht verfügbar."
    first_value = _aggregate_rows(
        query.metric, statistic_rows(first_response, query.entity.entity_id)
    )
    second_value = _aggregate_rows(
        query.metric, statistic_rows(second_response, query.entity.entity_id)
    )
    if first_value is None or second_value is None:
        return f"Für den Vergleich von {query.entity.friendly_name} fehlen Statistikdaten."
    first_value = round(first_value, 1)
    difference = round(first_value - round(second_value, 1), 1)
    relation = "höher" if difference > 0 else "niedriger" if difference < 0 else "gleich"
    if relation == "gleich":
        return (
            f"{query.entity.friendly_name} war {query.first[3]} und "
            f"{query.second[3]} gleich: {_speak_value(first_value, query.entity)}."
        )
    return (
        f"{query.entity.friendly_name} lag {query.first[3]} bei "
        f"{_speak_value(first_value, query.entity)}; das sind "
        f"{_speak_value(abs(difference), query.entity)} {relation} als {query.second[3]}."
    )


def _spoken_duration(total_seconds: float) -> str:
    """Hours and minutes; below one minute in seconds, never "0 Minuten"."""
    if total_seconds < 59.5:
        seconds = round(total_seconds)
        return f"{seconds} Sekunde" + ("" if seconds == 1 else "n")
    minutes = round(total_seconds / 60)
    hours, remainder = divmod(minutes, 60)
    hour_text = f"{hours} Stunde" + ("" if hours == 1 else "n")
    minute_text = f"{remainder} Minute" + ("" if remainder == 1 else "n")
    if hours and remainder:
        return f"{hour_text} und {minute_text}"
    return hour_text if hours else minute_text


def _state_value(item: object) -> str | None:
    if isinstance(item, dict):
        value = item.get("state", item.get("s"))
    else:
        value = getattr(item, "state", None)
    return str(value).casefold() if value is not None else None


def _state_time(item: object) -> datetime | None:
    if isinstance(item, dict):
        value = item.get("last_changed", item.get("lu"))
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        value = getattr(item, "last_changed", None) or getattr(item, "last_updated", None)
    return value if isinstance(value, datetime) else None


def render_state_history_result(
    query: StateHistoryQuery, response: object
) -> str:
    """Summarise state transitions returned by recorder.history."""
    mapping = response if isinstance(response, dict) else {}
    raw_rows = mapping.get(query.entity.entity_id, [])
    rows = raw_rows if isinstance(raw_rows, list) else []
    if not rows:
        return f"Für {query.entity.friendly_name} liegen {query.period_label} keine Verlaufsdaten vor."

    if query.metric is StateHistoryMetric.COUNT:
        count = 0
        previous = _state_value(rows[0])
        for row in rows[1:]:
            current = _state_value(row)
            if current in query.target_states and previous not in query.target_states:
                count += 1
            previous = current
        return (
            f"{query.entity.friendly_name} war {query.period_label} "
            f"{count}-mal {query.target_label}."
        )

    if query.metric is StateHistoryMetric.OCCURRED:
        occurred = any(_state_value(row) in query.target_states for row in rows)
        return (
            f"Ja, {query.entity.friendly_name} war {query.period_label} {query.target_label}."
            if occurred
            else f"Nein, {query.entity.friendly_name} war {query.period_label} nicht {query.target_label}."
        )

    if query.metric is StateHistoryMetric.LAST:
        matching_times = [
            changed
            for row in rows
            if _state_value(row) in query.target_states
            and (changed := _state_time(row)) is not None
        ]
        if not matching_times:
            return (
                f"{query.entity.friendly_name} war {query.period_label} "
                f"nicht {query.target_label}."
            )
        last = max(matching_times)
        return (
            f"{query.entity.friendly_name} war zuletzt am "
            f"{last.astimezone(query.end.tzinfo).strftime('%d.%m. um %H:%M Uhr')} "
            f"{query.target_label}."
        )

    total_seconds = 0.0
    for index, row in enumerate(rows):
        if _state_value(row) not in query.target_states:
            continue
        start = _state_time(row) or query.start
        end = (
            _state_time(rows[index + 1])
            if index + 1 < len(rows)
            else query.end
        )
        if end is not None:
            total_seconds += max(0.0, (min(end, query.end) - max(start, query.start)).total_seconds())
    duration = _spoken_duration(total_seconds)
    return (
        f"{query.entity.friendly_name} war {query.period_label} insgesamt "
        f"{duration} {query.target_label}."
    )


async def _async_execute_state_history_query(hass, query: StateHistoryQuery) -> str:
    """Read one bounded entity history through recorder's supported API."""
    try:
        from homeassistant.components.recorder import history

        result = await hass.async_add_executor_job(
            partial(
                history.get_significant_states,
                hass,
                query.start,
                query.end,
                entity_ids=[query.entity.entity_id],
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=True,
            )
        )
    except Exception as err:  # Recorder absent/disabled or history API unavailable.
        _LOGGER.warning("Recorder state-history query failed: %s", err, exc_info=True)
        return "Die Home-Assistant-Verlaufsdaten sind momentan nicht verfügbar."
    return render_state_history_result(query, result)


async def async_get_transition_evidence(
    hass,
    entity_id: str,
    *,
    start: datetime,
    end: datetime,
    from_state: str,
    to_state: str,
) -> TransitionEvidence:
    """Read transition evidence through the one recorder adapter.

    ``not_home`` means leaving home and therefore also covers a configured HA
    zone state. Empty/unavailable history is unknown evidence, never proof.
    """
    try:
        from homeassistant.components.recorder import history

        result = await hass.async_add_executor_job(
            partial(
                history.get_significant_states,
                hass,
                start,
                end,
                entity_ids=[entity_id],
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=True,
            )
        )
    except Exception as err:
        _LOGGER.warning("Recorder transition evidence failed: %s", err, exc_info=True)
        return TransitionEvidence(False, None)
    rows = result.get(entity_id, ()) if isinstance(result, dict) else ()
    states = tuple(
        state for item in rows if (state := _state_value(item)) is not None
    ) if isinstance(rows, (list, tuple)) else ()
    if not states:
        return TransitionEvidence(False, None)
    expected_from = normalize_for_compare(from_state)
    expected_to = normalize_for_compare(to_state)
    occurred = any(
        previous == expected_from
        and (
            current != "home"
            if expected_to == "not_home"
            else current == expected_to
        )
        for previous, current in zip(states, states[1:])
    )
    return TransitionEvidence(True, occurred, states)


__all__ = (
    "ComparativeHistoryQuery", "HistoryMetric", "HistoryQuery",
    "StateHistoryMetric", "StateHistoryQuery", "TransitionEvidence",
    "async_execute_history_query", "async_get_transition_evidence",
    "parse_history_query", "render_history_result", "render_state_history_result",
)
