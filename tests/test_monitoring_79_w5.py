"""7.9 W5: repeating and escalating.

``ActionType.REPEAT`` (steps, interval, the condition that keeps it going
and an upper bound - 12 by default, always said) and ``ActionType.ESCALATE``
(wait for the end of the situation; only a timeout notifies the second
recipient).  The effect is run on a timeline with a model of Home
Assistant's ``repeat``/``wait_template`` semantics: how many messages, when,
to whom.
"""

from __future__ import annotations

import json

import pytest

from _ha_sim import World, run
from _testhaus import PHONES, PUSH_OPTIONS, HouseConversation

GATE = "cover.garagentor"
DOOR = "binary_sensor.haustuer"
PHILIPP, ANNA = PHONES


def _house(monkeypatch, tmp_path) -> HouseConversation:
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS)


def _create(monkeypatch, tmp_path, text: str, answers: tuple[str, ...] = ()) -> tuple[str, dict]:
    house = _house(monkeypatch, tmp_path)
    speech = house.say(text).speech
    for answer in answers:
        speech = house.say(answer).speech
    assert "Soll ich das so einrichten" in speech, (text, speech)
    assert house.automations() == [], "nothing is written before the confirmation"
    house.say("Ja.")
    [automation] = house.automations()
    return speech, automation


_REMIND = ("erinnere mich", "melde dich", "sag mir Bescheid")
_GATE_REPEAT = [
    "Wenn das Garagentor offen ist, {v} alle 10 Minuten, bis es zu ist.",
    "Wenn das Garagentor aufgeht, {v} alle 10 Minuten, bis es zu ist.",
    "Wenn das Garagentor geöffnet wird, {v} alle 10 Minuten, solange es offen ist.",
    "Sobald das Garagentor offen ist, {v} alle zehn Minuten, bis das Garagentor geschlossen ist.",
    "Melde dich, wenn das Garagentor offen ist, und {v} alle 10 Minuten, bis es zu ist.",
]


def _meaning(automation: dict) -> str:
    return json.dumps({"triggers": automation["triggers"], "actions": automation["actions"]}, sort_keys=True)


@pytest.mark.parametrize("verb", _REMIND)
@pytest.mark.parametrize("form", _GATE_REPEAT)
def test_repeat_until_closed_has_one_meaning(monkeypatch, tmp_path, form, verb):
    text = form.format(v=verb)
    speech, automation = _create(monkeypatch, tmp_path, text)
    [trigger] = automation["triggers"]
    assert trigger == {"trigger": "state", "entity_id": GATE, "to": "open"}
    [repeat] = automation["actions"]
    assert repeat["repeat"]["while"] == [
        {"condition": "state", "entity_id": GATE, "state": "open"},
        {"condition": "template", "value_template": "{{ repeat.index <= 12 }}"},
    ]
    assert repeat["repeat"]["sequence"][-1] == {"delay": {"seconds": 600}}
    assert automation["mode"] == "restart"
    assert "alle 10 Minuten" in speech and "höchstens 12-mal" in speech and "längstens 2 Stunden" in speech
    assert "Startet Home Assistant währenddessen neu" in speech


def test_all_repeat_forms_are_the_same_automation(monkeypatch, tmp_path):
    meanings = {
        _meaning(_create(monkeypatch, tmp_path / str(i), form.format(v="erinnere mich"))[1])
        for i, form in enumerate(_GATE_REPEAT)
    }
    assert len(meanings) == 1


def test_the_repetition_stops_when_the_gate_closes(monkeypatch, tmp_path):
    _, automation = _create(monkeypatch, tmp_path, _GATE_REPEAT[0].format(v="erinnere mich"))
    world = World({GATE: "open"})
    sent = run(automation["actions"], world, {25 * 60: lambda w: w.states.update({GATE: "closed"})})
    assert [item.at for item in sent] == [0, 600, 1200]
    assert {item.target for item in sent} == {(PHILIPP,)}
    assert {item.message for item in sent} == {"Das Garagentor ist noch offen."}


