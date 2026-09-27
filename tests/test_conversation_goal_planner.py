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
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.house_graph import FactProvenance  # noqa: E402
from homeintent.memory import MemoryKind, MemoryStore  # noqa: E402
from homeintent.goal_model import DesiredState, GoalScope  # noqa: E402
from homeintent.profiles import (  # noqa: E402
    ProfileStore,
    RoutineDefinition,
    RoutineStepDefinition,
)
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


LIGHTS = [
    EntitySnapshot(
        "light.living", "Wohnzimmerlicht", "light", "on",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
    EntitySnapshot(
        "light.hall", "Flurlicht", "light", "on",
        capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
    ),
]


def _night_routine() -> RoutineDefinition:
    return RoutineDefinition(
        "schlafengehen",
        "Schlafengehen",
        "owner",
        tuple(
            RoutineStepDefinition(
                entity.entity_id,
                GoalScope(entity_ids=(entity.entity_id,)),
                DesiredState("state", "off"),
            )
            for entity in LIGHTS
        ),
        True,
    )


def test_prepare_night_requires_confirmed_definition(monkeypatch):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="night-plan",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    preview = asyncio.run(turn("Bereite das Haus für die Nacht vor."))
    assert "Was soll ich" in preview.response.speech
    assert "ausdrücklichen Bestätigung" in preview.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_prepare_night_definition_dialog_stores_only_after_confirmation(
    monkeypatch, tmp_path
):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    profiles = ProfileStore(tmp_path / "profiles.json")
    entity._runtime_data.profiles = profiles
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="define-night",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    question = asyncio.run(turn("Bereite das Haus für die Nacht vor."))
    assert "Was soll ich" in question.response.speech
    assert profiles.routine("schlafengehen", user_id="owner") is None

    preview = asyncio.run(
        turn("Schalte das Wohnzimmerlicht aus und schalte das Flurlicht aus.")
    )
    assert "Soll ich diese Definition lokal speichern" in preview.response.speech
    assert profiles.routine("schlafengehen", user_id="owner") is None
    entity.hass.services.async_call.assert_not_awaited()

    saved = asyncio.run(turn("Ja"))
    routine = profiles.routine("schlafengehen", user_id="owner")
    assert saved.response.speech.startswith("Gespeichert")
    assert routine is not None and len(routine.steps) == 2
    entity.hass.services.async_call.assert_not_awaited()

    planned = asyncio.run(turn("Bereite das Haus für die Nacht vor."))
    assert "Planvorschau" in planned.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_read_only_goal_fails_before_preview(monkeypatch, tmp_path):
    entity = NluConversationEntity(
        ConfigEntry(options={"read_only_entities": ["light.living"]})
    )
    entity.hass = HomeAssistant()
    profiles = ProfileStore(tmp_path / "profiles.json")
    asyncio.run(profiles.async_save_routine(_night_routine(), confirmed=True))
    entity._runtime_data.profiles = profiles
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)
    result = asyncio.run(
        entity._async_handle_message(
            ConversationInput(
                text="Bereite das Haus für die Nacht vor.",
                conversation_id="denied-night-plan",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )
    )
    assert "keinen sicheren Plan" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_named_procedure_is_confirmed_stored_and_rematerialized(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    entity._runtime_data.memory = store
    profiles = ProfileStore(tmp_path / "profiles.json")
    asyncio.run(profiles.async_save_routine(_night_routine(), confirmed=True))
    entity._runtime_data.profiles = profiles
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])

    async def turn(text: str):
        return await entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id="named-procedure",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )

    asyncio.run(turn("Bereite das Haus für die Nacht vor."))
    save = asyncio.run(turn("Speichere diesen Plan als Abendrunde"))
    assert "dauerhaft speichern" in save.response.speech
    assert asyncio.run(store.async_list()) == ()

    confirmed = asyncio.run(turn("Ja"))
    records = asyncio.run(
        store.async_list(person_id="owner", kinds=(MemoryKind.PROCEDURE,))
    )
    assert confirmed.response.speech.startswith("Gespeichert")
    assert len(records) == 1
    assert records[0].content["goal_kind"] == "prepare_night"

    listed = asyncio.run(turn("Welche Prozeduren kennst du?"))
    assert "abendrunde" in listed.response.speech.casefold()

    rerun = asyncio.run(turn("Führe die Prozedur Abendrunde aus"))
    assert "frisch" not in rerun.response.speech.casefold()
    assert "Soll ich sie ausführen" in rerun.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    cancelled = asyncio.run(turn("Nein"))
    assert "nicht ausgeführt" in cancelled.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_movie_goal_uses_one_confirmed_preference_as_preview(monkeypatch, tmp_path):
    movie_light = EntitySnapshot(
        "light.movie", "Filmlicht", "light", "off",
        capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
    )
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    store = MemoryStore(tmp_path / "memory.sqlite", enabled=True)
    entity._runtime_data.memory = store
    asyncio.run(
        store.async_remember(
            MemoryKind.PREFERENCE,
            {
                "activity": "television",
                "entity_id": movie_light.entity_id,
                "brightness_percent": 30,
            },
            provenance=FactProvenance.CONFIRMED_MEMORY,
            confirmed=True,
            person_id="owner",
        )
    )
    profiles = ProfileStore(tmp_path / "profiles.json")
    asyncio.run(
        profiles.async_save_routine(
            RoutineDefinition(
                "filmabend",
                "Filmabend",
                "owner",
                (
                    RoutineStepDefinition(
                        "movie-light",
                        GoalScope(entity_ids=(movie_light.entity_id,)),
                        DesiredState("brightness", 30, "%"),
                    ),
                ),
                True,
            ),
            confirmed=True,
        )
    )
    entity._runtime_data.profiles = profiles
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda *_: [movie_light]
    )
    monkeypatch.setattr(ha_conversation, "build_device_snapshots", lambda *_: [])

    result = asyncio.run(
        entity._async_handle_message(
            ConversationInput(
                text="Bereite den Filmabend vor",
                conversation_id="movie-plan",
                context=SimpleNamespace(user_id="owner"),
            ),
            None,
        )
    )
    active = entity._runtime_data.dialog_manager.active("movie-plan")
    assert "Planvorschau" in result.response.speech
    assert active is not None
    stored_plan = active.slots["plan"]
    action = next(step.action for step in stored_plan.steps if step.action is not None)
    assert action.data == {"brightness_pct": 30}
    entity.hass.services.async_call.assert_not_awaited()


