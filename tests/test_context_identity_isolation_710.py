"""Conversation context is scoped to both the session and its principal."""

from __future__ import annotations

import asyncio
import types

import _ha_stub

_ha_stub.install()

from _testhaus import HouseConversation, house_entities, with_states
from homeassistant.components.conversation import ConversationInput


def _turn(house: HouseConversation, text: str, *, user: str | None, satellite: str):
    mock = house.entity.hass.services.async_call
    before = len(mock.await_args_list)
    result = asyncio.run(
        house.entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="shared-session-id",
                context=types.SimpleNamespace(user_id=user) if user else None,
                satellite_id=satellite,
            ),
            chat_log=None,
        )
    )
    calls = mock.await_args_list[before:]
    return result, calls


def test_same_conversation_id_never_transfers_referent_between_users(monkeypatch):
    entities = with_states(house_entities(), light__stehlampe="on")
    house = HouseConversation(monkeypatch, entities)
    house.say("Mach die Stehlampe heller.")

    house.user = "anna"
    followup = house.say("Mach sie wieder aus.")

    assert followup.calls == []


def test_anonymous_satellites_with_same_conversation_id_are_isolated(monkeypatch):
    entities = with_states(house_entities(), light__stehlampe="on")
    house = HouseConversation(monkeypatch, entities)
    first, first_calls = _turn(
        house, "Mach die Stehlampe heller.", user=None, satellite="satellite.kueche"
    )
    second, second_calls = _turn(
        house, "Mach sie wieder aus.", user=None, satellite="satellite.schlafzimmer"
    )

    assert first_calls
    assert second_calls == []
    assert first.conversation_id == "shared-session-id"
    assert second.conversation_id == "shared-session-id"
