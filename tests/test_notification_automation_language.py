"""Notification clause semantics inside automations (spec §6, §19-§23, §52)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

from _automation_language_helpers import ENGINE, ENTITIES, meaning, question  # noqa: E402
from homeintent.engine import AutomationMatchResult  # noqa: E402
from homeintent.nlu.action_model import NotificationRecipientKind  # noqa: E402


@pytest.mark.parametrize(("sentence", "recipient"), (
    ("Benachrichtige mich, wenn die Haustür aufgeht.", "me"),
    ("Schick mir eine Nachricht, wenn die Haustür aufgeht.", "me"),
    ("Benachrichtige uns, wenn die Haustür aufgeht.", "us"),
    ("Schick Julia eine Nachricht, wenn die Haustür aufgeht.", "julia"),
    ("Sag Julia Bescheid, sobald die Haustür aufgeht.", "julia"),
))
def test_recipients(sentence, recipient):
    assert meaning(sentence) == f"state(binary_sensor.haustuer)=open => notify({recipient})"


@pytest.mark.parametrize("sentence", (
    "Schick mir per Push eine Nachricht, wenn die Haustür aufgeht.",
    "Schick mir aufs Handy eine Nachricht, wenn die Haustür aufgeht.",
    "Schick mir auf mein iPhone eine Nachricht, wenn die Haustür aufgeht.",
    "Schick mir als Push-Nachricht Bescheid, wenn die Haustür aufgeht.",
    "Benachrichtige mich per Benachrichtigung, wenn die Haustür aufgeht.",
))
def test_channel_words_do_not_change_the_meaning(sentence):
    assert meaning(sentence) == "state(binary_sensor.haustuer)=open => notify(me)"


def test_derived_message_describes_the_event():
    result = ENGINE.match_automation("Benachrichtige mich, wenn die Rolllade im Büro 50 Prozent erreicht.", ENTITIES)
    assert isinstance(result, AutomationMatchResult)
    [action] = result.model.actions
    assert action.message == "Der Rollladen im Büro hat 50 % erreicht."
    assert action.recipient is not None and action.recipient.kind is NotificationRecipientKind.CURRENT_USER


@pytest.mark.parametrize(("sentence", "message"), (
    ("Wenn die Rolllade im Büro 50 Prozent erreicht, schick mir die Nachricht: Rollladen ist halb unten.",
     "Rollladen ist halb unten"),
    ("Benachrichtige mich, sobald das Bürofenster geöffnet wird, mit dem Text: Fenster im Büro offen.",
     "Fenster im Büro offen."),
    ("Wenn das Fenster aufgeht, schick mir die Nachricht: Mach alle Lichter aus.", "Mach alle Lichter aus"),
    ("Wenn die Haustür aufgeht, schick mir die Nachricht: Wenn es regnet, schließe das Fenster.",
     "Wenn es regnet, schließe das Fenster"),
))
def test_explicit_message_text_is_inert(sentence, message):
    result = ENGINE.match_automation(sentence, ENTITIES)
    assert isinstance(result, AutomationMatchResult), result
    [action] = result.model.actions
    assert action.message == message


def test_unknown_named_recipient_is_asked_not_guessed():
    assert "Tante Erna" in question("Benachrichtige Tante Erna, wenn die Haustür aufgeht.")


@pytest.mark.parametrize("sentence", (
    "Wie kann ich eine Benachrichtigung erstellen?",
    "Kannst du Benachrichtigungen schicken?",
    "Warum wurde ich nicht benachrichtigt?",
    "Bekomme ich eine Nachricht, wenn die Haustür aufgeht?",
    "Meine Rolllade steht bei 50 Prozent.",
    "Ich bekomme keine Benachrichtigung wenn die Rolllade 50 Prozent erreicht.",
    "Benachrichtige mich nicht, wenn die Haustür aufgeht.",
))
def test_questions_statements_and_negations_are_no_automation(sentence):
    assert not isinstance(ENGINE.match_automation(sentence, ENTITIES), AutomationMatchResult)


def test_immediate_notification_is_not_an_automation():
    assert ENGINE.match_automation("Schick mir eine Testbenachrichtigung.", ENTITIES) is None
    assert ENGINE.match_immediate_notification("Schick mir eine Testbenachrichtigung.") is not None
    assert ENGINE.match_immediate_notification("Schick mir eine Nachricht, wenn die Haustür aufgeht.") is None
