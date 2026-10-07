"""Bounded natural-language adapter for Home Assistant recorder statistics."""

from __future__ import annotations

import logging
import re
from functools import partial
from dataclasses import dataclass
from datetime import datetime, timezone
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

# 7.9.3 A6: without a recorder every history, consumption and summary
# question failed with a full traceback in the log.  Now one line per cause
# and hour; the answer stays honest.
RECORDER_WARNING_INTERVAL = 3600.0
_RECORDER_WARNED: dict[tuple[int, str, str], float] = {}


def warn_recorder(what: str, err: BaseException | str, hass: object = None) -> None:
    """One single-line warning per cause, at most once per hour (per Home
    Assistant instance)."""
    import time

    detail = err if isinstance(err, str) else str(err)
    cause = err if isinstance(err, str) else f"{type(err).__name__}: {err}"
    key = (id(hass), what, cause[:200])
    now = time.monotonic()
    last = _RECORDER_WARNED.get(key)
    if last is not None and now - last < RECORDER_WARNING_INTERVAL:
        _LOGGER.debug("Recorder %s failed again: %s", what, detail)
        return
    _RECORDER_WARNED[key] = now
    _LOGGER.warning(
        "Recorder %s failed: %s (die Antwort sagt es ehrlich; diese Warnung höchstens einmal je Stunde)",
        what, detail,
    )


def recorder_loaded(hass: object) -> bool:
    """False when Home Assistant runs without the recorder component."""
    components = getattr(getattr(hass, "config", None), "components", None)
    return components is None or "recorder" in components


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


def parse_history_query(
    text: str, entities: list[EntitySnapshot], now: datetime
) -> HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery | None:
    """Parse only explicit statistic questions with one named sensor.

    Since 7.5.1 the language island "Verlauf" derives the frame from words
    and lexicon tables (``derive_history_query``); the historic sentence
    patterns are gone.
    """
    return derive_history_query(text, entities, now)


def derive_history_query(
    text: str, entities: list[EntitySnapshot], now: datetime
) -> HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery | None:
    """Language island "Verlauf" (7.5.1): the same frames from words and
    lexicon tables (``nlu.history_frame``) instead of sentence patterns."""
    from .nlu.history_frame import (
        comparison_periods,
        period_key,
        period_range,
        state_question_of,
        statistic_of,
        words_of,
    )

    words = words_of(text)
    value = " ".join(words)
    compared = comparison_periods(words)
    if compared is not None:
        entity = _measured_sensor(text, [item for item in entities if item.domain == "sensor"])
        first, second = period_range(compared[0], now), period_range(compared[1], now)
        if entity is not None and first is not None and second is not None:
            metric = HistoryMetric.CHANGE if statistic_of(words) == "CHANGE" else HistoryMetric.MEAN
            return ComparativeHistoryQuery(entity, metric, first, second)
    time_range = period_range(period_key(words), now)
    state_question = state_question_of(words)
    if state_question is not None and time_range is not None:
        entity = _mentioned_entity(
            text, [item for item in entities if item.domain in _STATE_HISTORY_DOMAINS]
        )
        target = _history_state_target(value, entity.domain) if entity is not None else None
        if entity is not None and target is not None:
            start, end, _period, label = time_range
            return StateHistoryQuery(
                entity, StateHistoryMetric[state_question], target[0], target[1], start, end, label
            )
    statistic = statistic_of(words)
    if statistic is None:
        return None
    entity = _measured_sensor(text, [item for item in entities if item.domain == "sensor"])
    if entity is None or time_range is None:
        return None
    start, end, period, label = time_range
    return HistoryQuery(entity, HistoryMetric[statistic], start, end, period, label)


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
        warn_recorder("statistics query", err, hass)
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
        warn_recorder("comparison query", err, hass)
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
        warn_recorder("state-history query", err, hass)
        return "Die Home-Assistant-Verlaufsdaten sind momentan nicht verfügbar."
    return render_state_history_result(query, result)


async def async_get_numeric_samples(
    hass, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """See ``async_read_numeric_samples``; a recorder failure is an empty list."""
    samples = await async_read_numeric_samples(hass, entity_id, start, end)
    return samples if samples is not None else []


async def async_read_numeric_samples(
    hass, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]] | None:
    """(time, value) of one sensor in a window, from the recorder (7.9 W3).

    Includes the state valid at the window's start; non-numeric states
    (unknown, unavailable) are skipped.  Recorder failure is no evidence:
    an empty list, never a guessed value.
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
        warn_recorder("numeric samples", err, hass)
        return None
    rows = result.get(entity_id, ()) if isinstance(result, dict) else ()
    samples: list[tuple[datetime, float]] = []
    for item in rows if isinstance(rows, (list, tuple)) else ():
        raw, moment = _state_value(item), _state_time(item)
        if raw is None or moment is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        samples.append((max(moment, start), value))
    return samples


async def async_read_state_rows(
    hass, entity_ids: list[str], start: datetime, end: datetime, *, attributes: bool = False
) -> dict[str, list[tuple[datetime, str, dict]]] | None:
    """(time, state, attributes) per entity in a window, including the state
    valid at the start (7.9.2 B1). ``None`` when the recorder cannot answer."""
    if not entity_ids:
        return {}
    if not recorder_loaded(hass):
        warn_recorder("state rows", "Recorder ist nicht geladen", hass)
        return None
    try:
        from homeassistant.components.recorder import history

        result = await hass.async_add_executor_job(
            partial(
                history.get_significant_states,
                hass,
                start,
                end,
                entity_ids=entity_ids,
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=not attributes,
            )
        )
    except Exception as err:
        warn_recorder("state rows", err, hass)
        return None
    rows: dict[str, list[tuple[datetime, str, dict]]] = {}
    for entity_id, items in (result.items() if isinstance(result, dict) else ()):
        for item in items if isinstance(items, (list, tuple)) else ():
            state, moment = _state_value(item), _state_time(item)
            if state is None or moment is None:
                continue
            raw = getattr(item, "attributes", None)
            if raw is None and isinstance(item, dict):
                raw = item.get("attributes")
            rows.setdefault(entity_id, []).append((max(moment, start), state, dict(raw or {})))
    return rows


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
        warn_recorder("transition evidence", err, hass)
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
    "async_execute_history_query", "async_get_numeric_samples", "async_get_transition_evidence",
    "async_read_numeric_samples", "async_read_state_rows",
    "parse_history_query", "render_history_result", "render_state_history_result",
)


async def async_read_forecast(hass, entity_id: str, kind: str) -> list[dict] | None:
    """``weather.get_forecasts`` for one entity (7.9.3 B1): a bounded read
    with ``return_response``, never a write; ``None`` when it fails."""
    try:
        result = await hass.services.async_call(
            "weather", "get_forecasts", {"entity_id": entity_id, "type": kind},
            blocking=True, return_response=True, context=call_context(),
        )
    except Exception as err:  # noqa: BLE001 - no forecast is an honest answer
        _LOGGER.debug("Weather forecast %s (%s) failed: %s", entity_id, kind, err)
        return None
    item = result.get(entity_id) if isinstance(result, dict) else None
    forecast = item.get("forecast") if isinstance(item, dict) else None
    return [entry for entry in forecast if isinstance(entry, dict)] if isinstance(forecast, list) else None
