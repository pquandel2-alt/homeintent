from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from homeintent.entities import EntitySnapshot
from homeintent.history_query import (
    HistoryMetric,
    ComparativeHistoryQuery,
    StateHistoryMetric,
    async_execute_history_query,
    parse_history_query,
    render_history_result,
    render_state_history_result,
)


NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
SENSOR = EntitySnapshot(
    "sensor.wohnzimmer_temperatur", "Wohnzimmer Temperatur", "sensor", "22",
    unit="°C", device_class="temperature",
)


@pytest.mark.parametrize(
    ("text", "metric"),
    (
        ("Wie hoch war die Wohnzimmer Temperatur gestern im Durchschnitt?", HistoryMetric.MEAN),
        ("Was war das Maximum der Wohnzimmer Temperatur heute?", HistoryMetric.MAX),
        ("Welches Minimum hatte die Wohnzimmer Temperatur diese Woche?", HistoryMetric.MIN),
        ("Wie viel hat Wohnzimmer Energie letzte Woche verbraucht?", HistoryMetric.CHANGE),
    ),
)
def test_history_metric_and_period_are_composed(text, metric):
    entities = [SENSOR]
    if "Energie" in text:
        entities = [EntitySnapshot("sensor.energy", "Wohnzimmer Energie", "sensor", "10", unit="kWh")]
    query = parse_history_query(text, entities, NOW)
    assert query is not None
    assert query.metric is metric
    assert query.start < query.end


def test_history_query_requires_explicit_sensor_and_period():
    assert parse_history_query("Wie war der Durchschnitt gestern?", [SENSOR], NOW) is None
    assert parse_history_query("Wie ist die Wohnzimmer Temperatur?", [SENSOR], NOW) is None


def test_binary_state_history_count_and_duration_are_composed():
    window = EntitySnapshot(
        "binary_sensor.window", "Badezimmer Fenster", "binary_sensor", "off"
    )
    count = parse_history_query(
        "Wie oft war das Badezimmer Fenster gestern offen?", [window], NOW
    )
    duration = parse_history_query(
        "Wie lange war das Badezimmer Fenster heute geöffnet?", [window], NOW
    )

    assert count is not None and count.metric is StateHistoryMetric.COUNT
    assert duration is not None and duration.metric is StateHistoryMetric.DURATION


def test_binary_state_history_occurrence_and_last_time_are_composed():
    window = EntitySnapshot(
        "binary_sensor.window", "Badezimmer Fenster", "binary_sensor", "off"
    )
    occurred = parse_history_query(
        "War das Badezimmer Fenster gestern offen?", [window], NOW
    )
    last = parse_history_query(
        "Wann war das Badezimmer Fenster zuletzt offen?", [window], NOW
    )

    assert occurred is not None and occurred.metric is StateHistoryMetric.OCCURRED
    assert last is not None and last.metric is StateHistoryMetric.LAST
    assert last.period_label == "in den letzten sieben Tagen"


@pytest.mark.parametrize(
    ("text", "entity", "states", "label"),
    (
        (
            "Wie lange lief der Saugroboter Atlas gestern?",
            EntitySnapshot("vacuum.atlas", "Saugroboter Atlas", "vacuum", "docked"),
            frozenset({"cleaning", "returning"}),
            "aktiv",
        ),
        (
            "Wie oft spielte das Radio Atlas gestern?",
            EntitySnapshot("media_player.atlas", "Radio Atlas", "media_player", "off"),
            frozenset({"playing", "buffering"}),
            "bei der Wiedergabe",
        ),
        (
            "Wie lange war der Ventilator Büro heute an?",
            EntitySnapshot("fan.office", "Ventilator Büro", "fan", "off"),
            frozenset({"on"}),
            "aktiv",
        ),
    ),
)
def test_operational_state_history_is_composed(text, entity, states, label):
    query = parse_history_query(text, [entity], NOW)

    assert query is not None
    assert query.target_states == states
    assert query.target_label == label


