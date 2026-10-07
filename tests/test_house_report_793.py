"""7.9.3 B6 (and A6 arrival push): reports pushed by an automation.

* "Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht" – preview with
  a real example (as of now), "Ja" creates one automation whose only action
  is ``homeintent.send_report`` with a report id; without a time a question.
* The service builds the report at run time (batteries, unreachable
  devices, the week's consumption, monitors that fired, monitor-runtime
  findings), resolves the recipients like a monitor (owner's own device,
  or the confirmed household for a shared report) and sends exactly one
  push.  It never switches anything; an unknown id sends nothing.
* "Zeig mir den Haus-Bericht" / "Was stand im Haus-Bericht?" answer the
  full last report (read-only, no "Ja").
* "Schick mir beim Nachhausekommen eine Zusammenfassung" – arrival of the
  speaker's person, the summary of the absence built at run time.

Phrasings are generated combinatorially.
"""

from __future__ import annotations

import asyncio
import itertools
import types
from datetime import datetime, timedelta

import pytest

import _ha_stub
from _ha_sim import World, fires
from _testhaus import PUSH_OPTIONS, HouseConversation, house_entities

_ha_stub.install()

_VERBS = ("Schick mir", "Sende mir", "Gib mir", "Schicke mir bitte")
_WHEN = ("jeden Sonntag um 18 Uhr", "sonntags um 18 Uhr", "jeden Sonntag um 18:00 Uhr", "am Sonntag um 18 Uhr jeden")
_WHAT = ("einen Haus-Bericht", "einen Hausbericht", "den Wochenbericht", "einen Bericht über das Haus")


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 11, 18, 0, tzinfo=dt_util.now().tzinfo)  # a Sunday
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _house(monkeypatch, tmp_path, user="admin", household=False):
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)
    if household:
        asyncio.run(house.entity._runtime_data.user_contexts.async_set_household(
            ["person.philipp", "person.anna"], confirmed=True
        ))
    return house


@pytest.mark.parametrize("verb,when,what", list(itertools.product(_VERBS, _WHEN, _WHAT)))
def test_weekly_request_paraphrases(verb, when, what):
    from homeintent.house_report import parse_report_request

    for text in (f"{verb} {when} {what}.", f"{verb} {what} {when}."):
        request = parse_report_request(text)
        assert request is not None and request.kind == "weekly", text
        assert request.weekdays == ("sun",) and (request.hour, request.minute) == (18, 0), text


_ARRIVALS = ("beim Nachhausekommen", "wenn ich nach Hause komme", "wenn ich heimkomme", "bei meiner Ankunft",
             "sobald ich wieder da bin", "wenn ich zurück bin")
_SUMMARIES = ("eine Zusammenfassung", "eine Zusammenfassung, was los war", "einen Überblick, was passiert ist")


@pytest.mark.parametrize("verb,arrival,summary", list(itertools.product(_VERBS, _ARRIVALS, _SUMMARIES)))
def test_arrival_request_paraphrases(verb, arrival, summary):
    from homeintent.house_report import parse_report_request

    request = parse_report_request(f"{verb} {arrival} {summary}.")
    assert request is not None and request.kind == "arrival", (verb, arrival, summary)


@pytest.mark.parametrize("text", [
    "Was stand im Haus-Bericht?", "Zeig mir den Haus-Bericht.", "Lies mir den Hausbericht vor.",
    "Wie sieht der Wochenbericht aus?", "Sag mir den Haus-Bericht nochmal.",
])
def test_show_paraphrases(text):
    from homeintent.house_report import parse_report_request, parse_report_show

    assert parse_report_show(text) and parse_report_request(text) is None, text


@pytest.mark.parametrize("text", [
    "Schick mir keinen Haus-Bericht mehr.", "Was ist heute passiert?", "Schick mir eine Nachricht, wenn die Tür aufgeht.",
    "Wie wird das Wetter?", "Schick mir jeden Sonntag eine Erinnerung.",
])
def test_negatives(text):
    from homeintent.house_report import parse_report_request, parse_report_show

    assert parse_report_request(text) is None and not parse_report_show(text), text


