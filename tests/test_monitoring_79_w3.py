"""7.9 W3: changes by an amount within a window ("um 3 Grad in einer Stunde").

Home Assistant cannot express it without new helpers, so HomeIntent runs it
itself (``MonitorGoalStore``/``MonitorGoalRuntime``) - the preview says so,
and nothing is stored before "Ja".  The meaning: the current value differs
from the window's maximum (falling) or minimum (rising) by at least the
amount; at most one message per window.  A window is never assumed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation
from homeintent.goal_run import GoalRunStore
from homeintent.monitor_goal import MonitorGoalRuntime, MonitorGoalStore, rate_rule_of
from homeintent.rate_monitor import ChangeDirection, RateRule, evaluate_rate

KELLER = "sensor.temperatur_keller"
BAD = "sensor.luftfeuchtigkeit_badezimmer"


def _house(monkeypatch, tmp_path, user: str = "admin") -> HouseConversation:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)
    house.entity._runtime_data.monitor_goals = MonitorGoalStore(tmp_path / "goals.json")
    return house


def _stored(house: HouseConversation) -> list:
    return list(asyncio.run(house.entity._runtime_data.monitor_goals.async_load()))


_NOTIFY = ("Melde dich", "Sag mir Bescheid", "Warne mich", "Benachrichtige mich", "Gib mir Bescheid")
# All mean: Keller temperature falls by >= 3 °C within one hour.
_KELLER_FALLS = (
    "die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt",
    "die Temperatur im Keller in einer Stunde um 3 Grad sinkt",
    "die Temperatur im Keller binnen 60 Minuten um drei Grad fällt",
    "die Kellertemperatur innerhalb von einer Stunde um 3 Grad fällt",
    "die Temperatur im Keller innerhalb einer Stunde um mindestens 3 Grad fällt",
)


@pytest.mark.parametrize("event", _KELLER_FALLS)
@pytest.mark.parametrize("notify", _NOTIFY)
def test_a_fall_within_a_window_has_one_meaning(monkeypatch, tmp_path, notify, event):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(f"{notify}, wenn {event}.")
    assert preview.calls == []
    assert "Das überwache ich selbst; es läuft, solange HomeIntent läuft." in preview.speech
    assert "höchstens einmal in einer Stunde" in preview.speech
    assert "Neustart" in preview.speech
    assert "Soll ich das so einrichten?" in preview.speech
    assert _stored(house) == [], "nothing is stored before the confirmation"
    assert house.automations() == [], "no Home Assistant helper or automation"
    done = house.say("Ja.")
    assert "Eingerichtet" in done.speech
    [record] = _stored(house)
    assert rate_rule_of(record.goal) == RateRule(KELLER, 3.0, "°C", ChangeDirection.FALL, 3600)
    assert record.goal.recipient_person_ids == ("person.philipp",)
    assert record.goal.provenance.confirmed


def test_a_rise_in_percent(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Warne mich, wenn die Luftfeuchtigkeit im Bad in 10 Minuten um 20 Prozent steigt.")
    house.say("Ja.")
    [record] = _stored(house)
    assert rate_rule_of(record.goal) == RateRule(BAD, 20.0, "%", ChangeDirection.RISE, 600)


def test_no_means_nothing_is_stored(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.")
    assert "nichts" in house.say("Nein.").speech
    assert _stored(house) == []


def test_anna_gets_it_on_her_own_phone(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path, user="anna")
    house.say("Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.")
    house.say("Ja.")
    [record] = _stored(house)
    assert record.goal.recipient_person_ids == ("person.anna",)


# --- the rule itself ----------------------------------------------------------------

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def _samples(*pairs: tuple[int, float]) -> list[tuple[datetime, float]]:
    return [(NOW - timedelta(minutes=minutes), value) for minutes, value in pairs]


@pytest.mark.parametrize("samples,current,fires", [
    (_samples((50, 18.0), (20, 17.0)), 15.0, True),     # max 18 -> 15: 3 °C
    (_samples((50, 18.0), (20, 17.0)), 15.5, False),    # only 2.5 °C
    (_samples((90, 19.0), (50, 17.0)), 14.5, False),    # 19 °C lies outside the hour
    (_samples((50, 15.0)), 18.0, False),                # a rise is no fall
    ([], 10.0, False),                                  # no recorder data: never a guess
])
def test_evaluate_rate_both_directions(samples, current, fires):
    rule = RateRule(KELLER, 3.0, "°C", ChangeDirection.FALL, 3600)
    assert (evaluate_rate(rule, samples, current, NOW) is not None) is fires


class _Delivery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, model, rendered) -> bool:
        self.sent.append((model.target_id, rendered.message))
        return True


def test_the_runtime_sends_one_message_per_window(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Melde dich, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.")
    house.say("Ja.")
    delivery = _Delivery()
    history = {"samples": _samples((50, 18.0))}

    async def read_history(entity_id, start, end):
        assert entity_id == KELLER and end - start == timedelta(hours=1)
        return history["samples"]

    async def fresh():
        return house.entities

    runtime = MonitorGoalRuntime(
        house.entity._runtime_data.monitor_goals, GoalRunStore(tmp_path / "runs.json"),
        house.entity._runtime_data.user_contexts, fresh, delivery, read_history=read_history,
    )
    # Unwatched sensors cost nothing and do nothing.
    assert asyncio.run(runtime.async_process_value_change(BAD, 90.0, occurred_at=NOW)) == ()
    assert asyncio.run(runtime.async_process_value_change(KELLER, 16.0, occurred_at=NOW)) == ()
    asyncio.run(runtime.async_process_value_change(KELLER, 15.0, occurred_at=NOW))
    assert delivery.sent == [(
        PHONES[0],
        "„Temperatur Keller“ ist innerhalb von einer Stunde um 3 °C gefallen (von 18 auf 15 °C).",
    )]
    # Still falling ten minutes later: no second message in the same window.
    asyncio.run(runtime.async_process_value_change(KELLER, 14.0, occurred_at=NOW + timedelta(minutes=10)))
    assert len(delivery.sent) == 1
    # A new fall after the window: a new message.
    later = NOW + timedelta(hours=2)
    history["samples"] = [(later - timedelta(minutes=30), 17.0)]
    asyncio.run(runtime.async_process_value_change(KELLER, 13.5, occurred_at=later))
    assert len(delivery.sent) == 2


# --- honest answers -----------------------------------------------------------------


def _refused(monkeypatch, tmp_path, text: str) -> str:
    house = _house(monkeypatch, tmp_path)
    turn = house.say(text)
    assert turn.calls == [] and "Soll ich das so einrichten" not in turn.speech, turn.speech
    house.say("Ja.")
    assert _stored(house) == [] and house.automations() == []
    return turn.speech


@pytest.mark.parametrize("text", [
    "Melde dich, wenn die Temperatur im Keller um 3 Grad fällt.",
    "Sag mir Bescheid, wenn die Luftfeuchtigkeit im Bad um 20 Prozent steigt.",
])
def test_a_missing_window_is_asked_for(monkeypatch, tmp_path, text):
    assert _refused(monkeypatch, tmp_path, text).startswith("In welchem Zeitraum?")


def test_degrees_only_for_temperature(monkeypatch, tmp_path):
    speech = _refused(
        monkeypatch, tmp_path,
        "Melde dich, wenn die Luftfeuchtigkeit im Bad innerhalb einer Stunde um 3 Grad steigt.",
    )
    assert "Grad" in speech and "?" in speech


def test_an_ambiguous_sensor_asks(monkeypatch, tmp_path):
    speech = _refused(
        monkeypatch, tmp_path, "Melde dich, wenn die Temperatur innerhalb einer Stunde um 3 Grad fällt."
    )
    assert "meinst du" in speech


def test_a_device_action_on_a_change_is_honestly_refused(monkeypatch, tmp_path):
    speech = _refused(
        monkeypatch, tmp_path,
        "Schalte die Heizung im Keller ein, wenn die Temperatur im Keller innerhalb einer Stunde um 3 Grad fällt.",
    )
    assert "melden" in speech
