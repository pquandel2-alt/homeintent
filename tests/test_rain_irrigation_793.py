"""7.9.3 B4: irrigation that depends on rain.

"Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht
geregnet hat bzw. nicht regnen soll."

* The condition comes from the rain sensor (last 24 hours) and/or the
  forecast for today; the preview names every source.
* Without any source: an honest answer and the offer without the
  condition.
* An immediate command with a rain condition is checked right now.
* The HA evaluation model runs the generated automation: no watering after
  rain or with rain announced, watering (open → 20 min → close) otherwise.

Phrasings are generated combinatorially.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import pytest

import _ha_stub
from _ha_sim import World, run
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities, with_states
from test_weather_793 import daily

_ha_stub.install()

NOW = datetime(2026, 10, 7, 9, 0)


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = NOW.replace(tzinfo=dt_util.now().tzinfo)
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _house(monkeypatch, tmp_path, now, entities=None, days=None, rain_sensor_changed=None):
    import homeintent.controllers.insights as insights

    async def forecast(self, entity_id, kind):
        return daily(now, days) if kind == "daily" else []

    monkeypatch.setattr(insights.InsightsController, "_async_forecast", forecast)
    house = HouseConversation(monkeypatch, entities=entities, tmp_path=tmp_path, options=PUSH_OPTIONS)
    if rain_sensor_changed is not None:
        from homeassistant.core import State
        from homeassistant.util import dt as dt_util

        state = State("binary_sensor.regensensor", "off", {})
        state.last_changed = dt_util.utcnow() - timedelta(hours=rain_sensor_changed)
        house.entity.hass.states._states["binary_sensor.regensensor"] = state
    return house


_ACTIONS = ("Bewässere jeden Morgen um 6 Uhr 20 Minuten", "Bewässere den Garten jeden Morgen um 6 Uhr für 20 Minuten",
            "Jeden Morgen um 6 Uhr bewässere den Garten 20 Minuten")
_GUARDS = {
    "aber nur wenn es nicht geregnet hat bzw. nicht regnen soll": ("no_rain_recent", "no_rain_today"),
    "aber nur wenn es nicht geregnet hat oder regnen soll": ("no_rain_recent", "no_rain_today"),
    "nur wenn es heute nicht regnet": ("no_rain_today",),
    "falls es heute nicht regnen soll": ("no_rain_today",),
    "aber nur wenn es nicht geregnet hat": ("no_rain_recent",),
    "sofern kein Regen angesagt ist": ("no_rain_today",),
}


@pytest.mark.parametrize("action,guard", list(itertools.product(_ACTIONS, _GUARDS)))
def test_preview_names_the_sources(monkeypatch, tmp_path, now, action, guard):
    house = _house(monkeypatch, tmp_path, now)
    preview = house.say(f"{action}, {guard}.").speech
    kinds = _GUARDS[guard]
    assert "Bewässerung Garten öffnen und nach 20 Minuten wieder schließen" in preview, preview
    assert "Das passiert nur, wenn" in preview, preview
    if "no_rain_recent" in kinds:
        assert "„Regensensor“ in den letzten 24 Stunden keinen Regen gemeldet hat" in preview
    if "no_rain_today" in kinds:
        assert "laut Wettervorhersage „Wettervorhersage“ heute kein Regen angesagt ist" in preview
    assert house.automations() == []


def _automation(monkeypatch, tmp_path, now, text):
    house = _house(monkeypatch, tmp_path, now)
    house.say(text)
    house.say("Ja.")
    [automation] = house.automations()
    return automation


_FULL = "Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht geregnet hat bzw. nicht regnen soll."


@pytest.mark.parametrize("sensor,hours_ago,today,waters", [
    ("off", 30, "partlycloudy", True),   # dry, nothing announced
    ("off", 10, "partlycloudy", False),  # it rained 10 hours ago
    ("on", 0, "partlycloudy", False),    # it is raining
    ("off", 30, "rainy", False),         # rain announced
    ("off", 30, {"condition": "cloudy", "precipitation_probability": 70}, False),
    ("off", 30, {"condition": "cloudy", "precipitation_probability": 20}, True),
])
def test_ha_evaluation(monkeypatch, tmp_path, now, sensor, hours_ago, today, waters):
    automation = _automation(monkeypatch, tmp_path, now, _FULL)
    assert automation["triggers"] == [{"trigger": "time", "at": "06:00:00"}]
    assert automation["alias"] == _FULL
    world = World({"binary_sensor.regensensor": sensor, "valve.bewaesserung": "closed"}, clock=(6, 0))
    world.changed_seconds_ago = {"binary_sensor.regensensor": hours_ago * 3600}
    override = {"condition": today} if isinstance(today, str) else today
    world.forecasts = {("weather.zuhause", "daily"): daily(now, {0: override})}
    run(automation["actions"], world)
    expected = [("valve", "open_valve", ("valve.bewaesserung",)), ("valve", "close_valve", ("valve.bewaesserung",))]
    assert world.service_calls == (expected if waters else []), world.service_calls


def test_rain_amount_sensor_when_no_rain_sensor(monkeypatch, tmp_path, now):
    entities = [entity for entity in house_entities() if entity.entity_id != "binary_sensor.regensensor"]
    house = _house(monkeypatch, tmp_path, now, entities=entities)
    preview = house.say("Bewässere jeden Morgen um 6 Uhr 20 Minuten, aber nur wenn es nicht geregnet hat.").speech
    assert "„Regenmenge heute“ in den letzten 24 Stunden keinen Regen gemeldet hat" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    for amount, ago, waters in (("0.0", 3600 * 30, True), ("3.4", 3600 * 5, False), ("3.4", 3600 * 30, True)):
        world = World({"sensor.regenmenge": amount, "valve.bewaesserung": "closed"}, clock=(6, 0))
        world.changed_seconds_ago = {"sensor.regenmenge": ago}
        run(automation["actions"], world)
        assert bool(world.service_calls) is waters, (amount, ago)


def test_only_the_forecast_is_used_when_no_sensor(monkeypatch, tmp_path, now):
    entities = [entity for entity in house_entities()
                if entity.entity_id not in {"binary_sensor.regensensor", "sensor.regenmenge"}]
    house = _house(monkeypatch, tmp_path, now, entities=entities)
    preview = house.say(_FULL).speech
    assert "heute kein Regen angesagt" in preview and "letzten 24 Stunden" not in preview, preview


def test_without_any_source_honest_and_offer_without(monkeypatch, tmp_path, now):
    entities = [entity for entity in house_entities()
                if entity.domain != "weather" and entity.entity_id not in {"binary_sensor.regensensor",
                                                                           "sensor.regenmenge"}]
    house = _house(monkeypatch, tmp_path, now, entities=entities)
    speech = house.say(_FULL).speech
    assert speech.startswith(
        "Dafür habe ich weder einen Regensensor noch eine Regenmenge und keine Wettervorhersage (weather-Entität)."
    ), speech
    assert "Soll ich „Bewässere jeden Morgen um 6 Uhr 20 Minuten“ ohne diese Bedingung einrichten?" in speech
    preview = house.say("Ja.").speech
    assert "Bewässerung Garten öffnen" in preview and "Das passiert nur" not in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert all(step.get("action") != "weather.get_forecasts" for step in automation["actions"])


@pytest.mark.parametrize("hours_ago,today,runs", [(30, "partlycloudy", True), (5, "partlycloudy", False),
                                                  (30, "rainy", False)])
def test_immediate_command_is_checked_now(monkeypatch, tmp_path, now, hours_ago, today, runs):
    house = _house(monkeypatch, tmp_path, now, days={0: {"condition": today}}, rain_sensor_changed=hours_ago)
    # A command for now (no time, no event) is checked right away; the
    # command itself then runs its ordinary way (here: the garden pump).
    turn = house.say("Schalte die Gartenpumpe ein, aber nur wenn es nicht geregnet hat bzw. heute nicht regnen soll.")
    switched = [call for call in turn.calls if call[:2] in {("switch", "turn_on"), ("homeassistant", "turn_on")}]
    if runs:
        assert switched and "deshalb führe ich" not in turn.speech, turn
    else:
        assert not switched and "deshalb führe ich „Schalte die Gartenpumpe ein“ jetzt nicht aus" in turn.speech, turn


def test_the_validator_refuses_a_foreign_source():
    from homeintent.weather import WeatherGuard, validate_guard

    entities = house_entities()
    assert validate_guard(WeatherGuard(("no_rain_today",), "weather.zuhause"), entities) is None
    assert validate_guard(WeatherGuard(("no_rain_today",), "light.stehlampe"), entities) is not None
    assert validate_guard(WeatherGuard(("no_rain_recent",), None, "binary_sensor.regensensor"), entities) is None
    assert validate_guard(WeatherGuard(("no_rain_recent",), None, "binary_sensor.haustuer"), entities) is not None
    assert validate_guard(WeatherGuard(("no_rain_recent",)), entities) is not None
    assert validate_guard(WeatherGuard(("anything",), "weather.zuhause"), entities) is not None


def test_entities_with_states_helper_still_works():
    entities = with_states(house_entities(), binary_sensor__regensensor="on")
    assert next(e for e in entities if e.entity_id == "binary_sensor.regensensor").state == "on"
