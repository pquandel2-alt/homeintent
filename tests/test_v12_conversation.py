"""V12 through the real conversation.py: dialog authority and collisions (28, 61-63)."""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from v12_harness import (
    build_world,
    exact_room,
    garage_states,
    living_satellite,
    philipp,
)

import homeintent.conversation as ha_conversation
from homeintent.automation_executor import AutomationExecutor
from homeintent.conversation import NluConversationEntity
from homeintent.native_timer import NativeTimerInfo
from homeintent.proactive_dialog import ProactiveDialogHandler
from homeintent.proactive_engine import ProactiveConfig
from homeintent.proactive_model import ProposalState, SituationKind
from homeassistant.components.conversation import ConversationInput
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

NUDELN = NativeTimerInfo("Nudeln", 250, True, start_minutes=5)
PIZZA = NativeTimerInfo("Pizza", 600, True, start_minutes=12)
DEVICE_DOMAINS = {"light", "switch", "cover", "lock", "homeassistant", "climate", "fan"}


class FakeTimers:
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


class Shim:
    """The runtime surface conversation.py uses, backed by the real engine."""

    def __init__(self, engine, dialogs):
        self.engine = engine
        self.enabled = engine.config.enabled
        self.dialogs = ProactiveDialogHandler(engine, dialogs)
        self.turns: list[tuple[str | None, str | None]] = []

    def record_authenticated_turn(self, user_id, device_id):
        self.turns.append((user_id, device_id))


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch, *, timers=(), config=None):
        self.world = build_world(
            tmp_path, garage_states(), config=config,
            recipients={"philipp": philipp()},
            rooms={"person.philipp": exact_room("person.philipp", "living_room")},
            satellite_records=[living_satellite()],
            household={"person.philipp": "home", "person.anna": "not_home"},
        )
        agent = NluConversationEntity(ConfigEntry())
        agent.hass = HomeAssistant()
        agent.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
        agent.hass.auth = SimpleNamespace(async_get_user=AsyncMock(
            side_effect=lambda user_id: SimpleNamespace(is_admin=user_id == "philipp", name=user_id)
        ))
        monkeypatch.setattr(
            ha_conversation, "build_entity_snapshots",
            lambda *_: list(self.world.ports.states.values()),
        )
        monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])
        monkeypatch.setattr(AutomationExecutor, "_assign_homeintent_category", lambda *_args: None)
        self.timers = FakeTimers(timers)
        agent._runtime_data.native_timer = self.timers
        self.shim = Shim(self.world.engine, agent._runtime_data.dialog_manager)
        agent._runtime_data.proactive_context = self.shim
        self.agent = agent

    def say(self, text, *, user="philipp", conversation_id="conv", device_id=None):
        return asyncio.run(self.agent._async_handle_message(
            ConversationInput(
                text=text, conversation_id=conversation_id,
                context=SimpleNamespace(user_id=user), device_id=device_id,
            ),
            None,
        ))

    def open_garage_proposal(self):
        async def scenario():
            await self.world.change("cover.garage", "open")
            await self.world.ports.advance(timedelta(minutes=15))
        asyncio.run(scenario())
        proposal_id = self.world.ports.delivered[-1].proposal_id
        assert proposal_id is not None
        return proposal_id

    def proposal_state(self, proposal_id):
        return self.world.engine.proposals.get(proposal_id).state

    def agent_device_calls(self):
        return [
            call.args[:2] for call in self.agent.hass.services.async_call.await_args_list
            if call.args and call.args[0] in DEVICE_DOMAINS
        ]


# --- basic proposal answer through the conversation agent -------------------

