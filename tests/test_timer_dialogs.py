"""Named Assist timers: ask for a name, pick among several, confirm clearing."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.native_timer import (  # noqa: E402
    NativeTimerInfo,
    describe_timers,
    match_timer_name,
)
from homeintent.productivity import (  # noqa: E402
    TimerOperation,
    parse_timer_request,
    timer_name_reply,
)
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

NUDELN = NativeTimerInfo("Nudeln", 250, True, start_minutes=5)
PIZZA = NativeTimerInfo("Pizza", 600, True, start_minutes=12)


class FakeTimers:
    """Records what the agent asks Home Assistant's timer manager to do."""

    def __init__(self, timers=()):
        self.timers = tuple(timers)
        self.async_ensure_audible = AsyncMock(return_value=None)
        self.async_execute = AsyncMock(side_effect=self._execute)
        self.async_cancel_all = AsyncMock(side_effect=lambda user_input: len(self.timers))
        self.restart_note = None

    async def async_list_timers(self, user_input):
        return self.timers

    def consume_restart_note(self):
        return self.restart_note

    async def _execute(self, request, user_input, *, target=None):
        name = target.name if target is not None else request.name
        return f"{request.operation.name}:{name}"


def _agent(monkeypatch, timers=()):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: [])
    fake = FakeTimers(timers)
    agent._runtime_data.native_timer = fake
    return agent, fake


def _say(agent, text, conversation_id="timer"):
    return asyncio.run(agent._async_handle_message(
        ConversationInput(text=text, conversation_id=conversation_id), chat_log=None
    ))


# --- language ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "operation", "name"),
    (
        ("Lösche den Timer Nudeln.", TimerOperation.CANCEL, "Nudeln"),
        ("Beende den Timer Pizza.", TimerOperation.FINISH, "Pizza"),
        ("Brich den Pizza-Timer ab.", TimerOperation.CANCEL, "Pizza"),
        ("Lösche den Nudeltimer.", TimerOperation.CANCEL, "Nudel"),
        ("Wie lange läuft der Timer Pizza noch?", TimerOperation.STATUS, "Pizza"),
        ("Pausiere den Timer Pizza.", TimerOperation.PAUSE, "Pizza"),
        ("Setze den Nudeltimer fort.", TimerOperation.RESUME, "Nudel"),
        ("Stelle einen Nudeltimer für 8 Minuten.", TimerOperation.START, "Nudel"),
        ("Stoppe den Timer.", TimerOperation.CANCEL, None),
        ("Starte einen Timer über 3 Minuten.", TimerOperation.START, None),
        ("Welche Timer laufen?", TimerOperation.LIST, None),
        ("Lösche alle Timer.", TimerOperation.CANCEL_ALL, None),
        ("Brich alle Timer ab.", TimerOperation.CANCEL_ALL, None),
    ),
)
def test_timer_language_extracts_operation_and_name(text, operation, name):
    request = parse_timer_request(text, [])

    assert request is not None
    assert request.operation is operation
    assert request.name == name


@pytest.mark.parametrize(
    ("reply", "name"),
    (
        ("Pizza", "Pizza"),
        ("Der Timer heißt Tee.", "Tee"),
        ("Er soll Eier heißen", "Eier"),
        ("Ohne Namen", ""),
        ("egal", ""),
        (".", None),
    ),
)
def test_timer_name_reply(reply, name):
    assert timer_name_reply(reply) == name


def test_spoken_name_matches_running_timer_loosely():
    assert match_timer_name("Nudel", (NUDELN, PIZZA)) == (NUDELN,)
    assert match_timer_name("pizza", (NUDELN, PIZZA)) == (PIZZA,)
    assert match_timer_name("Tee", (NUDELN, PIZZA)) == ()


def test_unnamed_timer_is_targeted_by_its_start_duration():
    unnamed = NativeTimerInfo("", 100, True, start_minutes=3)

    assert unnamed.label == "Timer über 3 Minuten"
    assert unnamed.target_slots() == {"start_minutes": {"value": 3}}
    assert NUDELN.target_slots() == {"name": {"value": "Nudeln"}}


def test_timer_overview_names_every_timer():
    assert describe_timers((NUDELN, PIZZA)) == (
        "2 Timer laufen. Nudeln: noch 4 Minuten und 10 Sekunden; "
        "Pizza: noch 10 Minuten."
    )


# --- starting ----------------------------------------------------------------


def test_timer_without_name_asks_for_one_and_keeps_listening(monkeypatch):
    agent, fake = _agent(monkeypatch)

    question = _say(agent, "Stelle einen Timer für 5 Minuten.")
    started = _say(agent, "Nudeln")

    assert question.response.speech == "Wie soll der Timer heißen?"
    assert question.continue_conversation is True
    assert started.response.speech == "START:Nudeln"
    request = fake.async_execute.await_args.args[0]
    assert request.duration_seconds == 300


def test_timer_can_be_started_without_name_on_request(monkeypatch):
    agent, fake = _agent(monkeypatch)
    _say(agent, "Stelle einen Timer für 5 Minuten.")

    started = _say(agent, "Ohne Namen")

    assert started.response.speech == "START:None"


