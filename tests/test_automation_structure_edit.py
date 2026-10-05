from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402
from _users import as_user, install_users  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.automation_structure_edit import (  # noqa: E402
    AutomationEditOperation,
    AutomationEditSection,
    parse_automation_structure_edit,
)
from homeintent.automation_summary import AutomationSummary  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


AUTOMATION = AutomationSummary(
    "auto-1",
    "Flurlicht Automation",
    source_text="Wenn es dunkel ist, schalte das Flurlicht ein",
    created_by="homeintent",
    triggers=({"trigger": "time", "at": "20:00:00"},),
    conditions=(),
    actions=({"action": "homeassistant.turn_on", "target": {"entity_id": "light.flur"}},),
)
LIGHT = EntitySnapshot(
    "light.flur", "Flurlicht", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)


def _agent(monkeypatch, automations=(AUTOMATION,)):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: [LIGHT]
    )
    agent._automation_executor = SimpleNamespace(
        async_list_automations=AsyncMock(return_value=automations),
        async_replace_automation_section=AsyncMock(),
        async_replace_automation_actions=AsyncMock(),
    )
    return agent


def _run(agent, text, conversation_id="edit-1"):
    # Editing needs the owner or an administrator (7.9.1 A2): the edit
    # mechanics run as an administrator.
    install_users(agent.hass)
    return asyncio.run(agent._async_handle_message(
        ConversationInput(text=text, conversation_id=conversation_id, context=as_user("admin")),
        chat_log=None,
    ))


def test_structure_edit_parser_keeps_section_operation_and_payload_separate():
    trigger = parse_automation_structure_edit(
        "Ändere den Auslöser der Automation Flur auf wenn die Sonne untergeht"
    )
    condition = parse_automation_structure_edit(
        "Füge der Automation Flur die Bedingung hinzu, dass jemand zuhause ist"
    )
    clear = parse_automation_structure_edit(
        "Lösche alle Bedingungen der Automation Flur"
    )
    assert trigger is not None
    assert trigger.section is AutomationEditSection.TRIGGERS
    assert trigger.operation is AutomationEditOperation.REPLACE
    assert trigger.payload == "wenn die Sonne untergeht"
    assert condition is not None and condition.operation is AutomationEditOperation.ADD
    assert condition.payload == "dass jemand zuhause ist"
    assert clear is not None and clear.operation is AutomationEditOperation.CLEAR
    semantic_remove = parse_automation_structure_edit("Entferne die Zeitbedingung")
    assert semantic_remove is not None
    assert semantic_remove.operation is AutomationEditOperation.REMOVE
    assert semantic_remove.payload == "time"


def test_trigger_edit_has_before_after_preview_and_confirmation(monkeypatch):
    agent = _agent(monkeypatch)
    preview = _run(
        agent,
        "Ändere den Auslöser der Automation Flurlicht Automation auf wenn die Sonne untergeht",
    )
    assert "vorher 1, danach 1" in preview.response.speech
    agent._automation_executor.async_replace_automation_section.assert_not_awaited()

    done = _run(agent, "ja")
    assert done.response.speech == "Die Auslöser wurden geändert."
    agent._automation_executor.async_replace_automation_section.assert_awaited_once()
    args = agent._automation_executor.async_replace_automation_section.await_args.args
    assert args[0:2] == (AUTOMATION.automation_id, "triggers")
    assert args[2] == [{"trigger": "sun", "event": "sunset"}]


def test_clear_all_conditions_is_confirmed_but_specific_delete_is_refused(monkeypatch):
    with_condition = AutomationSummary(
        **{
            **AUTOMATION.__dict__,
            "conditions": ({"condition": "time", "after": "08:00:00"},),
        }
    )
    agent = _agent(monkeypatch, (with_condition,))
    preview = _run(
        agent, "Lösche alle Bedingungen der Automation Flurlicht Automation"
    )
    assert "vorher 1, danach 0" in preview.response.speech
    _run(agent, "ja")
    args = agent._automation_executor.async_replace_automation_section.await_args.args
    assert args[1:3] == ("conditions", [])


def test_condition_can_be_removed_by_semantic_kind(monkeypatch):
    with_conditions = AutomationSummary(
        **{
            **AUTOMATION.__dict__,
            "conditions": (
                {"condition": "time", "after": "08:00:00"},
                {"condition": "state", "entity_id": "binary_sensor.home", "state": "on"},
            ),
        }
    )
    agent = _agent(monkeypatch, (with_conditions,))

    preview = _run(agent, "Entferne die Zeitbedingung")
    assert "vorher 2, danach 1" in preview.response.speech
    _run(agent, "ja")
    args = agent._automation_executor.async_replace_automation_section.await_args.args
    assert args[2] == [with_conditions.conditions[1]]


def test_action_can_be_appended_without_replacing_existing_action(monkeypatch):
    agent = _agent(monkeypatch)
    question = _run(
        agent,
        "Füge eine Aktion zur Automation Flurlicht Automation hinzu",
        "action-add",
    )
    assert "zusätzlich" in question.response.speech
    preview = _run(agent, "Schalte das Flurlicht aus", "action-add")
    assert "vorher 1, danach 2" in preview.response.speech
    done = _run(agent, "ja", "action-add")
    assert "Aktion wurde geändert" in done.response.speech
    args = agent._automation_executor.async_replace_automation_actions.await_args.args
    assert len(args[1]) == 2
    assert args[1][-1] == {
        "action": "homeassistant.turn_off",
        "target": {"entity_id": LIGHT.entity_id},
    }
