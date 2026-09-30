"""7.9 W2: absence and inactivity.

"X passiert D lang nicht" is a state trigger on the rest state with ``for``
("keine Bewegung" = motion sensor off, "nicht geöffnet" = closed); "bis U
nicht" is a time trigger at U plus a typed condition "unchanged since
midnight" built only from entity ids.  Home Assistant resets ``for`` timers
and ``last_changed`` on a restart - every inactivity preview says so.
Paraphrases are generated from building blocks; the effect is checked with a
model of Home Assistant's evaluation in both directions.
"""

from __future__ import annotations

import json

import pytest

from _ha_sim import World, condition_holds, entities_of, fires, for_trigger_fires
from _testhaus import PUSH_OPTIONS, HouseConversation

RESTART = "Startet Home Assistant neu, beginnt die Wartezeit von vorn."
RESTART_TODAY = "Startet Home Assistant an diesem Tag neu"
MOTION = "binary_sensor.bewegung_flur"

_NOTIFY = ("Melde dich", "Sag mir Bescheid", "Warne mich", "Benachrichtige mich")
# All mean: no motion in the hall for 12 hours.
_NO_MOTION = (
    "sich im Flur 12 Stunden nichts bewegt",
    "sich im Flur zwölf Stunden lang nichts bewegt",
    "im Flur 12 Stunden keine Bewegung erkannt wird",
    "im Flur seit 12 Stunden keine Bewegung erkannt wurde",
    "12 Stunden lang im Flur keine Bewegung erkannt wurde",
)
# All mean: the front door stayed closed for two days.
_DOOR_UNUSED = (
    "die Haustür zwei Tage nicht geöffnet wurde",
    "die Haustür 2 Tage lang nicht geöffnet wurde",
    "die Haustür seit zwei Tagen nicht geöffnet wurde",
    "die Haustür 48 Stunden nicht aufgemacht wurde",
)


def _create(monkeypatch, tmp_path, text: str, then: str = "Ja.") -> tuple[str, dict]:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    preview = house.say(text)
    assert preview.calls == [], text
    assert "Soll ich das so einrichten" in preview.speech, (text, preview.speech)
    assert house.automations() == [], "nothing is written before the confirmation"
    if "Nur heute oder jeden Tag" in preview.speech:
        # Once or every day is asked, never guessed (7.3.3).
        preview = house.say(then)
        assert "Soll ich das so einrichten" in preview.speech, preview.speech
        then = "Ja."
    house.say(then)
    [automation] = house.automations()
    return preview.speech, automation


@pytest.mark.parametrize("event", _NO_MOTION)
@pytest.mark.parametrize("notify", _NOTIFY)
def test_no_motion_for_a_span(monkeypatch, tmp_path, notify, event):
    speech, automation = _create(monkeypatch, tmp_path, f"{notify}, wenn {event}.")
    [trigger] = automation["triggers"]
    assert entities_of(trigger) == {MOTION}
    assert trigger["to"] == "off" and trigger["for"] == {"seconds": 12 * 3600}
    assert automation.get("conditions", []) == []
    assert RESTART in speech, speech
    assert "12 Stunden lang keine Bewegung erkannt wurde" in speech
    assert automation["actions"][0]["data"]["message"] == "Im Flur wurde seit 12 Stunden keine Bewegung erkannt."


@pytest.mark.parametrize("event", _DOOR_UNUSED)
@pytest.mark.parametrize("notify", _NOTIFY)
def test_a_door_not_opened_for_days(monkeypatch, tmp_path, notify, event):
    speech, automation = _create(monkeypatch, tmp_path, f"{notify}, wenn {event}.")
    [trigger] = automation["triggers"]
    assert entities_of(trigger) == {"binary_sensor.haustuer"}
    assert trigger["to"] == "off" and trigger["for"] == {"seconds": 48 * 3600}
    assert RESTART in speech, speech
    assert "nicht geöffnet" in automation["actions"][0]["data"]["message"]


def test_home_assistant_fires_only_after_the_whole_span(monkeypatch, tmp_path):
    _, automation = _create(monkeypatch, tmp_path, "Melde dich, wenn sich im Flur 12 Stunden nichts bewegt.")
    world = World({MOTION: "off"}, since={MOTION: 11 * 3600})
    assert not for_trigger_fires(automation, world, MOTION)
    world.since[MOTION] = 12 * 3600
    assert for_trigger_fires(automation, world, MOTION)
    world.states[MOTION] = "on"  # motion again: the rest state is gone
    assert not for_trigger_fires(automation, world, MOTION)


