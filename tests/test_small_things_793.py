"""7.9.3 A6: small things from the follow-up test.

* "Was hat heute am meisten verbraucht?" names only devices that consumed
  something (> 0 kWh); when nothing did, it says so.
* Without the recorder: one single-line warning per cause and hour instead
  of a traceback per question; the answer stays honest.
* The push on arrival with a summary is covered in ``test_house_report_793``.
"""

from __future__ import annotations

import logging

import pytest

import _ha_stub
from test_energy_792 import _house, _power  # noqa: F401 - the fixture helpers of 7.9.2

_ha_stub.install()


@pytest.fixture
def frozen_now(monkeypatch):
    from datetime import datetime

    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 23, 0, tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


_RANKING = ("Was hat heute am meisten verbraucht?", "Welches Gerät hat heute am meisten Strom verbraucht?",
            "Was hat gestern am meisten verbraucht?")


@pytest.mark.parametrize("question", _RANKING[:2])
def test_ranking_leaves_out_zero(monkeypatch, tmp_path, frozen_now, question):
    """Live-Befund: "Kaffeemaschine 0 kWh und Trockner 0 kWh"."""
    samples = {
        "sensor.leistung_waschmaschine": _power(8, 2000, 1.5),
        "sensor.leistung_trockner": _power(10, 0, 2.0),
        "sensor.leistung_kaffeemaschine": _power(7, 0, 0.25),
    }
    speech = _house(monkeypatch, tmp_path, samples).say(question).speech
    assert speech.startswith("Heute am meisten verbraucht: Waschmaschine 3 kWh"), speech
    assert "Trockner" not in speech and "Kaffeemaschine" not in speech and "0 kWh" not in speech


@pytest.mark.parametrize("question", _RANKING[:2])
def test_ranking_when_nothing_consumed(monkeypatch, tmp_path, frozen_now, question):
    samples = {entity: _power(8, 0, 1) for entity in (
        "sensor.leistung_waschmaschine", "sensor.leistung_trockner", "sensor.leistung_kaffeemaschine")}
    speech = _house(monkeypatch, tmp_path, samples).say(question).speech
    assert speech == "Heute hat keines der 3 Geräte messbar Strom verbraucht.", speech


def test_ranking_with_two_consumers_names_two(monkeypatch, tmp_path, frozen_now):
    samples = {
        "sensor.leistung_waschmaschine": _power(8, 2000, 1.5),
        "sensor.leistung_trockner": _power(10, 2500, 2.0),
        "sensor.leistung_kaffeemaschine": _power(7, 0, 0.25),
    }
    speech = _house(monkeypatch, tmp_path, samples).say(_RANKING[0]).speech
    assert speech.startswith("Heute am meisten verbraucht: Trockner 5 kWh und Waschmaschine 3 kWh"), speech


def _failing_recorder(monkeypatch):
    """A Home Assistant whose recorder history raises (e.g. disabled)."""
    import sys
    import types

    history = types.ModuleType("homeassistant.components.recorder.history")

    def broken(*args, **kwargs):
        raise RuntimeError("Recorder ist deaktiviert")

    history.get_significant_states = broken
    recorder = types.ModuleType("homeassistant.components.recorder")
    recorder.history = history
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", recorder)
    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder.history", history)


def test_without_recorder_one_line_per_cause(monkeypatch, caplog):
    import asyncio
    from datetime import datetime

    import homeintent.history_query as history_query
    from homeassistant.core import HomeAssistant

    _failing_recorder(monkeypatch)
    monkeypatch.setattr(history_query, "_RECORDER_WARNED", {})
    hass = HomeAssistant()
    caplog.set_level(logging.DEBUG, logger="homeintent.history_query")
    start, end = datetime(2026, 10, 6, 0, 0), datetime(2026, 10, 6, 12, 0)
    for _ in range(5):
        assert asyncio.run(history_query.async_read_state_rows(hass, ["binary_sensor.haustuer"], start, end)) is None
        asyncio.run(history_query.async_read_numeric_samples(hass, "sensor.energiezaehler", start, end))
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2, [record.getMessage() for record in warnings]  # one per cause (kind of read)
    for record in warnings:
        assert record.exc_info is None and "\n" not in record.getMessage()
        assert "Recorder ist deaktiviert" in record.getMessage()
    assert all(record.exc_info is None for record in caplog.records)


def test_the_warning_returns_after_an_hour(monkeypatch, caplog):
    import homeintent.history_query as history_query

    clock = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(history_query, "_RECORDER_WARNED", {})
    caplog.set_level(logging.WARNING, logger="homeintent.history_query")
    history_query.warn_recorder("state rows", RuntimeError("weg"), None)
    history_query.warn_recorder("state rows", RuntimeError("weg"), None)
    clock[0] += history_query.RECORDER_WARNING_INTERVAL + 1
    history_query.warn_recorder("state rows", RuntimeError("weg"), None)
    assert len(caplog.records) == 2


def test_recorder_not_loaded_is_honest_without_calling_it(monkeypatch, caplog):
    import asyncio
    import types
    from datetime import datetime

    import homeintent.history_query as history_query

    monkeypatch.setattr(history_query, "_RECORDER_WARNED", {})
    hass = types.SimpleNamespace(config=types.SimpleNamespace(components={"homeintent"}))
    caplog.set_level(logging.WARNING, logger="homeintent.history_query")
    result = asyncio.run(history_query.async_read_state_rows(
        hass, ["binary_sensor.haustuer"], datetime(2026, 10, 6), datetime(2026, 10, 7)
    ))
    assert result is None and len(caplog.records) == 1
    assert "nicht geladen" in caplog.records[0].getMessage()


def test_summary_answer_without_recorder_stays_honest(monkeypatch, tmp_path, frozen_now):
    from test_event_summary_792 import _house as summary_house

    speech = summary_house(monkeypatch, tmp_path, frozen_now, recorder=False).say("Was hab ich verpasst?").speech
    assert "Recorder" in speech and "nicht verfügbar" in speech