def test_the_upper_bound_holds_when_nobody_closes_it(monkeypatch, tmp_path):
    _, automation = _create(monkeypatch, tmp_path, _GATE_REPEAT[0].format(v="erinnere mich"))
    sent = run(automation["actions"], World({GATE: "open"}))
    assert len(sent) == 12 and sent[-1].at == 11 * 600


@pytest.mark.parametrize("answer,trigger_kind", [("Nur jetzt.", "time"), ("Jedes Mal.", "state")])
def test_without_an_event_it_asks_now_or_every_time(monkeypatch, tmp_path, answer, trigger_kind):
    house = _house(monkeypatch, tmp_path)
    question = house.say("Erinnere mich alle 10 Minuten, bis das Garagentor zu ist.").speech
    assert question == "Nur jetzt oder jedes Mal, wenn das Garagentor geöffnet wird?"
    assert house.automations() == []
    preview = house.say(answer).speech
    assert "Soll ich das so einrichten" in preview
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["trigger"] == trigger_kind
    # Already closed: the repetition never sends anything.
    assert run(automation["actions"], World({GATE: "closed"})) == []


_ESCALATION = [
    "Melde dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten immer noch offen ist, sag Anna Bescheid.",
    "Wenn die Haustür aufgeht, sag mir Bescheid, und wenn sie nach 15 Minuten noch offen ist, benachrichtige Anna.",
    "Sag mir Bescheid, wenn die Haustür geöffnet wird, und falls sie nach 15 Minuten immer noch offen steht, informiere Anna.",
]


@pytest.mark.parametrize("text", _ESCALATION)
def test_escalation_to_anna(monkeypatch, tmp_path, text):
    speech, automation = _create(monkeypatch, tmp_path, text)
    assert automation["triggers"] == [{"trigger": "state", "entity_id": DOOR, "to": "on"}]
    assert "Ist die Haustür nach 15 Minuten immer noch offen" in speech and "Handy Anna" in speech
    # Still open after 15 minutes: me at once, Anna after 15 minutes.
    sent = run(automation["actions"], World({DOOR: "on"}))
    assert [(item.at, item.target) for item in sent] == [(0, (PHILIPP,)), (900, (ANNA,))]
    assert sent[1].message == "Die Haustür ist seit 15 Minuten offen."
    # Closed after 10 minutes: Anna gets nothing.
    closes = {600: lambda w: w.states.update({DOOR: "off"})}
    sent = run(automation["actions"], World({DOOR: "on"}), closes)
    assert [(item.at, item.target) for item in sent] == [(0, (PHILIPP,))]


def test_all_escalation_forms_are_the_same_automation(monkeypatch, tmp_path):
    meanings = {
        _meaning(_create(monkeypatch, tmp_path / str(i), text)[1]) for i, text in enumerate(_ESCALATION)
    }
    assert len(meanings) == 1


def test_an_unknown_second_recipient_asks(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say(
        "Melde dich, wenn die Haustür offen ist, und wenn sie nach 15 Minuten noch offen ist, sag Bernd Bescheid."
    )
    assert "Bernd" in turn.speech and "Soll ich" not in turn.speech
    house.say("Ja.")
    assert house.automations() == []


def test_repeating_a_device_action_is_refused(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Wenn das Garagentor offen ist, schalte das Garagenlicht alle 10 Minuten ein, bis es zu ist.")
    assert "Soll ich" not in turn.speech
    house.say("Ja.")
    assert house.automations() == []


@pytest.mark.parametrize("interval,seconds", [
    ("jede Minute", 60), ("alle zwei Minuten", 120), ("stündlich", 3600), ("jede Stunde", 3600),
    ("alle 2 Stunden", 7200),
])
def test_interval_words(monkeypatch, tmp_path, interval, seconds):
    _, automation = _create(
        monkeypatch, tmp_path, f"Wenn das Garagentor offen ist, erinnere mich {interval}, bis es zu ist."
    )
    assert automation["actions"][0]["repeat"]["sequence"][-1] == {"delay": {"seconds": seconds}}


def test_an_interval_that_is_not_used_is_never_dropped(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Wenn das Garagentor offen ist, schalte jede Minute das Garagenlicht ein.")
    assert "Soll" not in turn.speech
    house.say("Ja.")
    assert house.automations() == []
