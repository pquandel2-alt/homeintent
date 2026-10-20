from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.memory import MemoryKind, MemoryStore  # noqa: E402
from homeintent.house_graph import FactProvenance  # noqa: E402
from homeintent.areas import AreaSnapshot  # noqa: E402
from homeintent.profiles import ComfortProfile, ProfileStore  # noqa: E402
from homeintent.situation import (  # noqa: E402
    EventQuality,
    EventType,
    NormalizedEvent,
    RoutineStatistics,
)
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


LAMP = EntitySnapshot(
    "light.floor", "Stehlampe", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
)


def test_preference_is_only_persisted_after_bound_confirmation(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    entity._runtime_data.memory = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: [LAMP]
    )

    async def turn(text: str, user_id: str = "owner"):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="memory-conversation",
                context=SimpleNamespace(user_id=user_id),
            ),
            chat_log=None,
        )

    first = asyncio.run(
        turn("Merk dir, dass ich beim Fernsehen nur die Stehlampe auf 30 Prozent möchte.")
    )
    before = asyncio.run(entity._runtime_data.memory.async_list())
    wrong_user = asyncio.run(turn("Ja", "guest"))
    after_wrong_user = asyncio.run(entity._runtime_data.memory.async_list())
    confirmed = asyncio.run(turn("Ja"))
    stored = asyncio.run(entity._runtime_data.memory.async_list())

    assert "dauerhaft" in first.response.speech
    assert before == () and after_wrong_user == ()
    assert "keine Frage offen" in wrong_user.response.speech
    assert confirmed.response.speech.startswith("Gespeichert")
    assert len(stored) == 1 and stored[0].kind is MemoryKind.PREFERENCE
    assert stored[0].content["entity_id"] == "light.floor"
    entity.hass.services.async_call.assert_not_awaited()


def test_complete_new_command_replaces_memory_confirmation(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    entity._runtime_data.memory = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])

    async def scenario():
        context = SimpleNamespace(user_id="owner")
        await entity._async_handle_message(
            ConversationInput(
                text="Merk dir, dass ich beim Fernsehen nur die Stehlampe auf 30 Prozent möchte.",
                conversation_id="replace-memory",
                context=context,
            ),
            None,
        )
        return await entity._async_handle_message(
            ConversationInput(
                text="Schalte die Stehlampe ein",
                conversation_id="replace-memory",
                context=context,
            ),
            None,
        )

    result = asyncio.run(scenario())
    assert result.response.speech
    entity.hass.services.async_call.assert_awaited_once()
    assert asyncio.run(entity._runtime_data.memory.async_list()) == ()


def test_personal_preference_can_be_corrected_and_forgotten_naturally(
    monkeypatch, tmp_path
):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    entity._runtime_data.memory = store
    asyncio.run(store.async_remember(
        MemoryKind.PREFERENCE,
        {
            "activity": "television",
            "brightness_percent": 30,
            "entity_id": LAMP.entity_id,
        },
        provenance=FactProvenance.CONFIRMED_MEMORY,
        confirmed=True,
        person_id="owner",
    ))
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="memory-change",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    proposed = asyncio.run(turn("Ändere meine Präferenz für die Stehlampe auf 40 Prozent"))
    corrected = asyncio.run(turn("Ja"))
    records = asyncio.run(store.async_list(person_id="owner"))
    forget_proposed = asyncio.run(turn("Vergiss meine Präferenz für die Stehlampe"))
    forgotten = asyncio.run(turn("Ja"))

    assert "korrigieren" in proposed.response.speech
    assert "korrigiert" in corrected.response.speech
    assert records[0].content["brightness_percent"] == 40
    assert "löschen" in forget_proposed.response.speech
    assert "gelöscht" in forgotten.response.speech
    assert asyncio.run(store.async_list(person_id="owner")) == ()