def test_duplicate_timer_name_is_asked_again(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN,))

    duplicate = _say(agent, "Stelle einen Timer für 8 Minuten mit dem Namen Nudeln.")
    started = _say(agent, "Spaghetti")

    assert duplicate.response.speech == (
        "Es läuft schon ein Timer Nudeln. Wie soll der neue Timer heißen?"
    )
    assert started.response.speech == "START:Spaghetti"


def test_name_question_can_be_cancelled(monkeypatch):
    agent, fake = _agent(monkeypatch)
    _say(agent, "Stelle einen Timer für 5 Minuten.")

    cancelled = _say(agent, "Abbrechen")

    assert cancelled.continue_conversation is False
    fake.async_execute.assert_not_awaited()
    # The dialog is closed: a new name is not taken as an answer any more.
    after = _say(agent, "Nudeln")
    fake.async_execute.assert_not_awaited()
    assert after.response.speech != "START:Nudeln"


# --- addressing one of several timers -----------------------------------------


def test_timer_is_deleted_by_a_loosely_spoken_name(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    result = _say(agent, "Lösche den Nudeltimer.")

    assert result.response.speech == "CANCEL:Nudeln"
    assert fake.async_execute.await_args.kwargs["target"] == NUDELN


def test_several_timers_without_name_ask_which_one(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    question = _say(agent, "Stoppe den Timer.")
    answer = _say(agent, "Pizza")

    assert question.response.speech == "Welchen Timer meinst du: Nudeln oder Pizza?"
    assert question.continue_conversation is True
    assert answer.response.speech == "CANCEL:Pizza"


def test_timer_choice_accepts_an_ordinal(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))
    _say(agent, "Pausiere den Timer.")

    answer = _say(agent, "den ersten")

    assert answer.response.speech == "PAUSE:Nudeln"


def test_single_timer_needs_no_name(monkeypatch):
    agent, fake = _agent(monkeypatch, (PIZZA,))

    result = _say(agent, "Brich den Timer ab.")

    assert result.response.speech == "CANCEL:Pizza"


def test_unknown_timer_name_lists_the_running_timers(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    result = _say(agent, "Lösche den Timer Tee.")

    assert result.response.speech.startswith("Ich finde keinen Timer Tee. 2 Timer laufen.")
    fake.async_execute.assert_not_awaited()


def test_no_running_timer_is_reported(monkeypatch):
    agent, fake = _agent(monkeypatch)

    result = _say(agent, "Lösche den Timer Pizza.")

    assert result.response.speech == "Es läuft kein Timer."


# --- overview and clearing ------------------------------------------------------


def test_which_timers_are_running(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    result = _say(agent, "Welche Timer laufen?")

    assert result.response.speech == describe_timers((NUDELN, PIZZA))


def test_remaining_time_of_a_named_timer(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    result = _say(agent, "Wie lange läuft der Timer Pizza noch?")

    assert result.response.speech == "Pizza: noch 10 Minuten."


def test_delete_all_timers_asks_first(monkeypatch):
    agent, fake = _agent(monkeypatch, (NUDELN, PIZZA))

    question = _say(agent, "Lösche alle Timer.")
    declined = _say(agent, "Nein")

    assert question.response.speech == "Soll ich wirklich alle 2 Timer löschen?"
    assert question.continue_conversation is True
    assert declined.response.speech == "Abgebrochen. Die Timer laufen weiter."
    fake.async_cancel_all.assert_not_awaited()

    _say(agent, "Lösche alle Timer.")
    confirmed = _say(agent, "Ja")

    assert confirmed.response.speech == "Alle 2 Timer wurden gelöscht."
    fake.async_cancel_all.assert_awaited_once()


def test_new_timer_command_while_naming_is_executed_not_used_as_name(monkeypatch):
    agent, fake = _agent(monkeypatch, (PIZZA,))
    _say(agent, "Stelle einen Timer für 3 Minuten.")

    result = _say(agent, "Pausiere den Timer Pizza.")

    assert result.response.speech == "PAUSE:Pizza"
    assert fake.async_execute.await_args.args[0].operation is TimerOperation.PAUSE


def test_question_while_naming_is_not_taken_as_name(monkeypatch):
    agent, fake = _agent(monkeypatch, (PIZZA,))
    _say(agent, "Stelle einen Timer für 3 Minuten.")

    result = _say(agent, "Wie lange läuft der Timer Pizza noch?")

    assert result.response.speech == "Pizza: noch 10 Minuten."
    assert timer_name_reply("Wie heißt er?") is None


def test_timer_status_after_restart_names_lost_timers(monkeypatch):
    """F19: timers lost to a Home Assistant restart are not silently gone."""
    agent, fake = _agent(monkeypatch)
    fake.restart_note = (
        "Hinweis: Durch den Neustart von Home Assistant ist der Timer „Nudeln“ "
        "(wäre um 18:05 Uhr abgelaufen) verloren gegangen. Bitte stelle ihn bei Bedarf neu."
    )
    speech = _say(agent, "Welche Timer laufen?").response.speech
    assert "Nudeln" in speech and "Neustart" in speech
    assert "Es läuft kein Timer." in speech


def test_restart_note_wording():
    from datetime import datetime, timezone

    from homeintent.native_timer import LostTimer, restart_note

    now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
    assert restart_note([], now) is None
    note = restart_note([LostTimer("Nudeln", datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc))], now)
    assert note is not None and "„Nudeln“ (ist inzwischen abgelaufen)" in note
