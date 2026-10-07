"""7.9.3 B1: weather questions and rain in automations.

* Questions from ``weather.*`` and ``weather.get_forecasts`` (daily or
  hourly depending on the question), generated combinatorially: "Wie wird
  das Wetter morgen?", "Wie warm wird es heute?", "Regnet es heute noch?",
  "Brauche ich einen Schirm?", "Wird es am Wochenende sonnig?", "Wie viel
  Wind ist morgen?".  Probabilities and amounts only when delivered.
  Without a weather entity: honest.  Several: a question.
* "Wenn Regen angesagt ist, fahr die Markise ein": a ``time_pattern``
  trigger, the forecast fetched in the automation (``response_variable``),
  a closed template; the HA evaluation model runs it (closes on rain, does
  nothing without rain, nothing when already closed).
* "Wenn es regnet, …" uses the rain sensor.
* Read-only questions need no "Ja"; automations go through preview and "Ja".
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

import _ha_stub
from _ha_sim import World, fires, run
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_ha_stub.install()

NOW = datetime(2026, 10, 7, 9, 0)  # a Wednesday


def _asks(preview: str) -> bool:
    """A device automation ends with the generic preview question."""
    return preview.endswith(("Soll ich das so einrichten?", "Soll diese Automation erstellt werden?"))


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = NOW.replace(tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def daily(now, days=None):
    days = days or {}
    base = [
        {"condition": "partlycloudy", "temperature": 17.0, "templow": 9.0, "precipitation_probability": 10,
         "precipitation": 0.0, "wind_speed": 12.0},
        {"condition": "rainy", "temperature": 14.0, "templow": 8.0, "precipitation_probability": 80,
         "precipitation": 6.2, "wind_speed": 28.0},
        {"condition": "sunny", "temperature": 19.0, "templow": 10.0, "precipitation_probability": 5,
         "precipitation": 0.0, "wind_speed": 9.0},
        {"condition": "cloudy", "temperature": 16.0, "templow": 9.0, "precipitation_probability": 30,
         "precipitation": 0.4, "wind_speed": 15.0},
    ]
    start = now.replace(hour=12, minute=0)
    out = []
    for offset in range(7):
        item = dict(base[offset % 4], **days.get(offset, {}))
        item["datetime"] = (start + timedelta(days=offset)).isoformat()
        out.append(item)
    return out


def hourly(now, rain_from=None, probability=True):
    start = now.replace(minute=0)
    out = []
    for offset in range(24):
        rainy = rain_from is not None and offset >= rain_from
        item = {"datetime": (start + timedelta(hours=offset)).isoformat(),
                "condition": "rainy" if rainy else "cloudy", "temperature": 12.0 + offset % 3,
                "precipitation": 1.2 if rainy else 0.0}
        if probability:
            item["precipitation_probability"] = 80 if rainy else 10
        out.append(item)
    return out


def _house(monkeypatch, tmp_path, now, *, rain_from=None, days=None, probability=True, entities=None,
           fail=False):
    import homeintent.controllers.insights as insights

    calls = []

    async def forecast(self, entity_id, kind):
        calls.append((entity_id, kind))
        if fail:
            return None
        return daily(now, days) if kind == "daily" else hourly(now, rain_from, probability)

    monkeypatch.setattr(insights.InsightsController, "_async_forecast", forecast)
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    house.forecast_calls = calls
    return house


# --- questions -----------------------------------------------------------------

_TOMORROW = ("Wie wird das Wetter morgen?", "Wie ist das Wetter morgen?", "Was sagt die Wettervorhersage für morgen?",
             "Wie wird morgen das Wetter?", "Wird es morgen regnen?", "Regnet es morgen?")


@pytest.mark.parametrize("text", _TOMORROW)
def test_tomorrow_reads_the_daily_forecast(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path, now)
    turn = house.say(text)
    assert house.forecast_calls == [("weather.zuhause", "daily")], turn.speech
    assert turn.speech.startswith("Morgen"), turn.speech
    assert "Regen" in turn.speech and "Wahrscheinlichkeit 80 %" in turn.speech and "6,2 mm" in turn.speech
    assert "(laut „Wettervorhersage“)" in turn.speech and turn.calls == []


_WARM = ("Wie warm wird es heute?", "Wie warm wird es morgen?", "Wie kalt wird es übermorgen?",
         "Wie viel Grad werden es am Freitag?", "Welche Temperatur wird es morgen?")


@pytest.mark.parametrize("text", _WARM)
def test_temperature_questions(monkeypatch, tmp_path, now, text):
    speech = _house(monkeypatch, tmp_path, now).say(text).speech
    assert "warm" in speech and "Grad" in speech, speech
    expected = {"übermorgen": "Übermorgen wird es bis zu 19 Grad", "heute": "Heute wird es bis zu 17 Grad",
                "morgen": "Morgen wird es bis zu 14 Grad",
                "Freitag": "Übermorgen wird es bis zu 19 Grad"}  # Wednesday: Friday is the day after tomorrow
    key = next(word for word in expected if word in text)
    assert speech.startswith(expected[key]), speech


@pytest.mark.parametrize("text", ["Regnet es heute noch?", "Brauche ich einen Schirm?", "Brauche ich heute einen Regenschirm?",
                                  "Kommt heute noch Regen?", "Gibt es heute noch Regen?", "Regnet es heute Abend?"])
@pytest.mark.parametrize("rain_from", [None, 5])
def test_rain_rest_of_today_reads_the_hourly_forecast(monkeypatch, tmp_path, now, text, rain_from):
    house = _house(monkeypatch, tmp_path, now, rain_from=rain_from)
    speech = house.say(text).speech
    assert house.forecast_calls == [("weather.zuhause", "hourly")], speech
    if rain_from is None:
        assert speech.startswith("Nein, heute ist laut Vorhersage kein Regen mehr angesagt"), speech
    else:
        assert speech.startswith("Ja, ab etwa 14 Uhr ist Regen angesagt (Wahrscheinlichkeit 80 %, 1,2 mm)"), speech


def test_probability_only_when_delivered(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path, now, rain_from=2, probability=False).say("Regnet es heute noch?").speech
    assert speech.startswith("Ja, ab etwa 11 Uhr ist Regen angesagt (1,2 mm)"), speech
    assert "Wahrscheinlichkeit" not in speech


@pytest.mark.parametrize("text", ["Wird es am Wochenende sonnig?", "Scheint am Wochenende die Sonne?",
                                  "Wie wird das Wetter am Wochenende?"])
def test_weekend(monkeypatch, tmp_path, now, text):
    # Wednesday: Saturday is offset 3 (cloudy), Sunday offset 4 (partlycloudy).
    speech = _house(monkeypatch, tmp_path, now).say(text).speech
    assert "am Samstag" in speech.casefold() or "Am Samstag" in speech, speech
    assert "am Sonntag" in speech, speech


@pytest.mark.parametrize("text", ["Wie viel Wind ist morgen?", "Wie windig wird es morgen?", "Wird es morgen stürmisch?"])
def test_wind(monkeypatch, tmp_path, now, text):
    speech = _house(monkeypatch, tmp_path, now).say(text).speech
    assert speech.startswith("Morgen weht der Wind mit bis zu 28 km/h"), speech


def test_now_uses_the_state(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now)
    speech = house.say("Wie ist das Wetter gerade?").speech
    assert speech.startswith("Gerade ist es teils bewölkt"), speech
    assert house.forecast_calls == []


def test_forecast_missing_is_honest(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path, now, fail=True).say("Wie wird das Wetter morgen?").speech
    assert "kann ich gerade nicht lesen" in speech and "rate ich nicht" in speech


def test_without_weather_entity_is_honest(monkeypatch, tmp_path, now):
    entities = [entity for entity in house_entities() if entity.domain != "weather"]
    speech = _house(monkeypatch, tmp_path, now, entities=entities).say("Wie wird das Wetter morgen?").speech
    assert speech.startswith("Ich habe keine Wettervorhersage in Home Assistant")


def test_several_weather_entities_ask(monkeypatch, tmp_path, now):
    entities = house_entities()
    weather = next(entity for entity in entities if entity.domain == "weather")
    second = replace(weather, entity_id="weather.ferienhaus", friendly_name="Wetter Ferienhaus")
    house = _house(monkeypatch, tmp_path, now, entities=[*entities, second])
    question = house.say("Wie wird das Wetter morgen?").speech
    assert question == "Welche Wettervorhersage meinst du: „Wetter Ferienhaus“ oder „Wettervorhersage“?"
    answer = house.say("Wetter Ferienhaus").speech
    assert answer.startswith("Morgen") and "(laut „Wetter Ferienhaus“)" in answer, answer
    assert house.forecast_calls == [("weather.ferienhaus", "daily")]


@pytest.mark.parametrize("text", [
    "Wie warm ist es im Wohnzimmer?", "Wie warm wird es im Büro?", "Wie warm ist es draußen?",
    "Schalte das Licht an, wenn es regnet.", "Ist das Fenster im Bad offen?",
])
def test_no_weather_question(text):
    from homeintent.weather import parse_weather_query

    query = parse_weather_query(text, NOW)
    assert query is None or "im " in text, text


# --- rain in automations ------------------------------------------------------------

_AWNING = ("Wenn Regen angesagt ist, fahr die Markise ein.", "Wenn Regen vorhergesagt ist, fahre die Markise ein.",
           "Wenn es regnen soll, fahr die Markise ein.", "Wenn Regen gemeldet wird, fahr die Markise ein.")


@pytest.mark.parametrize("text", _AWNING)
def test_awning_on_rain_forecast_preview_and_config(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path, now)
    preview = house.say(text).speech
    assert "Wenn laut Wettervorhersage „Wettervorhersage“ Regen angesagt ist" in preview, preview
    assert "Markise" in preview and "alle 30 Minuten" in preview and _asks(preview)
    assert house.automations() == []
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"] == [{"trigger": "time_pattern", "minutes": "/30"}]
    fetch, check, not_done, close = automation["actions"]
    assert fetch == {"action": "weather.get_forecasts", "target": {"entity_id": "weather.zuhause"},
                     "data": {"type": "hourly"}, "response_variable": "homeintent_vorhersage"}
    assert check["condition"] == "template" and not_done["condition"] == "template"
    assert close["action"] == "cover.close_cover" and close["target"]["entity_id"] in ("cover.markise", ["cover.markise"])


def _run_with_forecast(automation, states, forecast):
    world = World(dict(states), clock=(9, 30))
    world.forecasts = {("weather.zuhause", "hourly"): forecast}
    assert fires(automation, world)
    return run(automation["actions"], world), world


def test_ha_evaluation_of_the_awning(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now)
    house.say(_AWNING[0])
    house.say("Ja.")
    [automation] = house.automations()
    calls_rain = _run_with_forecast(automation, {"cover.markise": "open"}, hourly(now, rain_from=3))[1].service_calls
    assert ("cover", "close_cover", ("cover.markise",)) in calls_rain
    calls_dry = _run_with_forecast(automation, {"cover.markise": "open"}, hourly(now))[1].service_calls
    assert calls_dry == []
    calls_late = _run_with_forecast(automation, {"cover.markise": "open"}, hourly(now, rain_from=8))[1].service_calls
    assert calls_late == []  # beyond the next 6 hours
    calls_closed = _run_with_forecast(automation, {"cover.markise": "closed"}, hourly(now, rain_from=1))[1].service_calls
    assert calls_closed == []  # already done


@pytest.mark.parametrize("text", ["Wenn es regnet, fahr die Markise ein.", "Bei Regen fahr die Markise ein.",
                                  "Wenn es anfängt zu regnen, fahr die Markise ein."])
def test_raining_uses_the_rain_sensor(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path, now)
    preview = house.say(text).speech
    assert "Regensensor" in preview and _asks(preview), preview
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == "binary_sensor.regensensor" and trigger["to"] in ("on", ["on"])
    assert fires(automation, World({"binary_sensor.regensensor": "on", "cover.markise": "open"}),
                 "binary_sensor.regensensor", "off")


def test_rain_forecast_message_asks_for_the_time(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, now)
    question = house.say("Wenn Regen angesagt ist, sag mir Bescheid.").speech
    assert question.startswith("Um wie viel Uhr soll ich in der Vorhersage nachsehen?")
    preview = house.say("Um 7 Uhr.").speech
    assert "07:00" in preview and "heute Regen angesagt" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["trigger"] == "time" and automation["triggers"][0]["at"].startswith("07:00")
    assert automation["actions"][0]["data"] == {"type": "daily"}
    world = World({}, clock=(7, 0))
    world.forecasts = {("weather.zuhause", "daily"): daily(now)}
    assert run(automation["actions"], world) == []  # today partly cloudy: no message
    world.forecasts = {("weather.zuhause", "daily"): daily(now, {0: {"condition": "rainy"}})}
    assert len(run(automation["actions"], world)) == 1


def test_without_weather_entity_the_rule_is_honest_and_offered_without(monkeypatch, tmp_path, now):
    entities = [entity for entity in house_entities() if entity.domain != "weather"]
    house = _house(monkeypatch, tmp_path, now, entities=entities)
    speech = house.say("Wenn Regen angesagt ist, fahr die Markise ein.").speech
    assert speech.startswith("Dafür habe ich keine Wettervorhersage (weather-Entität).")
    assert "ohne diese Bedingung einrichten?" in speech
    assert house.say("Nein.").speech == "In Ordnung, ich richte nichts ein."
    assert house.automations() == []


@pytest.mark.parametrize("clause,kinds,role", [
    ("Wenn Regen angesagt ist, fahr die Markise ein.", ("rain_soon",), "trigger"),
    ("Wenn es regnet, schließ das Dachfenster.", ("raining",), "trigger"),
    ("Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es heute nicht regnet.", ("no_rain_today",), "guard"),
    ("Bewässere um 6 Uhr, nur wenn es nicht geregnet hat.", ("no_rain_recent",), "guard"),
    ("Bewässere um 6 Uhr, aber nur wenn es nicht geregnet hat bzw. nicht regnen soll.",
     ("no_rain_recent", "no_rain_today"), "guard"),
    ("Bewässere den Garten 20 Minuten, falls es heute nicht regnet.", ("no_rain_today",), "guard"),
])
def test_clause_constructions(clause, kinds, role):
    from homeintent.weather import parse_weather_clause

    found = parse_weather_clause(clause)
    assert found is not None and found.kinds == kinds and found.role == role, found
    assert "regn" not in found.rest.casefold() and "Regen" not in found.rest


@pytest.mark.parametrize("text", ["Wie wird das Wetter?", "Wenn die Tür aufgeht, sag mir Bescheid.",
                                  "Mach das Licht an.", "Wenn es draußen heller als 30000 Lux ist, öffne die Markise."])
def test_no_rain_clause(text):
    from homeintent.weather import parse_weather_clause

    assert parse_weather_clause(text) is None


@pytest.mark.parametrize("verb,when", list(itertools.product(
    ("fahr die Markise ein", "fahre die Markise ein", "schließe die Markise"),
    ("Wenn Regen angesagt ist", "Sobald Regen angesagt ist", "Falls Regen vorhergesagt wird"),
)))
def test_trigger_paraphrases(monkeypatch, tmp_path, now, verb, when):
    house = _house(monkeypatch, tmp_path, now)
    preview = house.say(f"{when}, {verb}.").speech
    assert "Regen angesagt" in preview and "Markise" in preview and _asks(preview), preview
