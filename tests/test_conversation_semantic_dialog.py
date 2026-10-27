"""General completion of commands with one missing semantic component."""

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


ENTITIES = [
    EntitySnapshot(
        "climate.buero", "Heizung Büro", "climate", "heat",
        capabilities=frozenset({"TEMPERATURE"}),
        attributes={"temperature": 20, "min_temp": 7, "max_temp": 30},
    ),
    EntitySnapshot(
        "climate.schlafzimmer", "Heizung Schlafzimmer", "climate", "heat",
        capabilities=frozenset({"TEMPERATURE"}),
        attributes={"temperature": 19, "min_temp": 7, "max_temp": 30},
    ),
    EntitySnapshot("light.kueche", "Küchenlicht", "light", "off"),
    EntitySnapshot("light.flur", "Flurlicht", "light", "off"),
    EntitySnapshot("light.bad", "Badlicht", "light", "off"),
]


def _entity(monkeypatch, tmp_path):
    result = NluConversationEntity(ConfigEntry())
    result.hass = HomeAssistant()
    result.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ENTITIES
    )
    return result


def _run(entity, text, conversation_id="semantic"):
    return asyncio.run(
        entity._async_handle_message(
            ConversationInput(text=text, conversation_id=conversation_id),
            chat_log=None,
        )
    )


def test_missing_target_is_requested_and_completed(monkeypatch, tmp_path):
    entity = _entity(monkeypatch, tmp_path)
    question = _run(entity, "Stell auf 22 Grad")
    assert "Welche Heizung" in question.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    completed = _run(entity, "Heizung Büro")
    assert "22 Grad" in completed.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "climate", "set_temperature",
        {"entity_id": "climate.buero", "temperature": 22.0},
        blocking=True,
    )


def test_missing_value_is_requested_and_completed(monkeypatch, tmp_path):
    entity = _entity(monkeypatch, tmp_path)
    question = _run(entity, "Stelle Heizung Büro")
    assert "welche Temperatur" in question.response.speech

    _run(entity, "21,5 Grad")
    entity.hass.services.async_call.assert_awaited_once_with(
        "climate", "set_temperature",
        {"entity_id": "climate.buero", "temperature": 21.5},
        blocking=True,
    )


def test_some_devices_requires_explicit_names(monkeypatch, tmp_path):
    entity = _entity(monkeypatch, tmp_path)
    question = _run(entity, "Mach ein paar Lichter an")
    assert "Welche Geräte" in question.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    completed = _run(entity, "Küchenlicht und Flurlicht")
    assert "2 Geräte" in completed.response.speech
    calls = entity.hass.services.async_call.await_args_list
    assert [item.args[:2] for item in calls] == [
        ("homeassistant", "turn_on"),
        ("homeassistant", "turn_on"),
    ]
    assert {item.args[2]["entity_id"] for item in calls} == {
        "light.kueche",
        "light.flur",
    }
    assert all(item.kwargs["blocking"] is True for item in calls)
