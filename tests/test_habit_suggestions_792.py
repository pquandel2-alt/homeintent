"""7.9.2 B2: suggestions from habits - statistics over the person's own
commands (execution trace), never a model.

A habit: the same effect on at least 4 of the last 7 days, always within
45 minutes of the median time ("an Werktagen" if only on working days).
Offered once per pattern, only to the person who did it, never during
another question; "Ja" leads to the normal automation preview with its own
"Ja". Queries, opt-out and push. Accesses, locks and critical devices are
never proposed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import _ha_stub
from _testhaus import PUSH_OPTIONS, HouseConversation

_ha_stub.install()

EG_LIGHTS = ("light.esszimmer_pendelleuchte", "light.flurlicht", "light.kuechenlicht", "light.stehlampe")


def _records(actor_user: str, plan: str, days: list[datetime], user_present: bool = True):
    from homeintent.execution_trace import TraceRecord, actor_hash

    return [
        TraceRecord(
            execution_id=f"{plan}-{day.isoformat()}", created_at=day.isoformat(), actor=actor_hash(actor_user),
            user_present=user_present, origin="explicit_command", plans=(plan,), executed=True,
        )
        for day in days
    ]


@pytest.fixture
def now(monkeypatch):
    from homeassistant.util import dt as dt_util

    fixed = datetime(2026, 10, 9, 22, 40, tzinfo=dt_util.now().tzinfo)  # a Friday
    monkeypatch.setattr(dt_util, "now", lambda *args: fixed)
    return fixed


def _workdays(now: datetime, count: int, hour=22, minute=30, spread=(0, 5, -10, 12, 3)) -> list[datetime]:
    days, day = [], now.replace(hour=hour, minute=minute)
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.append(day + timedelta(minutes=spread[len(days) % len(spread)]))
    return days


def _house(monkeypatch, tmp_path, records, user="admin"):
    import homeintent.controllers.insights as insights

    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)
    monkeypatch.setattr(insights.InsightsController, "_trace_records", lambda self: tuple(records))
    return house


def _plan(service="light.turn_off", targets=EG_LIGHTS) -> str:
    return f"{service} {', '.join(targets)}"


def test_offer_after_the_command_then_preview(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, 4)))
    turn = house.say("Schalte das Flurlicht ein.")
    assert "Du schaltest an Werktagen gegen 22:30 Uhr die Lichter im Erdgeschoss aus. Soll ich das automatisch machen?" in turn.speech, turn.speech
    preview = house.say("Ja.").speech
    assert preview.startswith("Automation erkannt: Wenn es 22:30 Uhr ist") and "Soll diese Automation erstellt werden?" in preview, preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"] == [{"trigger": "time", "at": "22:30:00"}]
    # Offered once per pattern only.
    assert "automatisch machen" not in house.say("Schalte das Flurlicht aus.").speech


@pytest.mark.parametrize(("count", "offered"), [(3, False), (4, True), (6, True)])
def test_needs_four_of_seven_days(monkeypatch, tmp_path, now, count, offered):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, count)))
    speech = house.say("Hast du Vorschläge für Automationen?").speech
    assert ("automatisch machen" in speech) is offered, speech


def test_times_must_be_similar(monkeypatch, tmp_path, now):
    days = _workdays(now, 4, spread=(0, 0, 0, 120))
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), days))
    assert "Gerade habe ich keinen neuen Vorschlag" in house.say("Hast du Vorschläge für Automationen?").speech


def test_only_the_person_who_did_it(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, 5)), user="anna")
    assert "automatisch machen" not in house.say("Schalte das Flurlicht ein.").speech
    assert "Gerade habe ich keinen neuen Vorschlag" in house.say("Hast du Vorschläge für Automationen?").speech


@pytest.mark.parametrize("plan", [
    "lock.lock lock.haustuerschloss", "cover.open_cover cover.garagentor", "valve.open_valve valve.hauptwasserventil",
    "alarm_control_panel.alarm_arm_away alarm_control_panel.alarmanlage",
])
def test_access_and_critical_are_never_proposed(monkeypatch, tmp_path, now, plan):
    house = _house(monkeypatch, tmp_path, _records("admin", plan, _workdays(now, 6)))
    assert "Gerade habe ich keinen neuen Vorschlag" in house.say("Hast du Vorschläge für Automationen?").speech


def test_no_and_off(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, 4)))
    house.say("Schalte das Flurlicht ein.")
    assert house.say("Nein.").speech == "In Ordnung, das schlage ich nicht mehr vor."
    assert "Gerade habe ich keinen neuen Vorschlag" in house.say("Hast du Vorschläge für Automationen?").speech
    house2 = _house(monkeypatch, tmp_path / "b", _records("admin", _plan(), _workdays(now, 4)))
    assert "keine Automationen mehr vor" in house2.say("Schlag mir nichts mehr vor.").speech
    assert "automatisch machen" not in house2.say("Schalte das Flurlicht ein.").speech


def test_never_during_another_question(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, 4)))
    speech = house.say("Schalte das Licht ein.").speech  # asks which light
    assert "automatisch machen" not in speech


def test_list(monkeypatch, tmp_path, now):
    records = _records("admin", _plan(), _workdays(now, 4)) + _records(
        "admin", _plan("light.turn_on", ("light.stehlampe",)), [now - timedelta(days=d, hours=4) for d in range(1, 6)]
    )
    speech = _house(monkeypatch, tmp_path, records).say("Welche Gewohnheiten hast du erkannt?").speech
    assert "Du schaltest jeden Tag gegen 18:40 Uhr Stehlampe ein." in speech, speech
    assert "an Werktagen gegen 22:30 Uhr die Lichter im Erdgeschoss aus" in speech


def test_push_mode(monkeypatch, tmp_path, now):
    house = _house(monkeypatch, tmp_path, _records("admin", _plan(), _workdays(now, 4)))
    assert "per Push" in house.say("Schick mir Vorschläge per Push.").speech
    speech = house.say("Schalte das Flurlicht ein.").speech
    assert "automatisch machen" not in speech
    import asyncio

    async def drain():
        await asyncio.gather(*house.entity.hass._tasks)

    asyncio.run(drain())
    pushes = [data for service, data in house.sink.notify_calls]
    assert any("HomeIntent-Vorschlag" in str(data) for data in pushes), pushes


def test_discovery_rule_constants():
    from homeintent.habit_suggestions import MIN_DAYS, TIME_TOLERANCE_MINUTES, WINDOW_DAYS

    assert (MIN_DAYS, WINDOW_DAYS, TIME_TOLERANCE_MINUTES) == (4, 7, 45)


def test_no_habit_yet_is_an_honest_answer(monkeypatch, tmp_path, now):
    """Prüfsatz: without any repeated action (and without the learning
    registry) the question is answered, never "nicht gefunden"."""
    speech = _house(monkeypatch, tmp_path, []).say("Welche Gewohnheiten hast du erkannt?").speech
    assert speech.startswith("Ich habe noch keine Gewohnheit erkannt."), speech
    assert "an mindestens 4 von 7 Tagen" in speech
