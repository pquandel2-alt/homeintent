from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.automation_wizard import parse_lifetime, starts_automation_wizard  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


WINDOW = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
LIGHT = EntitySnapshot(
    "light.kueche", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
PERSON = EntitySnapshot("person.philipp", "Philipp", "person", "home")


def _agent(monkeypatch):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots",
        lambda hass, entry: [WINDOW, LIGHT, PERSON],
    )
    return agent


def _run(agent, text, cid="wizard"):
    return asyncio.run(agent._async_handle_message(
        ConversationInput(text=text, conversation_id=cid), chat_log=None
    ))


def test_wizard_start_and_lifetime_are_bounded():
    assert starts_automation_wizard("Erstelle eine neue Automation")
    assert parse_lifetime("dauerhaft") == (False, None)
    assert parse_lifetime("nur dreimal") == (False, 3)
    assert parse_lifetime("einmalig") == (True, None)


def test_complete_automation_can_be_composed_over_multiple_turns(monkeypatch):
    agent = _agent(monkeypatch)
    start = _run(agent, "Erstelle eine neue Automation")
    trigger = _run(agent, "Wenn das Küchenfenster geöffnet wird")
    decision = _run(agent, "ja")
    condition = _run(agent, "nur wenn jemand zuhause ist")
    action = _run(agent, "Schalte das Küchenlicht ein")
    preview = _run(agent, "dauerhaft")

    assert "Was soll die Automation auslösen" in start.response.speech
    assert "Bedingung" in trigger.response.speech
    assert "Welche Bedingung" in decision.response.speech
    assert "Was soll dann passieren" in condition.response.speech
    assert "dauerhaft" in action.response.speech
    assert "Automation erkannt" in preview.response.speech
    pending = agent._context_store.get("wizard")
    assert pending.pending_automation_confirmation is not None
    model = pending.pending_automation_confirmation.model
    assert len(model.triggers) == len(model.conditions) == len(model.actions) == 1


def test_wizard_can_be_cancelled_without_service_call(monkeypatch):
    agent = _agent(monkeypatch)
    _run(agent, "Erstelle eine Automation", "cancel")
    result = _run(agent, "Abbrechen", "cancel")
    assert "verworfen" in result.response.speech
    assert agent._context_store.get("cancel") is None
    agent.hass.services.async_call.assert_not_awaited()


def test_wizard_retries_every_unclear_stage_without_losing_state(monkeypatch):
    agent = _agent(monkeypatch)
    cid = "wizard-retries"

    _run(agent, "Erstelle eine neue Automation", cid)
    bad_trigger = _run(agent, "irgendwann vielleicht", cid)
    good_trigger = _run(agent, "Wenn das Küchenfenster geöffnet wird", cid)
    bad_decision = _run(agent, "weiß nicht", cid)
    _run(agent, "ja", cid)
    bad_condition = _run(agent, "nur unter guten Umständen", cid)
    _run(agent, "nur wenn jemand zuhause ist", cid)
    bad_action = _run(agent, "irgendetwas tun", cid)
    _run(agent, "Schalte das Küchenlicht ein", cid)
    bad_lifetime = _run(agent, "eine Weile", cid)
    preview = _run(agent, "dauerhaft", cid)

    assert "Auslöser" in bad_trigger.response.speech
    assert "Bedingung" in good_trigger.response.speech
    assert bad_decision.response.speech == "Bitte antworte mit Ja oder Nein."
    assert "Bedingung" in bad_condition.response.speech
    assert "Aktion" in bad_action.response.speech
    assert "Bitte sage dauerhaft" in bad_lifetime.response.speech
    assert "Automation erkannt" in preview.response.speech


def test_wizard_no_condition_path_reaches_action(monkeypatch):
    agent = _agent(monkeypatch)
    cid = "wizard-no-condition"

    _run(agent, "Erstelle eine neue Automation", cid)
    _run(agent, "Wenn das Küchenfenster geöffnet wird", cid)
    action_question = _run(agent, "nein", cid)

    assert action_question.response.speech == "Was soll dann passieren?"


def test_no_condition_answers_the_condition_question(monkeypatch):
    """F10: "Keine Bedingung" is a no, not an endless "Ja oder Nein" loop."""
    for reply in ("Keine Bedingung.", "Ohne Bedingung", "keine"):
        agent = _agent(monkeypatch)
        _run(agent, "Erstelle eine neue Automation")
        _run(agent, "Wenn das Küchenfenster geöffnet wird")
        answer = _run(agent, reply)
        assert "Was soll dann passieren" in answer.response.speech, reply


def test_universal_cancel_without_open_question_is_answered(monkeypatch):
    agent = _agent(monkeypatch)
    answer = _run(agent, "Abbrechen.")
    assert answer.response.speech == "Es ist gerade nichts offen, das ich abbrechen könnte."
    agent.hass.services.async_call.assert_not_awaited()


def test_universal_cancel_ends_the_wizard(monkeypatch):
    agent = _agent(monkeypatch)
    _run(agent, "Erstelle eine neue Automation")
    _run(agent, "Wenn das Küchenfenster geöffnet wird")
    answer = _run(agent, "Vergiss es.")
    assert "verworfen" in answer.response.speech
    assert agent._context_store.get("wizard") is None or (
        agent._context_store.get("wizard").pending_automation_wizard is None
    )


def test_complete_command_ends_an_open_comfort_conflict_question(monkeypatch):
    """F10: the comfort conflict question must not swallow a new command."""
    from homeintent.dialog_manager import DialogPriority, DialogTaskKind

    agent = _agent(monkeypatch)
    manager = agent._runtime_data.dialog_manager
    manager.create(
        "wizard", "comfort-household-conflict", DialogTaskKind.CONFLICT_RESOLUTION,
        DialogPriority.CONFIRMATION, slots={"area_id": "kueche", "present_user_ids": ("a", "b")},
    )

    answer = _run(agent, "Schalte das Küchenlicht ein.")

    assert "Temperaturwert" not in answer.response.speech
    agent.hass.services.async_call.assert_awaited()
    assert manager.active("wizard") is None