def _reading_routine() -> RoutineDefinition:
    return RoutineDefinition(
        "lesezeit",
        "Lesezeit",
        "owner",
        (
            RoutineStepDefinition(
                "light.living",
                GoalScope(entity_ids=("light.living",)),
                DesiredState("state", "off"),
            ),
        ),
        True,
    )


def test_stored_routine_is_reachable_by_its_own_name(monkeypatch, tmp_path):
    """F14: a routine saved via homeintent.save_routine is not limited to
    the built-in names (schlafengehen/filmabend/abwesenheit)."""
    for index, sentence in enumerate(
        ("Bereite die Lesezeit vor.", "Starte die Routine Lesezeit.")
    ):
        entity = NluConversationEntity(ConfigEntry())
        entity.hass = HomeAssistant()
        profiles = ProfileStore(tmp_path / f"profiles-{index}.json")
        asyncio.run(profiles.async_save_routine(_reading_routine(), confirmed=True))
        entity._runtime_data.profiles = profiles
        monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)

        result = asyncio.run(
            entity._async_handle_message(
                ConversationInput(
                    text=sentence,
                    conversation_id=f"reading-{index}",
                    context=SimpleNamespace(user_id="owner"),
                ),
                None,
            )
        )

        assert "Planvorschau" in result.response.speech, sentence
        assert "Wohnzimmerlicht" in result.response.speech
        entity.hass.services.async_call.assert_not_awaited()


def test_goal_area_comes_from_the_area_registry():
    from homeintent.goal_intent import interpret_goal
    from homeintent.nlu.language_frontend import analyse_language

    goal = interpret_goal(
        analyse_language("Sorge dafür, dass es um 7 Uhr im Arbeitszimmer 21 Grad warm ist."),
        area_names={"buero": "buero", "arbeitszimmer": "buero"},
    )
    assert goal is not None and goal.scope.area_id == "buero"


def test_slow_plan_verification_answers_at_once_and_reports_only_failure(
    monkeypatch, tmp_path
):
    """F17: a confirmed plan whose effect takes long must not block the reply."""
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    profiles = ProfileStore(tmp_path / "profiles.json")
    asyncio.run(profiles.async_save_routine(_night_routine(), confirmed=True))
    entity._runtime_data.profiles = profiles
    from datetime import timedelta

    entity._runtime_data.effect_monitor.timeout = timedelta(milliseconds=300)
    monkeypatch.setattr(ha_conversation, "_PLAN_REPLY_BUDGET_SECONDS", 0.05)
    # The lights never report "off": verification must fail in the background.
    monkeypatch.setattr(ha_conversation, "build_entity_snapshots", lambda *_: LIGHTS)

    async def scenario():
        async def turn(text: str):
            return await entity._async_handle_message(
                ConversationInput(
                    text=text, conversation_id="slow-plan",
                    context=SimpleNamespace(user_id="owner"),
                ),
                None,
            )

        preview = await turn("Bereite das Haus für die Nacht vor.")
        assert "Planvorschau" in preview.response.speech
        answer = await turn("Ja")
        assert "melde mich nur, falls etwas nicht klappt" in answer.response.speech
        await asyncio.sleep(1.0)
        calls = [
            call for call in entity.hass.services.async_call.await_args_list
            if call.args[:2] == ("persistent_notification", "create")
        ]
        assert len(calls) == 1
        assert "nicht vollständig ausgeführt" in calls[0].args[2]["message"]

    asyncio.run(scenario())
