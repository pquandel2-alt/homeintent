"""Skript-Aktivierung (2026-08-20): the full spoken round trip through the
real ``NluConversationEntity._async_handle_message()`` orchestration - not a
second reimplementation of ``engine.py``'s matching or ``service_call.py``'s
``HassRunScript`` plan-building (each already has its own unit test file;
this file's job is to prove the whole live pipeline wires them together,
including the specific risk that Wave 11's ``_AUTOMATION_ENABLE_RE``
pre-check ("aktivier...") could swallow "Aktiviere {name}" before it ever
reaches the script grammar - see ``test_engine_scripts.py``'s equivalent
engine-level pin for the same concern).

Reuses the exact ``_ha_stub``/``_make_entity``/``_run`` harness
``tests/test_conversation_automation_once.py`` already established (Regel 6),
including its ``tmp_path``-based ``hass.config.path`` override - required
here too since "aktivier..." triggers a real (empty) ``automations.yaml``
read attempt before falling through to the script match.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers.intent import IntentResponseType  # noqa: E402

GUTE_NACHT = EntitySnapshot(
    "script.gute_nacht", "Gute Nacht", "script", "off",
    capabilities=frozenset({"TURN_ON"}),
)
ALL_ENTITIES = [GUTE_NACHT]


def _make_entity(monkeypatch, tmp_path: Path) -> NluConversationEntity:
    entry = ConfigEntry()
    entity = NluConversationEntity(entry)
    entity.hass = HomeAssistant()
    _ha_stub.register_script(entity.hass, "script.gute_nacht", [
        {"action": "persistent_notification.create", "data": {"message": "Gute Nacht"}},
    ])
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ALL_ENTITIES
    )
    return entity


def _run(entity: NluConversationEntity, text: str, conversation_id: str = "conv-1"):
    user_input = ConversationInput(text=text, conversation_id=conversation_id)
    return asyncio.run(entity._async_handle_message(user_input, chat_log=None))


def test_starte_runs_the_script(monkeypatch, tmp_path):
    entity = _make_entity(monkeypatch, tmp_path)
    result = _run(entity, "Starte Gute Nacht")

    entity.hass.services.async_call.assert_awaited_once_with(
        "script", "turn_on", {"entity_id": "script.gute_nacht"}, blocking=True,
    )
    assert result.response.response_type == IntentResponseType.ACTION_DONE
    assert result.response.speech


def test_aktiviere_survives_the_automation_enable_precheck_and_runs_the_script(
    monkeypatch, tmp_path,
):
    # The exact collision risk this feature had to rule out: "aktivier..."
    # first triggers conversation.py's match_automation_enable() attempt
    # (Wave 11's _AUTOMATION_ENABLE_RE) against an (empty, tmp_path-backed)
    # automations.yaml, which must structurally fail to match and fall
    # through to the real script match - not raise or silently no-op.
    entity = _make_entity(monkeypatch, tmp_path)
    result = _run(entity, "Aktiviere Gute Nacht")

    entity.hass.services.async_call.assert_awaited_once_with(
        "script", "turn_on", {"entity_id": "script.gute_nacht"}, blocking=True,
    )
    assert result.response.response_type == IntentResponseType.ACTION_DONE
    assert result.response.speech
