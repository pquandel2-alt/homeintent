"""7.9.9: timestamp rendering follows HA's zone, never the host zone."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import _ha_stub

_ha_stub.install()

from homeassistant.util import dt as dt_util
from homeintent.household_query import _format_datetime


@pytest.mark.parametrize(
    ("zone", "instant", "expected"),
    [
        (timezone.utc, "2026-09-06T15:54:22+00:00", "15:54 Uhr"),
        (ZoneInfo("Europe/Berlin"), "2026-09-06T15:54:22+00:00", "17:54 Uhr"),
        (ZoneInfo("Europe/Berlin"), "2026-01-06T15:54:22+00:00", "16:54 Uhr"),
    ],
)
def test_completion_timestamp_uses_configured_ha_zone(
    monkeypatch, zone, instant, expected
):
    monkeypatch.setattr(_ha_stub, "DEFAULT_TIME_ZONE", zone)
    now = datetime(2026, 10, 15, 12, tzinfo=zone)

    assert expected in (_format_datetime(instant, now) or "")
    assert dt_util.as_local(datetime.fromisoformat(instant)).tzinfo == zone


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc), "01:30 Uhr"),
        (datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc), "03:30 Uhr"),
        (datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc), "02:30 Uhr"),
        (datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), "02:30 Uhr"),
    ],
)
def test_berlin_dst_transition_is_rendered_by_instant(monkeypatch, instant, expected):
    berlin = ZoneInfo("Europe/Berlin")
    monkeypatch.setattr(_ha_stub, "DEFAULT_TIME_ZONE", berlin)
    now = datetime(2026, 10, 15, 12, tzinfo=berlin)

    assert expected in (_format_datetime(instant, now) or "")
