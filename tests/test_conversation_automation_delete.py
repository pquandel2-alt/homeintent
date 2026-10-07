"""HomeIntent V5 Teil 8/10 (V5.28, "Automation Deletion"): the full spoken
confirm/cancel/unclear round-trip for automation deletion, exercised through
the real ``NluConversationEntity._async_handle_message()`` orchestration -
not a second reimplementation of ``match_automation_delete()``/
``classify_confirmation_reply()``/``AutomationExecutor.async_delete_automation()``
(each already has its own dedicated pure-function/direct-method test file;
this file's job is to prove the whole live pipeline wires them together
correctly turn-to-turn).

Reuses the exact ``_ha_stub``/``_make_entity``/``_run``/``_automations_yaml``
harness ``tests/test_conversation_automation_confirmation.py`` and
``tests/test_conversation_automation_query.py`` already established
(Regel 6) - including creating a real automation through the existing YES
confirmation round-trip first, so every deletion test here deletes the real
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
from homeintent.controllers.automation_management import (  # noqa: E402
    AUTOMATION_DELETED_TEXT,
    AUTOMATION_DELETION_CANCELLED_TEXT,
    AUTOMATION_DELETION_CONFIRMATION_UNCLEAR_TEXT,
)
from homeintent.controllers.automations import AUTOMATION_CREATED_TEXT  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import intent  # noqa: E402

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


# --- full YES round-trip: confirmation turn, then "Ja" deletes + reloads ---


def test_yes_reply_deletes_the_automation_and_calls_automation_reload(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")
    entity.hass.services.async_call.reset_mock()

    confirm_result = _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")
    assert 'Soll die Automation "' in confirm_result.response.speech
    assert "gelöscht werden?" in confirm_result.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    delete_result = _run(entity, "Ja", conversation_id="delete")

    assert delete_result.response.speech == AUTOMATION_DELETED_TEXT
    assert delete_result.response.error_code is None
    entity.hass.services.async_call.assert_awaited_once_with("automation", "reload", {}, blocking=True)
    assert _automations_yaml(tmp_path) == []


def test_entferne_wording_also_deletes(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")

    _run(entity, "Entferne die Automation für Küchenlicht.", conversation_id="delete")
    delete_result = _run(entity, "Ja", conversation_id="delete")

    assert delete_result.response.speech == AUTOMATION_DELETED_TEXT
    assert _automations_yaml(tmp_path) == []


# --- NO / cancel round-trip -------------------------------------------------


def test_no_reply_cancels_and_deletes_nothing(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")
    entity.hass.services.async_call.reset_mock()

    _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")
    cancel_result = _run(entity, "Nein", conversation_id="delete")

    assert cancel_result.response.speech == AUTOMATION_DELETION_CANCELLED_TEXT
    entity.hass.services.async_call.assert_not_awaited()
    assert len(_automations_yaml(tmp_path)) == 1


def test_after_a_no_reply_the_pending_deletion_is_cleared(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")

    _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")
    _run(entity, "Nein", conversation_id="delete")

    # A later "Ja" on the same conversation_id must not resurrect the
    # cancelled deletion - it's just another unmatched sentence now.
    stray_result = _run(entity, "Ja", conversation_id="delete")

    assert stray_result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    assert len(_automations_yaml(tmp_path)) == 1


# --- UNCLEAR round-trip: re-asks, keeps the pending state ------------------


def test_unclear_reply_re_asks_and_keeps_the_pending_deletion(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")

    _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")
    unclear_result = _run(entity, "Wie ist das Wetter?", conversation_id="delete")

    assert unclear_result.response.speech == AUTOMATION_DELETION_CONFIRMATION_UNCLEAR_TEXT
    assert len(_automations_yaml(tmp_path)) == 1

    # The pending deletion survived the unclear reply - a "Ja" now still
    # deletes the originally-targeted automation, not a fresh sentence.
    confirm_result = _run(entity, "Ja", conversation_id="delete")
    assert confirm_result.response.speech == AUTOMATION_DELETED_TEXT
    assert _automations_yaml(tmp_path) == []


# --- 0-match / 2+-match refusals never set a pending deletion --------------


def test_no_matching_automation_refuses_and_sets_no_pending_deletion(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="create")

    result = _run(entity, "Lösche die Automation für Bürolicht.", conversation_id="delete")
    assert result.response.speech == "Ich habe keine Automation gefunden, die Bürolicht steuert."

    # No pending deletion was stored - a stray "Ja" is just unmatched.
    stray_result = _run(entity, "Ja", conversation_id="delete")
    assert stray_result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    assert len(_automations_yaml(tmp_path)) == 1


def test_two_matching_automations_refuse_rather_than_pick_one(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    _create_automation(entity, conversation_id="first")
    _create_automation(entity, conversation_id="second")
    assert len(_automations_yaml(tmp_path)) == 2

    result = _run(entity, "Lösche die Automation für Küchenlicht.", conversation_id="delete")

    assert result.response.speech == (
        "Es gibt mehrere Automationen, die Küchenlicht steuern. "
        "Das kann ich nicht eindeutig löschen."
    )
    assert len(_automations_yaml(tmp_path)) == 2


# --- cost gate: the blocking automations.yaml/metadata read is skipped -----
# for ordinary turns that don't look like an automation deletion ------------


def test_an_ordinary_command_never_triggers_the_automations_yaml_read_for_delete(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)

    async def _fail_if_called(self):
        raise AssertionError(
            "async_list_automations() must not be called for a sentence "
            "that doesn't match the automation-delete regex gate"
        )

    monkeypatch.setattr(
        ha_automation_executor.AutomationExecutor, "async_list_automations", _fail_if_called
    )

    result = _run(entity, "Schalte das Küchenlicht ein.")

    entity.hass.services.async_call.assert_awaited_once()
    assert result.response.speech != "Das habe ich nicht verstanden."
