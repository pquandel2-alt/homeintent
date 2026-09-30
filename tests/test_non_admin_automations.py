"""F26: creating automations can be reserved for administrators.

New installations store ``allow_non_admin_automations = False`` (see
``tests_ha/test_options_flow.py``); entries from before 7.2.1 keep their
behaviour. With the option off a non-admin user cannot confirm a new
automation, an administrator still can.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.const import CONF_ALLOW_NON_ADMIN_AUTOMATIONS  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

ENTITIES = [
    EntitySnapshot(
        "light.kinderzimmer", "Kinderzimmerlicht", "light", "off",
        area_id="kinderzimmer", area_name="Kinderzimmer",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
]
USERS = {"admin": True, "lena": False}
SENTENCE = "Jeden Werktag um 6 Uhr schalte das Kinderzimmerlicht ein."


def _agent(monkeypatch, tmp_path: Path, options: dict[str, object]) -> NluConversationEntity:
    entry = ConfigEntry()
    entry.options = options
    agent = NluConversationEntity(entry)
    agent.hass = HomeAssistant()
    agent.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def _get_user(user_id: str):
        return SimpleNamespace(is_admin=USERS[user_id], name=user_id)

    agent.hass.auth = SimpleNamespace(async_get_user=_get_user)
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: ENTITIES)
    return agent


def _say(agent: NluConversationEntity, text: str, user: str) -> str:
    user_input = ConversationInput(
        text=text, conversation_id=f"conv-{user}", context=SimpleNamespace(user_id=user)
    )
    result = asyncio.run(agent._async_handle_message(user_input, chat_log=None))
    return str(result.response.speech)


def test_non_admin_cannot_create_automations_when_the_option_is_off(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path, {CONF_ALLOW_NON_ADMIN_AUTOMATIONS: False})
    # 7.8 B5: refused before the preview, not after "Ja".
    assert _say(agent, SENTENCE, "lena") == (
        "Das Erstellen von Automationen ist nur für Administratoren erlaubt."
    )
    assert "Administratoren" not in _say(agent, "Ja", "lena")


def test_admin_still_creates_automations_when_the_option_is_off(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path, {CONF_ALLOW_NON_ADMIN_AUTOMATIONS: False})
    _say(agent, SENTENCE, "admin")
    assert _say(agent, "Ja", "admin") == "Automation wurde erstellt."