def test_forget_all_personal_data_requires_confirmation(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    entity._runtime_data.memory = store
    asyncio.run(store.async_remember(
        MemoryKind.PREFERENCE,
        {"entity_id": LAMP.entity_id, "brightness_percent": 30},
        provenance=FactProvenance.CONFIRMED_MEMORY,
        confirmed=True,
        person_id="owner",
    ))
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="forget-person",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    proposed = asyncio.run(turn("Lösche alle Daten über mich"))
    before = asyncio.run(store.async_list(person_id="owner"))
    confirmed = asyncio.run(turn("Ja"))

    assert "wirklich" in proposed.response.speech
    assert len(before) == 1
    assert "kontrolliert gelöscht" in confirmed.response.speech
    assert asyncio.run(store.async_list(person_id="owner")) == ()


def test_comfort_request_uses_confirmed_profile_before_action(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    profiles = ProfileStore(tmp_path / "profiles.json")
    asyncio.run(
        profiles.async_save_comfort_profile(
            ComfortProfile(
                "living-comfort", "owner", "living",
                brightness_min=40, brightness_max=60, confirmed=True,
            ),
            confirmed=True,
        )
    )
    entity._runtime_data.profiles = profiles
    lamp = EntitySnapshot(
        LAMP.entity_id, LAMP.friendly_name, LAMP.domain, LAMP.state,
        area_id="living", capabilities=LAMP.capabilities,
        attributes={"brightness": 10},
    )
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [lamp])
    monkeypatch.setattr(
        ha_conversation,
        "resolve_conversation_area",
        lambda *_: AreaSnapshot("living", "Wohnzimmer"),
    )

    async def turn(text: str, user_id: str = "owner"):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="comfort",
                context=SimpleNamespace(user_id=user_id),
            ),
            None,
        )

    proposed = asyncio.run(turn("Mach es hier gemütlicher."))
    assert "Stehlampe" in proposed.response.speech
    assert "bestätigten Helligkeitsbereich" in proposed.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    wrong_user = asyncio.run(turn("Ja", "guest"))
    assert "keine Frage offen" in wrong_user.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    confirmed = asyncio.run(turn("Ja"))
    assert "führe den Plan" in confirmed.response.speech
    entity.hass.services.async_call.assert_awaited()

def test_comfort_request_without_unique_preference_never_acts(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    entity._runtime_data.memory = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])
    result = asyncio.run(
        entity._async_handle_message(
            ConversationInput(
                text="Mach es hier gemütlicher.",
                conversation_id="comfort-ambiguous",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )
    )
    assert "keinem eindeutigen Home-Assistant-Bereich" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_comfort_profile_definition_requires_explicit_confirmation(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    profiles = ProfileStore(tmp_path / "profiles.json")
    entity._runtime_data.profiles = profiles
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])
    monkeypatch.setattr(
        ha_conversation,
        "resolve_conversation_area",
        lambda *_: AreaSnapshot("living", "Wohnzimmer"),
    )

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="define-comfort",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    question = asyncio.run(turn("Mach es hier gemütlicher."))
    assert "Was bedeutet angenehm" in question.response.speech

    preview = asyncio.run(
        turn("Temperatur 21 bis 22 Grad und Licht 40 bis 60 Prozent.")
    )
    assert "Temperatur 21 bis 22 Grad" in preview.response.speech
    assert "Helligkeit 40 bis 60 Prozent" in preview.response.speech
    assert profiles.comfort(area_id="living", user_id="owner") is None

    confirmed = asyncio.run(turn("Ja"))
    profile = profiles.comfort(area_id="living", user_id="owner")
    assert confirmed.response.speech.startswith("Gespeichert")
    assert profile is not None
    assert (profile.temperature_min, profile.temperature_max) == (21, 22)
    assert (profile.brightness_min, profile.brightness_max) == (40, 60)
    entity.hass.services.async_call.assert_not_awaited()


def test_explicit_routine_feedback_is_bound_and_persisted(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    entity._runtime_data.memory = store
    now = datetime.now(timezone.utc)
    stats = RoutineStatistics(minimum_observations=1, anomaly_threshold=0.5)
    stats.observe(
        NormalizedEvent(
            "old", EventType.ACTIVATED, LAMP.entity_id, "off", "on",
            now - timedelta(days=7, hours=3), "living", (), True, "normal",
            "home_assistant", EventQuality.GOOD,
        )
    )
    assessment = stats.assess(
        NormalizedEvent(
            "alert", EventType.ACTIVATED, LAMP.entity_id, "off", "on", now,
            "living", (), True, "normal", "home_assistant", EventQuality.GOOD,
        )
    )
    assert assessment.unusual
    entity._runtime_data.routine_statistics[LAMP.entity_id] = stats
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: [LAMP])
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])
    monkeypatch.setattr(ha_conversation.dt_util, "utcnow", lambda: now)

    result = asyncio.run(
        entity._async_handle_message(
            ConversationInput(
                text="Das war hilfreich",
                conversation_id="routine-feedback",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )
    )
    records = asyncio.run(
        store.async_list(person_id="owner", kinds=(MemoryKind.DECISION,))
    )
    assert "hilfreich" in result.response.speech
    assert len(records) == 1
    assert records[0].content == {
        "feedback": "helpful",
        "routine_entity_id": LAMP.entity_id,
    }
    entity.hass.services.async_call.assert_not_awaited()