_UNTIL = (
    "Warne mich, wenn bis 10 Uhr im Flur keine Bewegung erkannt wurde.",
    "Warne mich, wenn im Flur bis 10 Uhr keine Bewegung war.",
    "Melde dich, wenn es bis 10 Uhr im Flur keine Bewegung gab.",
    "Sag mir Bescheid, wenn Oma bis 10 Uhr keine Bewegung im Flur hatte.",
)


@pytest.mark.parametrize("text", _UNTIL)
def test_no_motion_until_a_time(monkeypatch, tmp_path, text):
    speech, automation = _create(monkeypatch, tmp_path, text, then="Jeden Tag.")
    [trigger] = automation["triggers"]
    assert trigger["trigger"] == "time" and trigger["at"].startswith("10:00")
    [condition] = automation["conditions"]
    assert condition["condition"] == "template"
    assert condition["value_template"] == (
        "{{ is_state('binary_sensor.bewegung_flur', 'off') and "
        "states.binary_sensor.bewegung_flur.last_changed < today_at('00:00') }}"
    )
    assert RESTART_TODAY in speech, speech
    # The effect: quiet morning -> message; any change since midnight -> none.
    quiet = World({MOTION: "off"}, clock=(10, 0))
    assert fires(automation, quiet)
    moved = World({MOTION: "off"}, clock=(10, 0), changed_today={MOTION: True})
    assert not fires(automation, moved)
    moving_now = World({MOTION: "on"}, clock=(10, 0))
    assert not fires(automation, moving_now)
    assert not fires(automation, World({MOTION: "off"}, clock=(9, 59)))


def test_a_named_person_is_not_pretended_to_be_recognized(monkeypatch, tmp_path):
    speech, _ = _create(monkeypatch, tmp_path, _UNTIL[3], then="Jeden Tag.")
    assert "nicht, wer sich bewegt" in speech and "Oma" in speech


def test_no_motion_until_is_the_same_automation_for_every_wording(monkeypatch, tmp_path):
    automations = [
        json.dumps(_create(monkeypatch, tmp_path / str(i), text, then="Jeden Tag.")[1]["conditions"])
        for i, text in enumerate(_UNTIL)
    ]
    assert len(set(automations)) == 1


def test_the_washing_machine_did_not_run_until_a_time(monkeypatch, tmp_path):
    speech, automation = _create(
        monkeypatch, tmp_path,
        "Sag mir Bescheid, wenn die Waschmaschine bis 20 Uhr nicht gelaufen ist.", then="Jeden Tag.",
    )
    [condition] = automation["conditions"]
    assert condition["value_template"] == (
        "{{ states.sensor.waschmaschine_status.last_changed < today_at('00:00') }}"
    )
    assert "Die Waschmaschine ist heute bis 20:00 Uhr nicht gelaufen." == automation["actions"][0]["data"]["message"]
    assert condition_holds(condition, World({"sensor.waschmaschine_status": "idle"}))
    assert not condition_holds(
        condition, World({"sensor.waschmaschine_status": "idle"},
                         changed_today={"sensor.waschmaschine_status": True}),
    )


# --- honest answers ------------------------------------------------------------------


def _refused(monkeypatch, tmp_path, text: str) -> str:
    house = HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)
    turn = house.say(text)
    assert turn.calls == [], text
    assert "Soll ich das so einrichten" not in turn.speech, (text, turn.speech)
    house.say("Ja.")
    assert house.automations() == [], text
    return turn.speech


def test_a_place_without_a_detector_is_named(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Warne mich, wenn Oma bis 10 Uhr keine Bewegung im Bad hatte.")
    assert "Badezimmer gibt es keinen Bewegungs- oder Präsenzmelder" in speech


def test_without_a_time_it_asks_until_when(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Sag mir Bescheid, wenn die Waschmaschine heute nicht lief.")
    assert speech.startswith("Bis wann")


def test_a_power_sensor_alone_cannot_tell_a_past_run(monkeypatch, tmp_path):
    speech = _refused(monkeypatch, tmp_path, "Sag mir Bescheid, wenn der Trockner bis 20 Uhr nicht gelaufen ist.")
    assert "Leistungssensor" in speech and "Binärsensor" in speech and "den Trockner" in speech


def test_negation_without_a_span_is_not_inactivity(monkeypatch, tmp_path):
    """"wenn sich im Flur nichts bewegt" names no span: nothing to wait for."""
    speech = _refused(monkeypatch, tmp_path, "Melde dich, wenn sich im Flur nichts bewegt.")
    assert "?" in speech or "nicht" in speech
