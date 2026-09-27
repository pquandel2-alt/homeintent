"""F3 against the real recorder: ``recorder.get_statistics`` answers
``{"statistics": {statistic_id: rows}}`` - HomeIntent 7.1.2 read the id at
the top level and reported "keine Statistikdaten" for every question.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.homeintent.entities import EntitySnapshot
from custom_components.homeintent.history_query import (
    async_execute_history_query,
    parse_history_query,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock: Any, enable_custom_integrations: Any):
    """The recorder database must be prepared before the ``hass`` fixture."""
    yield


async def test_f3_statistics_are_read_from_the_real_recorder(
    recorder_mock: Any, hass: HomeAssistant
) -> None:
    from homeassistant.components.recorder.models import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_import_statistics
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    now = dt_util.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = dt_util.as_utc(midnight - timedelta(days=1))
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": "sensor.temperatur_wohnzimmer",
            "unit_class": "temperature",
            "unit_of_measurement": "°C",
        },
        [
            {"start": start + timedelta(hours=hour), "mean": 20.0 + hour % 2, "min": 19.0, "max": 22.0}
            for hour in range(24)
        ],
    )
    await async_wait_recording_done(hass)
    sensor = EntitySnapshot(
        "sensor.temperatur_wohnzimmer", "Temperatur Wohnzimmer", "sensor", "20.4",
        area_id="wohnzimmer", area_name="Wohnzimmer", unit="°C", device_class="temperature",
    )
    query = parse_history_query(
        "Wie hoch war die durchschnittliche Temperatur im Wohnzimmer gestern?", [sensor], now
    )
    assert query is not None

    answer = await async_execute_history_query(hass, query)

    assert answer == "Der Durchschnitt von Temperatur Wohnzimmer betrug gestern 20,5 Grad."