def test_weekly_preview_yes_and_config(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.").speech
    assert preview.startswith("Jeden Sonntag um 18:00 Uhr schicke ich dir an „Handy Philipp“ einen Haus-Bericht")
    assert "zum Beispiel „Haus-Bericht: 2 schwache Batterien“" in preview, preview
    assert "Ich schalte dabei nichts" in preview and "<" not in preview
    assert house.automations() == []
    turn = house.say("Ja.")
    assert turn.speech.startswith("Eingerichtet.")
    [automation] = house.automations()
    assert automation["actions"] == [{"action": "homeintent.send_report",
                                      "data": {"report_id": automation["actions"][0]["data"]["report_id"]}}]
    assert automation["triggers"] == [{"trigger": "time", "at": "18:00:00"}]
    assert automation["conditions"] == [{"condition": "time", "weekday": ["sun"]}]


def test_without_time_asks_then_previews(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    assert house.say("Schick mir jeden Sonntag einen Haus-Bericht.").speech.startswith("Um wie viel Uhr")
    assert "Jeden Sonntag um 18:00 Uhr" in house.say("Um 18 Uhr.").speech


def test_no_creates_nothing(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    house.say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.")
    assert house.say("Nein.").speech == "In Ordnung, ich richte nichts ein."
    assert house.automations() == []


def _entry(house):
    return types.SimpleNamespace(options=house.entity.entry.options, runtime_data=house.entity._runtime_data)


def _send(house, report_id, now, rows=None, entities=None):
    from homeintent.report_runtime import async_send_report

    async def read(hass, ids, start, end, *, attributes=False):
        return rows(ids, start, end, attributes) if rows is not None else {}

    before = len(house.sink.notify_calls)
    other = len(house.sink.other_calls)
    sent = asyncio.run(async_send_report(
        house.entity.hass, _entry(house), report_id, entities or house_entities(), now, read_rows=read,
    ))
    return sent, house.sink.notify_calls[before:], house.sink.other_calls[other:]


def test_service_sends_the_short_report_once_and_show_has_details(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    house.say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.")
    house.say("Ja.")
    report_id = house.automations()[0]["actions"][0]["data"]["report_id"]

    def rows(ids, start, end, attributes):
        if not attributes:
            return {}
        return {"automation.fenster": [(start, "on", {"friendly_name": "Fenster-Warnung",
                                                      "last_triggered": (now - timedelta(days=2)).isoformat()}),
                                       (now - timedelta(days=1), "on", {"friendly_name": "Fenster-Warnung",
                                                                        "last_triggered": (now - timedelta(days=1)).isoformat()})]}

    from homeassistant.core import State

    house.entity.hass.states._states["automation.fenster"] = State("automation.fenster", "on", {})
    sent, pushes, others = _send(house, report_id, now, rows)
    assert sent
    [(service, data)] = pushes
    assert data["entity_id"] == ["notify.handy_philipp_nachricht"]
    assert data["message"].startswith("Haus-Bericht: 2 schwache Batterien, 2 Meldungen.")
    assert "Zeig mir den Haus-Bericht" in data["message"]
    assert [call for call in others if call[0] not in {"notify"}] == []  # never switches anything
    detail = house.say("Was stand im Haus-Bericht?").speech
    assert detail.startswith("Der letzte Haus-Bericht vom 11.10. um 18:00 Uhr: Schwache Batterien: "), detail
    assert "Batterie Rauchmelder oben (9 %)" in detail and "„Fenster-Warnung“ 2-mal" in detail


def test_unavailable_devices_are_reported(monkeypatch, tmp_path, now):
    from _testhaus import with_states

    house = _house(monkeypatch, tmp_path)
    house.say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.")
    house.say("Ja.")
    report_id = house.automations()[0]["actions"][0]["data"]["report_id"]
    entities = with_states(house_entities(), light__stehlampe="unavailable")
    _, [(_, data)], _ = _send(house, report_id, now, entities=entities)
    assert "1 Gerät nicht erreichbar" in data["message"]
    assert "Nicht erreichbar: Stehlampe" in house.say("Zeig mir den Haus-Bericht.").speech


def test_unknown_report_id_sends_nothing(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    sent, pushes, _ = _send(house, "0" * 32, now)
    assert not sent and pushes == []


def test_a_report_goes_only_to_its_owner(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, user="anna")
    house.say("Schick mir jeden Sonntag um 18 Uhr einen Haus-Bericht.")
    house.say("Ja.")
    report_id = house.automations()[0]["actions"][0]["data"]["report_id"]
    _, [(_, data)], _ = _send(house, report_id, now)
    assert data["entity_id"] == ["notify.handy_anna_nachricht"]


def test_a_shared_report_goes_to_the_confirmed_household(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, household=True)
    preview = house.say("Schick uns allen jeden Sonntag um 18 Uhr einen Haus-Bericht, für uns alle.").speech
    assert "gemeinsam" in preview, preview
    house.say("Ja.")
    report_id = house.automations()[0]["actions"][0]["data"]["report_id"]
    _, [(_, data)], _ = _send(house, report_id, now)
    assert sorted(data["entity_id"]) == ["notify.handy_anna_nachricht", "notify.handy_philipp_nachricht"]


def test_show_before_any_report_is_the_state_now(monkeypatch, tmp_path, now):
    speech = _house(monkeypatch, tmp_path).say("Zeig mir den Haus-Bericht.").speech
    assert speech.startswith("Einen Haus-Bericht habe ich dir noch nicht geschickt. Nach heutigem Stand: ")


def test_the_validator_allows_only_the_report_action():
    from homeintent.house_report import ReportRequest, build_config, validate_report_config

    entities = house_entities()
    good = build_config("a" * 32, ReportRequest("weekly", ("sun",), 18, 0), None)
    assert validate_report_config(good, entities) is None
    bad = dict(good, actions=[{"action": "light.turn_on", "target": {"entity_id": "light.stehlampe"}}])
    assert validate_report_config(bad, entities) is not None
    extra = dict(good, actions=[{"action": "homeintent.send_report",
                                 "data": {"report_id": "a" * 32, "target": "notify.fremd"}}])
    assert validate_report_config(extra, entities) is not None
    two = dict(good, actions=good["actions"] * 2)
    assert validate_report_config(two, entities) is not None
    arrival = build_config("b" * 32, ReportRequest("arrival"), "person.unbekannt")
    assert validate_report_config(arrival, entities) is not None


# --- A6: push on arrival --------------------------------------------------------

def test_arrival_preview_yes_and_trigger(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    preview = house.say("Schick mir beim Nachhausekommen eine Zusammenfassung.").speech
    assert preview.startswith("Wenn du nach Hause kommst, schicke ich dir an „Handy Philipp“ eine Zusammenfassung")
    assert "erstellt erst in dem Moment" in preview and "Ich schalte dabei nichts" in preview
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert (trigger["entity_id"], trigger["to"]) == ("person.philipp", "home")
    # HA evaluation: arriving fires, leaving or a restart ("unknown" -> home) does not.
    world = World({"person.philipp": "home"})
    assert fires(automation, world, "person.philipp", "not_home")
    assert not fires(automation, world, "person.philipp", "unknown")
    assert not fires(automation, World({"person.philipp": "not_home"}), "person.philipp", "home")


def test_arrival_push_contains_the_absence_built_at_run_time(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path)
    house.say("Wenn ich heimkomme, schick mir eine Zusammenfassung, was los war.")
    house.say("Ja.")
    report_id = house.automations()[0]["actions"][0]["data"]["report_id"]
    left, back = now.replace(hour=9), now.replace(hour=17, minute=30)

    def rows(ids, start, end, attributes):
        data = {
            "person.philipp": [(now - timedelta(days=1), "home"), (left, "not_home"), (back, "home")],
            "binary_sensor.haustuer": [(now.replace(hour=0), "off"), (now.replace(hour=15, minute=1), "on")],
        }
        return {
            key: [(max(moment, start), state, {}) for moment, state in values if moment <= end]
            for key, values in data.items() if key in ids
        }

    _, [(_, data)], _ = _send(house, report_id, now, rows)
    assert data["entity_id"] == ["notify.handy_philipp_nachricht"]
    assert data["message"].startswith("Während du weg warst (09:00 bis 17:30 Uhr): um 15:01 Haustür wurde geöffnet")


def test_arrival_without_known_person_is_honest(monkeypatch, tmp_path, now):
    house = HouseConversation(monkeypatch, options=PUSH_OPTIONS)
    speech = house.say("Schick mir beim Nachhausekommen eine Zusammenfassung.").speech
    assert "welche Person du bist" in speech and house.automations() == []
