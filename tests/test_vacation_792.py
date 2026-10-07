"""7.9.2 B4: vacation mode - an absence profile with an end date.

Generated: start phrasings x end dates; the preview lists every part; "Ja"
creates the bounded monitoring, the optional simulation and switches the
helper (through the one write path); the end automation and "Urlaub
vorbei" take *everything* back. Never heating, gates, doors or locks.
The generated automations are evaluated with Jinja like Home Assistant
does (simulation schedule, end condition).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_ha_stub.install()


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 6, 12, 0, tzinfo=dt_util.now().tzinfo)  # a Tuesday
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _house(monkeypatch, tmp_path, household=True) -> HouseConversation:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    if household:
        asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(
            ["person.philipp", "person.anna"], confirmed=True
        ))
    return house


def _store(tmp_path):
    path = tmp_path / "homeintent_vacation.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _metadata(tmp_path):
    path = tmp_path / "homeintent_automation_metadata.json"
    return json.loads(path.read_text()) if path.exists() else {}


_STARTS = (
    "Ich bin bis {d} weg.", "Wir sind bis {d} im Urlaub.", "Urlaubsmodus bis {d}.",
    "Wir fahren bis {d} in den Urlaub.", "Schalte den Urlaubsmodus bis {d} ein.",
)
_ENDS = {"Sonntag": date(2026, 10, 11), "zum 20.": date(2026, 10, 20), "20.10.": date(2026, 10, 20),
         "Freitag": date(2026, 10, 9)}


@pytest.mark.parametrize("end", list(_ENDS))
@pytest.mark.parametrize("start", _STARTS)
def test_start_preview_lists_every_part(monkeypatch, tmp_path, now, start, end):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(start.format(d=end)).speech
    expected = _ENDS[end]
    assert f"({expected:%d.%m.} um 23:59 Uhr)" in preview, preview
    assert "1. Jede Tür- oder Fensteröffnung und jede Bewegung" in preview
    assert "„Urlaubsmodus“ schalte ich jetzt ein und am Ende wieder aus" in preview
    assert "Heizung, Tore, Türen und Schlösser bleiben unberührt" in preview
    assert house.automations() == []  # nothing before "Ja"


def test_yes_creates_and_end_takes_everything_back(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    house.say("Wir fahren bis Sonntag in den Urlaub und simuliere Anwesenheit.")
    turn = house.say("Ja.")
    assert turn.speech == "Der Urlaubsmodus läuft bis 11.10. 23:59 Uhr."
    assert ("input_boolean", "turn_on", {"entity_id": "input_boolean.urlaubsmodus"}) in turn.calls
    automations = house.automations()
    assert len(automations) == 3  # monitoring, simulation, end
    ids = {item["id"] for item in automations}
    assert ids == set(_store(tmp_path)["automation_ids"])
    # Only allowed actions anywhere.
    text = json.dumps(automations)
    for forbidden in ("climate.", "cover.", "lock.", "valve.", "alarm_control_panel."):
        assert f'"action": "{forbidden}' not in text
    status = house.say("Was macht der Urlaubsmodus gerade?").speech
    assert status.startswith("Der Urlaubsmodus läuft bis 11.10. um 23:59 Uhr.") and "Anwesenheit simuliere ich" in status
    # The end automation deletes every vacation automation and itself.
    end = next(item for item in automations if item["alias"] == "Urlaubsmodus: Ende")
    deleted = {step["data"]["automation_id"] for step in end["actions"] if step["action"] == "homeintent.delete_automation"}
    assert deleted == ids
    assert {"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.urlaubsmodus"}} in end["actions"]
    assert {"trigger": "homeassistant", "event": "start"} in end["triggers"]
    # "Urlaub vorbei" through preview and "Ja": nothing remains.
    question = house.say("Urlaub vorbei.").speech
    assert "lösche seine 3 Automationen" in question and "Soll ich?" in question
    done = house.say("Ja.")
    assert done.speech == "Der Urlaubsmodus ist beendet; alles ist zurückgenommen."
    assert ("input_boolean", "turn_off", {"entity_id": "input_boolean.urlaubsmodus"}) in done.calls
    assert house.automations() == [] and _store(tmp_path) == {}
    assert not set(_metadata(tmp_path)) & ids
    assert house.say("Was macht der Urlaubsmodus gerade?").speech == "Der Urlaubsmodus ist aus."


@pytest.mark.parametrize("phrase", ["Urlaub vorbei.", "Wir sind zurück.", "Ich bin wieder zurück.", "Beende den Urlaubsmodus."])
def test_end_phrases(monkeypatch, tmp_path, now, phrase):
    house = _house(monkeypatch, tmp_path)
    house.say("Ich bin bis Sonntag weg.")
    house.say("Ja.")
    assert "Soll ich?" in house.say(phrase).speech
    house.say("Ja.")
    assert house.automations() == []


def test_no_keeps_nothing(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    house.say("Ich bin bis Sonntag weg.")
    assert house.say("Nein.").speech == "In Ordnung, ich ändere nichts."
    assert house.automations() == [] and _store(tmp_path) == {}


def test_without_end_date_asks(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    assert house.say("Wir fahren in den Urlaub.").speech.startswith("Bis wann seid ihr weg?")
    preview = house.say("bis Freitag").speech
    assert "(09.10. um 23:59 Uhr)" in preview, preview


def test_negation_is_respected(monkeypatch, tmp_path, now):
    from homeintent.vacation import parse_vacation_request

    assert parse_vacation_request("Schalte den Urlaubsmodus nicht ein.", now.date()) is None
    assert parse_vacation_request("Ich bin bis Sonntag nicht da.", now.date()) is not None


def test_simulation_from_habits_with_deterministic_jitter(now):
    from homeintent.habit_suggestions import TimedHabit
    from homeintent.vacation import build_plan, jitter_minutes, parse_vacation_request, plan_configs

    habits = [
        TimedHabit("a", "x", "light", "turn_on", ("light.stehlampe",), 19, 0, False, 6),
        TimedHabit("b", "x", "light", "turn_off", ("light.stehlampe",), 22, 30, False, 6),
    ]
    request = parse_vacation_request("Ich bin bis Sonntag weg und simuliere Anwesenheit.", now.date())
    plan = build_plan(request, now, house_entities(), habits, ("notify.handy_philipp_nachricht",))
    assert plan.simulation_from_history and plan.lights[0].on == (19, 0)
    configs, _ = plan_configs(plan, now.date())
    again, _ = plan_configs(plan, now.date())
    simulation = next(config for _, config in configs if "simulieren" in config["alias"])
    assert simulation["triggers"] == next(c for _, c in again if "simulieren" in c["alias"])["triggers"]
    # Every day has its own on/off time within ±15 minutes, decided by the template.
    import jinja2

    template = simulation["conditions"][1]["value_template"]
    for offset in range(6):
        day = now.date() + timedelta(days=offset)
        shift = jitter_minutes(day, "light.stehlampe", "on")
        assert -15 <= shift <= 15
        at = (datetime.combine(day, datetime.min.time()) + timedelta(hours=19, minutes=shift)).strftime("%H:%M")
        env = jinja2.Environment()
        rendered = env.from_string(template).render(
            trigger={"id": f"on|{at}"}, now=lambda: datetime.combine(day, datetime.min.time())
        )
        assert rendered == "True"


def test_without_history_fixed_times_are_said(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Ich bin bis Sonntag weg und simuliere Anwesenheit.").speech
    assert "fehlt mir Verlauf" in preview and "feste Zeiten" in preview and "19:00–22:30 Uhr" in preview


def test_validator_refuses_foreign_actions():
    from homeintent.vacation import validate_plan

    entities = house_entities()
    bad = [("x", {"actions": [{"action": "lock.unlock", "target": {"entity_id": "lock.haustuerschloss"}}]})]
    assert validate_plan(bad, entities) is not None
    heat = [("x", {"actions": [{"action": "climate.set_temperature", "target": {"entity_id": "climate.heizung_buero"}}]})]
    assert validate_plan(heat, entities) is not None


@pytest.mark.parametrize("command", [
    "Schalte den Urlaubsmodus ein.", "Aktiviere den Urlaubsmodus.", "schalte den urlaubs modus ein",
    "Mach den Urlaubsmodus an.",
])
def test_the_helper_command_stays_a_device_command(monkeypatch, tmp_path, command):
    """Entwicklungs-Benchmark 7.7 (Zeilen 44, 455, 505): naming the helper
    without an end date or travel words switches the helper as before; the
    profile needs "bis …" or "Wir fahren in den Urlaub"."""
    from homeintent.vacation import parse_vacation_request

    assert parse_vacation_request(command, date(2026, 10, 6)) is None
    assert parse_vacation_request(command.rstrip(".") + " bis Sonntag.", date(2026, 10, 6)) is not None
