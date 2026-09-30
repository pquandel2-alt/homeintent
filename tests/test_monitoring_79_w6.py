"""7.9 W6: a monitoring request over two turns, and pronouns in device actions.

"Überwache das Garagentor." opens a dialog with exactly this object; the
next utterance of the same user in the same conversation is read with the
object as antecedent.  The dialog expires (dialog TTL), "Abbrechen" ends it,
and any other complete request ends it without side effects.  A pronoun in
a device action is bound like the event's pronoun: same antecedent, same
gender check; locks are never switched by an automation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from _testhaus import PUSH_OPTIONS, HouseConversation

GATE = "cover.garagentor"


def _house(monkeypatch, tmp_path, user: str = "admin") -> HouseConversation:
    return HouseConversation(monkeypatch, tmp_path=tmp_path, options=PUSH_OPTIONS, user=user)


_OPEN = (
    "Überwache das Garagentor.", "Beobachte das Garagentor.", "Behalte das Garagentor im Auge.",
    "Hab ein Auge auf das Garagentor.", "Kannst du das Garagentor überwachen?",
)
_ANSWERS = {
    "Wenn es länger als 10 Minuten offen ist.": {"to": "open", "for": {"seconds": 600}},
    "Melde dich, wenn es länger als 10 Minuten offen ist.": {"to": "open", "for": {"seconds": 600}},
    "Sag mir Bescheid, sobald es aufgeht.": {"to": "open"},
    "Wenn es offen ist, warne mich.": {"to": "open"},
}


@pytest.mark.parametrize("answer", list(_ANSWERS))
@pytest.mark.parametrize("opening", _OPEN)
def test_the_event_arrives_in_the_next_turn(monkeypatch, tmp_path, opening, answer):
    house = _house(monkeypatch, tmp_path)
    question = house.say(opening)
    assert question.speech.startswith("Wann soll ich mich zu „Garagentor“ melden?"), question.speech
    assert house.automations() == []
    preview = house.say(answer)
    assert "Soll ich das so einrichten" in preview.speech, preview.speech
    assert house.automations() == []
    house.say("Ja.")
    [automation] = house.automations()
    [trigger] = automation["triggers"]
    assert trigger["entity_id"] == GATE
    assert {key: trigger[key] for key in ("to", "for") if key in trigger} == _ANSWERS[answer]


def test_cancel_ends_the_open_request(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Überwache das Garagentor.")
    assert "verworfen" in house.say("Abbrechen.").speech
    house.say("Wenn es offen ist.")
    house.say("Ja.")
    assert house.automations() == []


def test_another_request_ends_it_without_side_effect(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Überwache das Garagentor.")
    turn = house.say("Schalte das Flurlicht ein.")
    assert turn.targets == {"light.flurlicht"}
    # The open request is gone: a later bare event is not bound to it.
    house.say("Wenn es offen ist.")
    house.say("Ja.")
    assert house.automations() == []


def test_another_user_does_not_complete_it(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Überwache das Garagentor.")
    house.user = "anna"
    house.say("Wenn es offen ist.")
    house.say("Ja.")
    assert house.automations() == []


def test_the_open_request_expires(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    house.say("Überwache das Garagentor.")
    manager = house.entity._runtime_data.dialog_manager
    tasks = manager._tasks[house.conversation_id]
    for task_id, task in list(tasks.items()):
        tasks[task_id] = replace(task, expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    house.say("Wenn es offen ist.")
    house.say("Ja.")
    assert house.automations() == []


def test_an_unknown_object_is_named(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    assert "Blumentopf" in house.say("Überwache den Blumentopf.").speech
    house.say("Wenn er umfällt.")
    house.say("Ja.")
    assert house.automations() == []


# --- pronouns in device actions ------------------------------------------------------


@pytest.mark.parametrize("text,trigger,action_entity,service", [
    ("Überwache das Garagentor und schließ es, wenn es länger als 10 Minuten offen ist.",
     GATE, GATE, "cover.close_cover"),
    ("Überwache die Stehlampe und schalte sie aus, wenn sie länger als 2 Stunden an ist.",
     "light.stehlampe", "light.stehlampe", "homeassistant.turn_off"),
    ("Beobachte den Deckenfluter und mach ihn aus, wenn er länger als eine Stunde an ist.",
     "light.deckenfluter_buero", "light.deckenfluter_buero", "homeassistant.turn_off"),
])
def test_a_pronoun_in_the_action_is_the_monitored_object(monkeypatch, tmp_path, text, trigger, action_entity, service):
    house = _house(monkeypatch, tmp_path)
    preview = house.say(text)
    assert "Soll diese Automation erstellt werden" in preview.speech, preview.speech
    house.say("Ja.")
    [automation] = house.automations()
    assert automation["triggers"][0]["entity_id"] == trigger
    [action] = automation["actions"]
    assert action["action"] == service
    assert action["target"]["entity_id"] in (action_entity, [action_entity])


def test_a_pronoun_with_the_wrong_gender_is_not_bound(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Überwache das Garagentor und schließ ihn, wenn es länger als 10 Minuten offen ist.")
    assert "Soll" not in turn.speech
    house.say("Ja.")
    assert house.automations() == []


def test_locks_are_never_switched_by_an_automation(monkeypatch, tmp_path):
    house = _house(monkeypatch, tmp_path)
    turn = house.say("Überwache die Haustür und schließ sie ab, wenn sie offen ist.")
    assert "Schlösser" in turn.speech and "bestätigst du" in turn.speech
    house.say("Ja.")
    assert house.automations() == [] and turn.calls == []
