"""Safety confirmations are phrased as a grammatical German question."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.controllers.replies import confirmation_question as _confirmation_question  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


@pytest.mark.parametrize(
    ("response_text", "question"),
    (
        ("Burgtor wird geöffnet.", "Soll ich wirklich Burgtor öffnen?"),
        ("Garagentor wird geschlossen.", "Soll ich wirklich Garagentor schließen?"),
        ("2 Rollläden geöffnet.", "Soll ich wirklich 2 Rollläden öffnen?"),
        ("Haustür aufschließen.", "Soll ich wirklich Haustür aufschließen?"),
        ("Klingel drücken.", "Soll ich wirklich Klingel drücken?"),
        ("3 Schalter eingeschaltet.", "Soll ich wirklich 3 Schalter einschalten?"),
        ("Pumpe auf 40 Prozent gestellt.", "Soll ich wirklich Pumpe auf 40 Prozent gestellt?"),
    ),
)
def test_confirmation_question_uses_the_infinitive(response_text, question):
    assert _confirmation_question(response_text) == question


GATE = EntitySnapshot(
    "cover.hoftor", "Hoftor", "cover", "closed",
    area_id="hof", area_name="Hof", device_class="gate",
    capabilities=frozenset({"POSITION"}),
)


def test_gate_is_opened_only_after_a_grammatical_confirmation(monkeypatch, tmp_path):
    agent = NluConversationEntity(ConfigEntry())
    agent.hass = HomeAssistant()
    agent.hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    agent.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda hass, entry: [GATE])

    def run(text):
        return asyncio.run(agent._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="tor",
                context=SimpleNamespace(user_id="owner"),
            ),
            chat_log=None,
        ))

    question = run("Öffne das Hoftor.")

    assert question.response.speech == "Soll ich wirklich Hoftor öffnen?"
    agent.hass.services.async_call.assert_not_awaited()

    run("Ja.")

    agent.hass.services.async_call.assert_awaited_once()
    assert agent.hass.services.async_call.await_args.args[:2] == ("cover", "open_cover")
