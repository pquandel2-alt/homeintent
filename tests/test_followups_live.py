"""F13: follow-up utterances from the README against a live-like house."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.capabilities import derive_capabilities  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


def _entity(entity_id, name, state, area=None, **attributes):
    domain = entity_id.split(".", 1)[0]
    attributes = {"friendly_name": name, **attributes}
    return EntitySnapshot(
        entity_id, name, domain, state, area_id=area.casefold() if area else None, area_name=area,
        attributes=attributes,
        capabilities=frozenset(c.name for c in derive_capabilities(domain, None, attributes)),
    )


def _agent(monkeypatch, radio_state):
    house = [
        _entity("media_player.kuechenradio", "Küchenradio", radio_state, "Küche", supported_features=21437),
        _entity("notify.handy_anna_nachricht", "Handy Anna", "unknown"),
        _entity("notify.handy_philipp_nachricht", "Handy Philipp", "unknown"),
        _entity("person.anna", "Anna", "home"),
    ]
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: house)
    return agent


def _say(agent, text):
    asyncio.run(agent._async_handle_message(
        ConversationInput(text=text, conversation_id="followup"), chat_log=None
    ))
    return agent.hass.services.async_call.await_args_list


def test_bitte_weiterspielen_resumes_the_paused_radio(monkeypatch):
    agent = _agent(monkeypatch, "playing")
    _say(agent, "Pausiere das Küchenradio.")
    calls = _say(agent, "Bitte weiterspielen.")
    assert calls[-1].args[:2] == ("media_player", "media_play")
    assert calls[-1].args[2]["entity_id"] == "media_player.kuechenradio"


def test_kannst_du_es_pausieren_refers_to_the_started_radio(monkeypatch):
    agent = _agent(monkeypatch, "paused")
    _say(agent, "Starte das Küchenradio.")
    calls = _say(agent, "Kannst du es pausieren?")
    assert calls[-1].args[:2] == ("media_player", "media_pause")


def test_immediate_message_to_a_named_person(monkeypatch):
    agent = _agent(monkeypatch, "idle")
    calls = _say(agent, "Schick Anna eine Nachricht, dass das Essen fertig ist.")
    assert len(calls) == 1
    domain, service, data = calls[0].args[:3]
    assert (domain, service) == ("notify", "send_message")
    assert data["entity_id"] == "notify.handy_anna_nachricht"
    assert data["message"] == "Das Essen ist fertig."
