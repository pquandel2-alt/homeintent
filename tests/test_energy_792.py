"""7.9.2 B5: consumption over a period - energy (kWh), never power.

Synthetic recorder histories: an energy meter (increases, with a reset), a
power sensor (integrated, "geschätzt aus der Leistung"), the ranking of
the three largest consumers, costs only with a configured price, "gerade"
stays power in W, no recorder -> honest answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()


def _house(monkeypatch, tmp_path, samples, options=None):
    import homeintent.controllers.insights as insights

    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options={**PUSH_OPTIONS, **(options or {})})

    def reader(hass):
        async def read(entity_id, start, end):
            if samples is None:
                return None
            rows = samples.get(entity_id, [])
            return [(max(moment, start), value) for moment, value in rows if moment <= end]
        return read

    monkeypatch.setattr(insights, "samples_reader", reader)
    return house


def _today(hour: int, minute: int = 0) -> datetime:
    from homeassistant.util import dt as dt_util

    return dt_util.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def _power(start_hour: int, watts: float, hours: float) -> list[tuple[datetime, float]]:
    """A device drawing ``watts`` for ``hours`` from ``start_hour`` today."""
    begin = _today(0)
    on = _today(start_hour)
    return [(begin, 0.0), (on, watts), (on + timedelta(hours=hours), 0.0)]


@pytest.fixture
def frozen_now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 23, 0, tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


@pytest.mark.parametrize("question", [
    "Wie viel Strom hat die Waschmaschine heute verbraucht?",
    "Wieviel Energie hat die Waschmaschine heute gebraucht?",
    "Wie viel Strom hat heute die Waschmaschine verbraucht?",
])
def test_device_from_power_is_estimated(monkeypatch, tmp_path, frozen_now, question):
    house = _house(monkeypatch, tmp_path, {"sensor.leistung_waschmaschine": _power(8, 2000, 1.5)})
    speech = house.say(question).speech
    assert speech == "Die Waschmaschine hat heute etwa 3 kWh verbraucht (geschätzt aus der Leistung).", speech


@pytest.mark.parametrize(("question", "label"), [
    ("Wie viel Energie haben wir heute verbraucht?", "Heute"),
    ("Wie viel Strom hat das Haus gestern verbraucht?", "Gestern"),
    ("Wie viel Strom haben wir diese Woche verbraucht?", "Diese Woche"),
])
def test_house_meter_with_reset(monkeypatch, tmp_path, frozen_now, question, label):
    start = frozen_now - timedelta(days=8)
    rows = [(start + timedelta(hours=index), 100.0 + index * 0.5) for index in range(24 * 9)]
    house = _house(monkeypatch, tmp_path, {"sensor.energiezaehler": rows})
    speech = house.say(question).speech
    assert speech.startswith(f"{label} wurden ") and "kWh verbraucht (Energiezähler)" in speech, speech
    assert "geschätzt" not in speech


def test_meter_reset_is_no_negative_consumption():
    from homeintent.energy_query import consumption_kwh
    from _testhaus import house_entities

    meter = next(e for e in house_entities() if e.entity_id == "sensor.energiezaehler")
    t = datetime(2026, 10, 6, 0, 0)
    samples = [(t, 10.0), (t + timedelta(hours=1), 12.0), (t + timedelta(hours=2), 0.5), (t + timedelta(hours=3), 1.5)]
    assert consumption_kwh(meter, samples, t + timedelta(hours=4)) == pytest.approx(2.0 + 0.5 + 1.0)


@pytest.mark.parametrize("question", [
    "Was hat heute am meisten verbraucht?", "Was hat heute am meisten Strom verbraucht?",
    "Welches Gerät hat heute am meisten verbraucht?",
])
def test_ranking_in_kwh(monkeypatch, tmp_path, frozen_now, question):
    samples = {
        "sensor.leistung_waschmaschine": _power(8, 2000, 1.5),
        "sensor.leistung_trockner": _power(10, 2500, 2.0),
        "sensor.leistung_kaffeemaschine": _power(7, 1000, 0.25),
        "sensor.stromverbrauch_haus": _power(0, 400, 20),
    }
    speech = _house(monkeypatch, tmp_path, samples).say(question).speech
    assert speech.startswith("Heute am meisten verbraucht: Trockner 5 kWh, Waschmaschine 3 kWh und Kaffeemaschine"), speech
    assert " W" not in speech.replace("Waschmaschine", "") and "Haus" not in speech


def test_costs_only_with_a_price(monkeypatch, tmp_path, frozen_now):
    samples = {"sensor.leistung_waschmaschine": _power(8, 2000, 1.5)}
    question = "Wie viel Strom hat die Waschmaschine heute gekostet?"
    without = _house(monkeypatch, tmp_path, samples).say(question).speech
    assert "€" not in without, without
    priced = _house(monkeypatch, tmp_path / "p", samples, {"energy_price": 0.32}).say(question).speech
    assert "Das sind etwa 0,96 €." in priced, priced


@pytest.mark.parametrize("question", [
    "Wie viel verbraucht die Waschmaschine gerade?", "Wie viel Strom zieht die Waschmaschine gerade?",
    "Wie viel verbraucht die Waschmaschine aktuell?",
])
def test_now_stays_power(monkeypatch, tmp_path, question):
    speech = _house(monkeypatch, tmp_path, {}).say(question).speech
    assert speech == "Die Waschmaschine verbraucht gerade 1840 Watt.", speech


def test_without_recorder_is_honest(monkeypatch, tmp_path, frozen_now):
    speech = _house(monkeypatch, tmp_path, None).say("Wie viel Strom hat die Waschmaschine heute verbraucht?").speech
    assert "Recorder" in speech and "rate ich nicht" in speech


def test_without_samples_is_honest(monkeypatch, tmp_path, frozen_now):
    speech = _house(monkeypatch, tmp_path, {}).say("Wie viel Strom hat die Waschmaschine heute verbraucht?").speech
    assert "keine Verlaufsdaten" in speech, speech


@pytest.mark.parametrize("text", [
    "Sag mir Bescheid, wenn der Stromverbrauch heute über 10 kWh liegt.",
    "Wenn die Waschmaschine heute mehr als 2 kWh verbraucht hat, sag mir Bescheid.",
])
def test_monitoring_requests_are_no_questions(text):
    from homeintent.energy_query import parse_energy_query

    assert parse_energy_query(text, datetime(2026, 10, 6, 12, 0)) is None
