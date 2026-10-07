"""HomeIntent V5 Teil 8/10 (Wave 11, "Automation Disable/Enable"): the full
spoken disable/enable turn, exercised through the real
``NluConversationEntity._async_handle_message()`` orchestration - not a
second reimplementation of ``match_automation_disable()``/
``match_automation_enable()``/``AutomationExecutor.async_disable_automation()``/
``async_enable_automation()`` (each already has its own dedicated pure-
function/direct-method test file; this file's job is to prove the whole live
pipeline wires them together correctly).

Unlike ``test_conversation_automation_delete.py``, there is no confirmation
round-trip here at all: a single-match resolution acts immediately on the
same turn (see ``AutomationToggleMatchResult``'s own docstring for why).

Reuses the exact ``_ha_stub``/``_make_entity``/``_run``/``_automations_yaml``
harness ``tests/test_conversation_automation_delete.py`` already established
(Regel 6) - including creating a real automation through the existing YES
confirmation round-trip first, so every toggle test here acts on the real
thing that pipeline writes, not a hand-rolled fixture duplicating its shape.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml as pyyaml

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402
from _users import as_user, install_users  # noqa: E402

_ha_stub.install()

import homeintent.automation_executor as ha_automation_executor  # noqa: E402
import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.controllers.automations import (  # noqa: E402
    AUTOMATION_CREATED_TEXT,
)
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

KUECHE_FENSTER_ZU = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
KUECHE_LICHT = EntitySnapshot(
    "light.kueche_licht", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
BUERO_LICHT = EntitySnapshot(
    "light.buero_licht", "Bürolicht", "light", "off",
    area_id="buero", area_name="Büro",
    capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
)
ALL_ENTITIES = [KUECHE_FENSTER_ZU, KUECHE_LICHT, BUERO_LICHT]

AUTOMATION_SENTENCE = "Wenn das Küchenfenster geöffnet wird, schalte das Küchenlicht ein."


def _make_entity(monkeypatch, tmp_path: Path) -> NluConversationEntity:
    entry = ConfigEntry()
    entity = NluConversationEntity(entry)
    entity.hass = HomeAssistant()
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ALL_ENTITIES
    )
    return entity


def _run(entity: NluConversationEntity, text: str, conversation_id: str = "conv-1"):
    # Managing needs the owner or an administrator (7.9.1 A2): the
    # management mechanics run as an administrator.
    install_users(entity.hass)
    user_input = ConversationInput(text=text, conversation_id=conversation_id, context=as_user("admin"))
    return asyncio.run(entity._async_handle_message(user_input, chat_log=None))


def _automations_yaml(tmp_path: Path) -> list[dict]:
    path = tmp_path / "automations.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return pyyaml.safe_load(handle) or []


def _create_automation(entity: NluConversationEntity, conversation_id: str) -> None:
    _run(entity, AUTOMATION_SENTENCE, conversation_id=conversation_id)
    result = _run(entity, "Ja", conversation_id=conversation_id)
    assert result.response.speech == AUTOMATION_CREATED_TEXT


# --- disable: single match acts immediately, no confirmation round-trip ----


def test_disable_sets_initial_state_false_and_calls_automation_reload(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")
    entity.hass.services.async_call.reset_mock()

    result = _run(entity, "Deaktiviere die Automation für Küchenlicht.", conversation_id="toggle")

    assert result.response.speech == "Automation wurde deaktiviert."
    assert result.response.error_code is None
    entity.hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)
    automations = _automations_yaml(tmp_path)
    assert len(automations) == 1
    assert automations[0]["initial_state"] is False


def test_enable_sets_initial_state_true_and_calls_automation_reload(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")
    _run(entity, "Deaktiviere die Automation für Küchenlicht.", conversation_id="toggle")
    entity.hass.services.async_call.reset_mock()

    result = _run(entity, "Aktiviere die Automation für Küchenlicht.", conversation_id="toggle")

    assert result.response.speech == "Automation wurde aktiviert."
    assert result.response.error_code is None
    entity.hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)
    automations = _automations_yaml(tmp_path)
    assert len(automations) == 1
    assert automations[0]["initial_state"] is True


def test_disable_needs_no_confirmation_round_trip_a_following_ja_is_unmatched(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")

    disable_result = _run(entity, "Deaktiviere die Automation für Küchenlicht.", conversation_id="toggle")
    assert disable_result.response.speech == "Automation wurde deaktiviert."

    # No pending state was stored - a "Ja" right after is just an ordinary,
    # unmatched follow-up, not a stray confirmation reply.
    from homeassistant.helpers import intent

    stray_result = _run(entity, "Ja", conversation_id="toggle")
    assert stray_result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH


# --- 0-match / 2+-match refusals never mutate the file ---------------------


def test_no_matching_automation_refuses_disable_without_writing(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")
    entity.hass.services.async_call.reset_mock()

    result = _run(entity, "Deaktiviere die Automation für Bürolicht.", conversation_id="toggle")

    assert result.response.speech == "Ich habe keine Automation gefunden, die Bürolicht steuert."
    entity.hass.services.async_call.assert_not_awaited()
    automations = _automations_yaml(tmp_path)
    assert "initial_state" not in automations[0]


def test_two_matching_automations_refuse_disable_rather_than_pick_one(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="first")
    _create_automation(entity, conversation_id="second")
    entity.hass.services.async_call.reset_mock()

    result = _run(entity, "Deaktiviere die Automation für Küchenlicht.", conversation_id="toggle")

    assert result.response.speech == (
        "Es gibt mehrere Automationen, die Küchenlicht steuern. "
        "Das kann ich nicht eindeutig deaktivieren."
    )
    entity.hass.services.async_call.assert_not_awaited()
    automations = _automations_yaml(tmp_path)
    assert all("initial_state" not in a for a in automations)


# --- cost gate: the blocking automations.yaml/metadata read is skipped -----
# for ordinary turns that don't look like a disable/enable -------------------


def test_an_ordinary_command_never_triggers_the_automations_yaml_read_for_toggle(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    async def _fail_if_called(self):
        raise AssertionError(
            "async_list_automations() must not be called for a sentence "
            "that doesn't match either toggle regex gate"
        )

    monkeypatch.setattr(
        ha_automation_executor.AutomationExecutor, "async_list_automations", _fail_if_called
    )

    result = _run(entity, "Schalte das Küchenlicht ein.")

    entity.hass.services.async_call.assert_awaited_once()
    assert result.response.speech != "Das habe ich nicht verstanden."