def test_period_comparison_is_composed_without_fixed_word_order():
    query = parse_history_query(
        "War die Wohnzimmer Temperatur gestern niedriger als heute?", [SENSOR], NOW
    )

    assert isinstance(query, ComparativeHistoryQuery)
    assert query.first[3] == "heute"
    assert query.second[3] == "gestern"


def test_state_history_result_counts_transitions_and_sums_duration():
    window = EntitySnapshot(
        "binary_sensor.window", "Badezimmer Fenster", "binary_sensor", "off"
    )
    query = parse_history_query(
        "Wie oft war das Badezimmer Fenster gestern offen?", [window], NOW
    )
    assert query is not None
    rows = {
        window.entity_id: [
            {"state": "off"},
            {"state": "on"},
            {"state": "off"},
            {"state": "on"},
        ]
    }
    assert "2-mal offen" in render_state_history_result(query, rows)


def test_state_history_result_answers_occurrence_and_last_time():
    window = EntitySnapshot(
        "binary_sensor.window", "Badezimmer Fenster", "binary_sensor", "off"
    )
    occurred = parse_history_query(
        "War das Badezimmer Fenster gestern offen?", [window], NOW
    )
    last = parse_history_query(
        "Wann war das Badezimmer Fenster zuletzt offen?", [window], NOW
    )
    assert occurred is not None and last is not None
    rows = {
        window.entity_id: [
            {"state": "off", "last_changed": NOW - timedelta(hours=5)},
            {"state": "on", "last_changed": NOW - timedelta(hours=3)},
            {"state": "off", "last_changed": NOW - timedelta(hours=2)},
        ]
    }

    assert "Ja," in render_state_history_result(occurred, rows)
    assert "09:00 Uhr" in render_state_history_result(last, rows)


def test_history_result_is_aggregated_defensively():
    query = parse_history_query(
        "Wohnzimmer Temperatur gestern im Durchschnitt", [SENSOR], NOW
    )
    assert query is not None
    text = render_history_result(query, {
        SENSOR.entity_id: [{"mean": 20.0}, {"mean": 22.0}],
    })
    # Spoken German unit (voice output), not the raw HA symbol.
    assert "21 Grad" in text


def test_recorder_unavailable_degrades_cleanly(caplog):
    query = parse_history_query(
        "Wohnzimmer Temperatur gestern im Durchschnitt", [SENSOR], NOW
    )
    assert query is not None

    class Services:
        async def async_call(self, *args, **kwargs):
            raise RuntimeError("recorder disabled")

    hass = type("Hass", (), {"services": Services()})()
    assert "nicht verfügbar" in asyncio.run(async_execute_history_query(hass, query))
    assert "Recorder statistics query failed: recorder disabled" in caplog.text


def test_comparative_recorder_failure_is_logged(caplog):
    query = parse_history_query(
        "War die Wohnzimmer Temperatur gestern niedriger als heute?", [SENSOR], NOW
    )
    assert isinstance(query, ComparativeHistoryQuery)

    class Services:
        async def async_call(self, *args, **kwargs):
            raise RuntimeError("comparison unavailable")

    hass = type("Hass", (), {"services": Services()})()
    assert "nicht verfügbar" in asyncio.run(async_execute_history_query(hass, query))
    assert "Recorder comparison query failed: comparison unavailable" in caplog.text


def test_state_history_failure_is_logged(caplog):
    window = EntitySnapshot(
        "binary_sensor.window", "Badezimmer Fenster", "binary_sensor", "off"
    )
    query = parse_history_query(
        "Wie oft war das Badezimmer Fenster gestern offen?", [window], NOW
    )
    assert query is not None

    class Hass:
        async def async_add_executor_job(self, *args, **kwargs):
            raise RuntimeError("history unavailable")

    assert "nicht verfügbar" in asyncio.run(async_execute_history_query(Hass(), query))
    assert "Recorder state-history query failed" in caplog.text