def test_voice_yes_through_conversation_executes_the_garage_proposal(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    proposal_id = h.open_garage_proposal()
    result = h.say("Ja", device_id="dev_living")
    assert "geschlossen" in result.response.speech
    assert result.continue_conversation is False
    assert h.world.sink.device_calls == [("cover", "close_cover", {"entity_id": "cover.garage"})]
    assert h.proposal_state(proposal_id) is ProposalState.EXECUTED
    assert h.agent_device_calls() == []
    assert h.shim.turns[-1] == ("philipp", "dev_living")


def test_multiple_proposals_clarify_through_conversation(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.world.ports.household["person.philipp"] = "not_home"

    async def scenario():
        await h.world.change("cover.garage", "open")
        await h.world.change("light.kitchen", "on")
        await h.world.ports.advance(timedelta(minutes=15))

    asyncio.run(scenario())
    question = h.say("Ja")
    assert question.response.speech == "Meinst du das Licht in der Küche oder die Garage?"
    assert question.continue_conversation is True
    assert h.world.sink.device_calls == []
    answer = h.say("die Garage")
    assert "geschlossen" in answer.response.speech
    assert h.world.sink.device_calls == [("cover", "close_cover", {"entity_id": "cover.garage"})]


def test_clarification_no_keeps_both_proposals(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.world.ports.household["person.philipp"] = "not_home"

    async def scenario():
        await h.world.change("cover.garage", "open")
        await h.world.change("light.kitchen", "on")
        await h.world.ports.advance(timedelta(minutes=15))

    asyncio.run(scenario())
    h.say("Ja")
    declined = h.say("Nein")
    assert "nichts ausgeführt" in declined.response.speech
    assert all(item.state is ProposalState.PENDING for item in h.world.engine.proposals.all())
    assert h.world.sink.device_calls == []


def test_wrong_user_on_same_satellite_cannot_confirm(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    proposal_id = h.open_garage_proposal()
    result = h.say("Ja", user="anna", device_id="dev_living")
    assert "anderen Benutzer" in result.response.speech
    assert h.world.sink.device_calls == []
    assert h.proposal_state(proposal_id) is ProposalState.PENDING


# --- 61 timer collisions --------------------------------------------------------

def test_timer_status_question_is_not_taken_by_pending_proposal(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, timers=(NUDELN, PIZZA))
    proposal_id = h.open_garage_proposal()
    result = h.say("Wie lange läuft der Nudeltimer noch?")
    assert result.response.speech == "Nudeln: noch 4 Minuten und 10 Sekunden."
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert h.world.sink.device_calls == [] and h.agent_device_calls() == []


def test_timer_ambiguity_ordinal_goes_to_timer_not_proposal(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, timers=(NUDELN, PIZZA))
    proposal_id = h.open_garage_proposal()
    question = h.say("Stoppe den Timer.")
    assert question.response.speech == "Welchen Timer meinst du: Nudeln oder Pizza?"
    answer = h.say("den ersten")
    assert answer.response.speech == "CANCEL:Nudeln"
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert h.world.sink.device_calls == []


def test_timer_naming_reply_is_not_a_proposal_answer(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    proposal_id = h.open_garage_proposal()
    question = h.say("Stell einen Timer auf 10 Minuten.")
    assert question.response.speech == "Wie soll der Timer heißen?"
    named = h.say("Nudeln")
    assert "Nudeln" in named.response.speech
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert h.world.sink.device_calls == []


def test_timer_delete_all_confirmation_owns_the_yes(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, timers=(NUDELN, PIZZA))
    proposal_id = h.open_garage_proposal()
    question = h.say("Lösche alle Timer.")
    assert question.response.speech == "Soll ich wirklich alle 2 Timer löschen?"
    confirmed = h.say("Ja")
    assert confirmed.response.speech == "Alle 2 Timer wurden gelöscht."
    h.timers.async_cancel_all.assert_awaited_once()
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert h.world.sink.device_calls == []
    assert h.world.engine.permissions.all() == ()


def test_timer_delete_all_no_keeps_timers_and_proposal(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, timers=(NUDELN, PIZZA))
    proposal_id = h.open_garage_proposal()
    h.say("Lösche alle Timer.")
    declined = h.say("Nein")
    assert declined.response.speech == "Abgebrochen. Die Timer laufen weiter."
    h.timers.async_cancel_all.assert_not_awaited()
    assert h.proposal_state(proposal_id) is ProposalState.PENDING


def test_timer_expiry_is_announced_exactly_once(tmp_path):
    from homeintent.native_timer import NativeTimerRuntime

    world = build_world(tmp_path, garage_states(), recipients={"philipp": philipp()},
                        household={"person.philipp": "home"})
    hass = HomeAssistant()
    calls: list[tuple[str, str]] = []

    async def record(domain, service, data, blocking=False):
        calls.append((domain, service))

    hass.services.async_call = ServiceMock(side_effect=record)
    tasks: list = []
    hass.async_create_task = lambda coro, name=None: tasks.append(coro)
    entry = ConfigEntry()
    entry.options = {
        "agent_tts_entity": "tts.local", "agent_media_players": ["media_player.kitchen"],
        "timer_chime_media_id": "media-source://media_source/local/chime.mp3",
    }
    entry.runtime_data = SimpleNamespace(proactive_context=SimpleNamespace(
        record_timer_finished=lambda label: world.engine.record_external_communication(
            SituationKind.TIMER_FINISHED, label, owner="native_timer"),
    ))
    runtime = NativeTimerRuntime(hass, entry)
    runtime._handle_timer_event("finished", SimpleNamespace(name="Nudeln"))

    async def drain():
        for coro in tasks:
            await coro

    asyncio.run(drain())
    assert calls == [("tts", "speak"), ("media_player", "play_media")]
    speak = hass.services.async_call.await_args_list[0].args[2]
    assert speak["message"] == "Der Timer Nudeln ist abgelaufen."
    assert world.ports.delivered == []
    record_ = world.engine.history.records()[-1]
    assert record_.situation_kind is SituationKind.TIMER_FINISHED
    assert record_.result == "delivered_by_native_timer"


# --- 62 automation collisions -----------------------------------------------------

def test_automation_request_is_not_a_standing_permission(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    proposal_id = h.open_garage_proposal()
    preview = h.say("Wenn es dunkel wird, schalte das Küchenlicht ein.")
    assert "Sonne" in preview.response.speech or "dunkel" in preview.response.speech.casefold()
    assert h.world.engine.permissions.all() == ()
    declined = h.say("Nein")
    assert "nicht" in declined.response.speech.casefold()
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert h.world.engine.permissions.all() == ()
    assert not (tmp_path / "automations.yaml").exists() or "Küchenlicht" not in (tmp_path / "automations.yaml").read_text()


def test_unsupported_trailing_text_saves_neither_automation_nor_permission(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    result = h.say(
        "Wenn niemand zuhause ist und im Wohnzimmer noch Licht an ist, darfst du es automatisch "
        "ausschalten und außerdem den Rasen mähen."
    )
    assert "nichts gespeichert" in result.response.speech or "weder" in result.response.speech
    assert h.world.engine.permissions.all() == ()
    assert not (tmp_path / "automations.yaml").exists()


# --- 63 security dialog collision ------------------------------------------------------

def test_security_confirmation_owns_the_yes(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    proposal_id = h.open_garage_proposal()
    question = h.say("Entriegle die Haustür.")
    assert "?" in question.response.speech
    assert question.continue_conversation is True
    answer = h.say("Ja")
    assert ("lock", "unlock") in h.agent_device_calls()
    assert h.world.sink.device_calls == []
    assert h.proposal_state(proposal_id) is ProposalState.PENDING
    assert "Garage" not in answer.response.speech


def test_security_no_cancels_only_the_security_action(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    proposal_id = h.open_garage_proposal()
    h.say("Entriegle die Haustür.")
    h.say("Nein")
    assert h.agent_device_calls() == []
    assert h.proposal_state(proposal_id) is ProposalState.PENDING


# --- standing permission dialog ----------------------------------------------------------

PERMISSION = (
    "Wenn niemand zuhause ist und im Wohnzimmer noch Licht an ist, "
    "darfst du es automatisch ausschalten."
)


def test_standing_permission_requires_preview_and_explicit_yes(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    preview = h.say(PERMISSION)
    assert preview.response.speech.startswith("Vorschau: Wenn niemand zu Hause ist und im Wohnzimmer")
    assert "schalte ich Wohnzimmerlicht und Stehlampe automatisch aus" in preview.response.speech
    assert preview.continue_conversation is True
    assert h.world.engine.permissions.all() == ()
    saved = h.say("Ja")
    assert saved.response.speech.startswith("Gespeichert.")
    permissions = h.world.engine.permissions.all()
    assert len(permissions) == 1
    permission = permissions[0]
    assert permission.owner_user_id == "philipp"
    assert permission.entity_ids == ("light.living", "light.living_floor")
    assert permission.confirmed and not permission.revoked
    listed = h.say("Welche Daueranweisungen gibt es?")
    assert listed.response.speech.startswith("1 Daueranweisung aktiv")
    assert listed.response.response_type == "query_answer"
    revoked = h.say("Widerrufe alle Daueranweisungen.")
    assert revoked.response.speech == "Ich habe 1 Daueranweisung widerrufen."
    assert h.world.engine.permissions.active(h.world.ports.clock) == ()


def test_confirming_the_same_standing_permission_twice_keeps_one(tmp_path, monkeypatch):
    """F7: a repeated confirmation renews the instruction instead of duplicating it."""
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    h.say(PERMISSION)
    assert h.say("Ja").response.speech.startswith("Gespeichert.")
    h.say(PERMISSION)
    again = h.say("Ja")
    assert again.response.speech.startswith("Diese Daueranweisung gab es schon")
    assert len(h.world.engine.permissions.all()) == 1
    for question in ("Was darfst du ohne Rückfrage?", "Was machst du automatisch?"):
        assert h.say(question).response.speech.startswith("1 Daueranweisung aktiv"), question


def test_standing_permission_no_stores_nothing(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    h.say(PERMISSION)
    declined = h.say("Nein")
    assert declined.response.speech == "In Ordnung. Die Daueranweisung wurde nicht gespeichert."
    assert h.world.engine.permissions.all() == ()


@pytest.mark.parametrize("text", [
    "Wenn die Garage offen ist, darfst du sie automatisch schließen.",
    "Wenn ich nach Hause komme, darfst du die Haustür automatisch entriegeln.",
    "Du darfst das Garagentor automatisch zumachen.",
])
def test_never_auto_permission_requests_are_refused(tmp_path, monkeypatch, text):
    h = Harness(tmp_path, monkeypatch, config=ProactiveConfig(enabled=True, standing_permissions_enabled=True))
    result = h.say(text)
    assert "nie automatisch" in result.response.speech
    assert h.world.engine.permissions.all() == ()
    follow = h.say("Ja")
    assert h.world.engine.permissions.all() == ()
    assert h.world.sink.device_calls == [] and h.agent_device_calls() == []
    assert "Gespeichert" not in follow.response.speech


def test_permissions_disabled_by_default(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    result = h.say(PERMISSION)
    assert "deaktiviert" in result.response.speech
    assert h.world.engine.permissions.all() == ()


# --- explainability & history ---------------------------------------------------------------

def test_explain_and_history_from_stored_evidence(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.open_garage_proposal()
    why = h.say("Warum hast du mich wegen der Garage angesprochen?")
    text = why.response.speech
    assert "auf die Situation „Garage“ hingewiesen" in text
    assert "per Sprache im Raum" in text
    assert "15 Minuten" in text
    assert "Priorität: wichtig" in text
    assert "eindeutig in einem Raum" in text
    history = h.say("Welche Hinweise gab es heute?")
    assert history.response.speech == "Heute habe ich mich zu 1 Situation gemeldet: Garage."
    assert history.response.response_type == "query_answer"
    nothing = h.say("Warum hast du mich wegen der Waschmaschine angesprochen?")
    assert nothing.response.speech == "Dazu habe ich in letzter Zeit keinen Hinweis gegeben."


def test_mute_requires_explicit_confirmation(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.open_garage_proposal()
    question = h.say("Sag mir das künftig nicht mehr.")
    assert "Sicherheitswarnungen bleiben immer aktiv" in question.response.speech
    assert not h.world.engine.attention_state.is_muted("philipp", SituationKind.ENTRY_LEFT_OPEN)
    h.say("Ja")
    assert h.world.engine.attention_state.is_muted("philipp", SituationKind.ENTRY_LEFT_OPEN)