# --- F3: real Home Assistant response format and sensor resolution ---------

HOUSE_SENSORS = [
    EntitySnapshot(
        "sensor.temperatur_wohnzimmer", "Temperatur Wohnzimmer", "sensor", "20.4",
        area_id="wohnzimmer", area_name="Wohnzimmer", unit="°C", device_class="temperature",
    ),
    EntitySnapshot(
        "sensor.temperatur_kueche", "Temperatur Küche", "sensor", "20.9",
        area_id="kueche", area_name="Küche", unit="°C", device_class="temperature",
    ),
    EntitySnapshot(
        "sensor.aussentemperatur", "Außentemperatur", "sensor", "12.3",
        area_id="garten", area_name="Garten", floor_name="Außenbereich",
        unit="°C", device_class="temperature",
    ),
    EntitySnapshot(
        "sensor.energiezaehler", "Energiezähler", "sensor", "18234.7",
        area_id="hwr", area_name="Hauswirtschaftsraum", unit="kWh",
        device_class="energy", state_class="total_increasing",
    ),
]


def test_render_accepts_real_recorder_response_format():
    query = parse_history_query(
        "Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?",
        HOUSE_SENSORS, NOW,
    )
    assert query is not None
    text = render_history_result(query, {
        "statistics": {"sensor.temperatur_wohnzimmer": [{"mean": 20.44}, {"mean": 21.0}]},
    })
    assert text == "Der Durchschnitt von Temperatur Wohnzimmer betrug gestern 20,7 Grad."


@pytest.mark.parametrize(
    ("text", "entity_id", "metric"),
    (
        ("Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?",
         "sensor.temperatur_wohnzimmer", HistoryMetric.MEAN),
        ("Wie hat sich der Energiezähler diese Woche verändert?",
         "sensor.energiezaehler", HistoryMetric.CHANGE),
        ("Wie kalt war es gestern draußen minimal?",
         "sensor.aussentemperatur", HistoryMetric.MIN),
        ("Wie warm war es gestern in der Küche maximal?",
         "sensor.temperatur_kueche", HistoryMetric.MAX),
        # Compound noun and "im Schnitt" (found in the 7.2.1 live re-test).
        ("Wie war die Durchschnittstemperatur gestern im Wohnzimmer?",
         "sensor.temperatur_wohnzimmer", HistoryMetric.MEAN),
        ("Wie warm war es gestern im Schnitt draußen?",
         "sensor.aussentemperatur", HistoryMetric.MEAN),
    ),
)
def test_sensor_is_resolved_by_area_and_measurement(text, entity_id, metric):
    query = parse_history_query(text, HOUSE_SENSORS, NOW)
    assert query is not None
    assert query.entity.entity_id == entity_id
    assert query.metric is metric


def test_area_measurement_without_unique_sensor_is_not_guessed():
    # Two temperature sensors, no area named: never pick one.
    assert parse_history_query(
        "Wie hoch war die durchschnittliche Temperatur gestern?", HOUSE_SENSORS, NOW
    ) is None


def test_yesterday_versus_day_before_comparison_reads_nested_format():
    query = parse_history_query(
        "War die Temperatur im Wohnzimmer gestern niedriger als vorgestern?",
        HOUSE_SENSORS, NOW,
    )
    assert isinstance(query, ComparativeHistoryQuery)
    assert query.first[3] == "gestern" and query.second[3] == "vorgestern"
    responses = iter((
        {"statistics": {"sensor.temperatur_wohnzimmer": [{"mean": 20.0}]}},
        {"statistics": {"sensor.temperatur_wohnzimmer": [{"mean": 21.5}]}},
    ))

    class Services:
        async def async_call(self, *args, **kwargs):
            return next(responses)

    class Hass:
        services = Services()

    text = asyncio.run(async_execute_history_query(Hass(), query))
    assert text == (
        "Temperatur Wohnzimmer lag gestern bei 20 Grad; "
        "das sind 1,5 Grad niedriger als vorgestern."
    )
