"""Phase 3 (HomeIntent v4.2.1 plan, Sections 3/23-26): exercises the real
``NluConversationEntity._async_handle_message()`` orchestration end-to-end -
pending-clarification / follow-up / reference / query-followup / fresh-match
try-chain, ``ConversationContextStore`` set/clear, the ``QUERY_ANSWER`` vs
``ACTION_DONE`` response-type gate, and the actual
``hass.services.async_call`` call site - not a second reimplementation of
``engine.match()`` (every other test file in this suite only calls that
directly).

Uses ``tests/_ha_stub.py`` to inject a minimal fake ``homeassistant`` module
tree, since the real package isn't installed anywhere in this project's test
environment (see that module's docstring). ``build_entity_snapshots`` (pure
HA-registry glue, out of scope for this plan) is monkeypatched per test
instead of stubbed.
"""

from __future__ import annotations

from _ha_stub import ServiceMock  # noqa: E402
import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
from homeintent.areas import AreaSnapshot  # noqa: E402
from homeintent.automation_management import (  # noqa: E402
    AutomationManagementKind,
    AutomationManagementRequest,
)
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.bindings import BindingKind  # noqa: E402
from homeintent.dialog_manager import DialogTaskKind  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.context import (  # noqa: E402
    ConversationContext,
    PendingAutomationManagement,
    PendingServiceConfirmation,
)
from homeintent.service_call import ServiceCallPlan  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import intent  # noqa: E402

FLUR_LICHT_OFF = EntitySnapshot(
    "light.flur_licht", "Flurlicht", "light", "off",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
)
FLUR_LICHT_ON = EntitySnapshot(
    "light.flur_licht", "Flurlicht", "light", "on",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
)
EG_TEMPERATUR = EntitySnapshot(
    "sensor.kueche_temperatur",
    "Küche Temperatur",
    "sensor",
    "22.4",
    area_id="kueche",
    area_name="Küche",
    floor_id="eg",
    floor_name="Erdgeschoss",
    floor_level=0,
    unit="°C",
    device_class="temperature",
)
KUECHE_FENSTER = EntitySnapshot(
    "binary_sensor.kueche_fenster", "Küchenfenster", "binary_sensor", "off",
    area_id="kueche", area_name="Küche", device_class="window",
)
KUECHE_LICHT = EntitySnapshot(
    "light.kueche", "Küchenlicht", "light", "off",
    area_id="kueche", area_name="Küche",
    capabilities=frozenset({"TURN_ON", "TURN_OFF", "BRIGHTNESS"}),
)
RADIO_ATLAS = EntitySnapshot(
    "media_player.atlas", "Radio Atlas", "media_player", "playing"
)
SAUGROBOTER_ATLAS = EntitySnapshot(
    "vacuum.atlas", "Saugroboter Atlas", "vacuum", "cleaning"
)

LIVE_OUTDOOR_ENTITIES = [
    EntitySnapshot(
        "weather.baesweiler", "Baesweiler", "weather", "sunny",
        attributes={"temperature": 29},
    ),
    EntitySnapshot(
        "weather.openweathermap", "OpenWeatherMap", "weather", "sunny",
        attributes={"temperature": 28},
    ),
    EntitySnapshot(
        "sensor.outdoor_battery", "Außentemperatur Sensor Batterie", "sensor",
        "100", unit="%", device_class="battery",
    ),
    EntitySnapshot(
        "sensor.outdoor_humidity", "Außentemperatur Sensor Luftfeuchtigkeit",
        "sensor", "52.5", unit="%", device_class="humidity",
    ),
    EntitySnapshot(
        "sensor.outdoor_temperature", "Außentemperatur Sensor Temperatur",
        "sensor", "30.4", unit="°C", device_class="temperature",
    ),
]


def _make_entity(monkeypatch, entities: list[EntitySnapshot]) -> NluConversationEntity:
    entry = ConfigEntry()
    entity = NluConversationEntity(entry)
    entity.hass = HomeAssistant()
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: entities
    )
    return entity


def _run(entity: NluConversationEntity, text: str, conversation_id: str = "conv-1"):
    user_input = ConversationInput(text=text, conversation_id=conversation_id)
    return asyncio.run(entity._async_handle_message(user_input, chat_log=None))


def test_live_outdoor_sensor_wins_over_multiple_weather_entities(monkeypatch):
    entity = _make_entity(monkeypatch, LIVE_OUTDOOR_ENTITIES)

    result = _run(entity, "Welche Temperatur zeigt der Außentemperatur-Sensor?")

    assert result.response.speech == "30,4 Grad."
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    entity.hass.services.async_call.assert_not_awaited()


def test_state_followup_without_und_keeps_floor_and_device_class(monkeypatch):
    entities = [
        EntitySnapshot(
            "binary_sensor.gaeste_wc", "Gäste WC Fenster", "binary_sensor", "on",
            area_id="gaeste_wc", area_name="Gäste WC", floor_id="eg",
            floor_name="Erdgeschoss", floor_level=0, device_class="window",
        ),
        EntitySnapshot(
            "binary_sensor.schlafzimmer", "Schlafzimmer Fenster",
            "binary_sensor", "on", area_id="schlafzimmer",
            area_name="Schlafzimmer", floor_id="eg", floor_name="Erdgeschoss",
            floor_level=0, device_class="window",
        ),
        EntitySnapshot(
            "binary_sensor.kueche", "Küchenfenster", "binary_sensor", "off",
            area_id="kueche", area_name="Küche", floor_id="eg",
            floor_name="Erdgeschoss", floor_level=0, device_class="window",
        ),
    ]
    entity = _make_entity(monkeypatch, entities)

    first = _run(entity, "Sind im Erdgeschoss Fenster geöffnet?", "window-turn")
    followup = _run(entity, "Welche sind geschlossen?", "window-turn")

    assert first.response.speech == (
        "Bis auf Küchenfenster sind alle drei Fenster offen."
    )
    assert followup.response.speech == "Nur Küchenfenster ist geschlossen."
    assert followup.response.response_type == intent.IntentResponseType.QUERY_ANSWER


def test_turn_on_command_calls_service_and_responds_action_done(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    result = _run(entity, "Schalte das Flurlicht ein")

    entity.hass.services.async_call.assert_awaited_once()
    call_args = entity.hass.services.async_call.await_args
    # HassTurnOn deliberately calls the generic cross-domain
    # ``homeassistant.turn_on`` service, not ``light.turn_on``
    # (service_call.py:57) - matches production behavior.
    assert call_args.args[0] == "homeassistant"
    assert call_args.args[1] == "turn_on"
    assert call_args.args[2]["entity_id"] == "light.flur_licht"
    assert call_args.kwargs == {"blocking": True}

    assert result.response.error_code is None
    assert result.response.response_type == intent.IntentResponseType.ACTION_DONE
    assert result.response.speech


def test_source_area_resolves_roomless_percentage_command_locally(monkeypatch):
    """A generic target is scoped by this turn's physical voice origin."""
    entities = [
        EntitySnapshot(
            "cover.rollladen_wohnzimmer",
            "Rolllade Wohnzimmer",
            "cover",
            "closed",
            area_id="wohnzimmer",
            area_name="Wohnzimmer",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.rollladen_gaeste_wc",
            "Rolllade Gäste WC",
            "cover",
            "closed",
            area_id="gaeste_wc",
            area_name="Gäste-WC",
            capabilities=frozenset({"POSITION"}),
        ),
    ]
    entity = _make_entity(monkeypatch, entities)
    monkeypatch.setattr(
        ha_conversation,
        "resolve_conversation_area",
        lambda hass, user_input: AreaSnapshot("wohnzimmer", "Wohnzimmer"),
    )

    result = _run(entity, "Fahr die Rolllade auf fünfzig Prozent.")

    assert result.response.speech == "Rolllade Wohnzimmer auf 50 Prozent gefahren."
    entity.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"position": 50, "entity_id": "cover.rollladen_wohnzimmer"},
        blocking=True,
    )


def test_explicit_remote_entity_name_overrides_source_area(monkeypatch):
    entities = [
        EntitySnapshot(
            "cover.rollladen_wohnzimmer",
            "Rolllade Wohnzimmer",
            "cover",
            "closed",
            area_id="wohnzimmer",
            area_name="Wohnzimmer",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.rollladen_gaeste_wc",
            "Rolllade Gäste WC",
            "cover",
            "closed",
            area_id="gaeste_wc",
            area_name="Gäste-WC",
            capabilities=frozenset({"POSITION"}),
        ),
    ]
    entity = _make_entity(monkeypatch, entities)
    monkeypatch.setattr(
        ha_conversation,
        "resolve_conversation_area",
        lambda hass, user_input: AreaSnapshot("wohnzimmer", "Wohnzimmer"),
    )

    _run(entity, "Fahr die Rolllade Gäste WC auf fünfzig Prozent.")

    assert entity.hass.services.async_call.await_args.args[2]["entity_id"] == (
        "cover.rollladen_gaeste_wc"
    )


def test_fresh_roomless_command_uses_source_area_not_previous_area(monkeypatch):
    entities = [
        EntitySnapshot(
            "sensor.schlafzimmer_temperatur",
            "Temperatur Schlafzimmer",
            "sensor",
            "21",
            area_id="schlafzimmer",
            area_name="Schlafzimmer",
            unit="°C",
            device_class="temperature",
        ),
        EntitySnapshot(
            "cover.rollladen_wohnzimmer",
            "Rolllade Wohnzimmer",
            "cover",
            "closed",
            area_id="wohnzimmer",
            area_name="Wohnzimmer",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.rollladen_gaeste_wc",
            "Rolllade Gäste WC",
            "cover",
            "closed",
            area_id="gaeste_wc",
            area_name="Gäste-WC",
            capabilities=frozenset({"POSITION"}),
        ),
    ]
    entity = _make_entity(monkeypatch, entities)
    monkeypatch.setattr(
        ha_conversation,
        "resolve_conversation_area",
        lambda hass, user_input: AreaSnapshot("wohnzimmer", "Wohnzimmer"),
    )

    first = _run(entity, "Wie warm ist es im Schlafzimmer?", "source-area-turn")
    second = _run(
        entity,
        "Fahr die Rolllade auf fünfzig Prozent.",
        "source-area-turn",
    )

    assert first.response.speech == "21 Grad."
    assert second.response.speech == "Rolllade Wohnzimmer auf 50 Prozent gefahren."
    assert entity.hass.services.async_call.await_args.args[2]["entity_id"] == (
        "cover.rollladen_wohnzimmer"
    )


def test_conversation_has_no_legacy_device_router(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    assert not hasattr(ha_conversation, "match_device_control")

    result = _run(entity, "Schalte das Flurlicht ein")

    assert result.response.response_type == intent.IntentResponseType.ACTION_DONE
    entity.hass.services.async_call.assert_awaited_once()


def test_multi_command_applies_policy_before_any_service_call(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF, KUECHE_LICHT])
    entity.entry.options["read_only_entities"] = ["light.kueche"]

    result = _run(
        entity,
        "Schalte das Flurlicht ein und schalte das Küchenlicht aus",
    )

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code == intent.IntentResponseErrorCode.FAILED_TO_HANDLE
    assert "Lesen" in result.response.speech


def test_multi_command_executes_all_validated_actions_and_stores_atomic_undo(
    monkeypatch,
):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF, KUECHE_LICHT])

    result = _run(
        entity,
        "Schalte das Flurlicht ein und schalte das Küchenlicht aus",
        "multi-success",
    )

    assert result.response.error_code is None
    assert entity.hass.services.async_call.await_count == 2
    pending = entity._context_store.get("multi-success")
    assert pending is not None
    assert pending.pending_undo is not None
    assert len(pending.pending_undo.plans) == 2


def test_multi_command_runtime_failure_stops_remaining_actions(monkeypatch):
    third = EntitySnapshot("light.third", "Drittes Licht", "light", "off")
    entity = _make_entity(
        monkeypatch, [FLUR_LICHT_OFF, KUECHE_LICHT, third]
    )
    entity.hass.services.async_call = ServiceMock(
        side_effect=[None, RuntimeError("second failed"), None]
    )

    result = _run(
        entity,
        "Schalte das Flurlicht ein und schalte das Küchenlicht aus "
        "und schalte Drittes Licht ein",
        "multi-failure",
    )

    assert "second failed" in result.response.speech
    assert entity.hass.services.async_call.await_count == 2


def test_state_query_returns_query_answer_and_does_not_call_service(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_ON])

    result = _run(entity, "Ist das Flurlicht an?")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert "Flurlicht" in result.response.speech


@pytest.mark.parametrize(
    ("entity_snapshot", "question", "expected"),
    (
        (RADIO_ATLAS, "Spielt das Radio Atlas?", "Ja,"),
        (RADIO_ATLAS, "Pausiert das Radio Atlas?", "Nein,"),
        (SAUGROBOTER_ATLAS, "Startet der Saugroboter Atlas?", "nicht sicher"),
        (SAUGROBOTER_ATLAS, "Stoppt der Saugroboter Atlas?", "nicht sicher"),
    ),
)
def test_verb_first_state_question_is_live_and_never_calls_service(
    monkeypatch, entity_snapshot, question, expected
):
    entity = _make_entity(monkeypatch, [entity_snapshot])

    result = _run(entity, question)

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code is None
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert expected in result.response.speech


def test_verb_question_keeps_focus_for_pronoun_and_explanation(monkeypatch):
    entity = _make_entity(monkeypatch, [RADIO_ATLAS])

    first = _run(entity, "Spielt das Radio Atlas?", "verb-context")
    followup = _run(entity, "Läuft es noch?", "verb-context")
    explanation = _run(entity, "Woher weißt du das?", "verb-context")

    entity.hass.services.async_call.assert_not_awaited()
    assert first.response.speech.startswith("Ja,")
    assert followup.response.speech.startswith("Ja,")
    assert "Home-Assistant-Zustand" in explanation.response.speech
    assert "kein Dienst" in explanation.response.speech


def test_elliptical_query_reuses_predicate_for_new_explicit_target(monkeypatch):
    second_radio = EntitySnapshot(
        "media_player.birke", "Radio Birke", "media_player", "paused"
    )
    entity = _make_entity(monkeypatch, [RADIO_ATLAS, second_radio])

    _run(entity, "Spielt das Radio Atlas?", "verb-new-target")
    followup = _run(entity, "Und das Radio Birke?", "verb-new-target")

    entity.hass.services.async_call.assert_not_awaited()
    assert followup.response.speech.startswith("Nein,")
    assert "Radio Birke" in followup.response.speech
    assert followup.response.response_type == intent.IntentResponseType.QUERY_ANSWER


def test_other_reference_uses_remaining_device_in_same_area(monkeypatch):
    first = EntitySnapshot(
        "media_player.atlas", "Radio Atlas", "media_player", "playing",
        area_id="wohnzimmer", area_name="Wohnzimmer",
    )
    second = EntitySnapshot(
        "media_player.birke", "Radio Birke", "media_player", "paused",
        area_id="wohnzimmer", area_name="Wohnzimmer",
    )
    entity = _make_entity(monkeypatch, [first, second])

    _run(entity, "Spielt das Radio Atlas?", "verb-other")
    followup = _run(entity, "Und der andere?", "verb-other")

    entity.hass.services.async_call.assert_not_awaited()
    assert followup.response.speech.startswith("Nein,")
    assert "Radio Birke" in followup.response.speech


def test_ambiguous_verb_question_names_candidates_and_accepts_short_reply(monkeypatch):
    second_radio = EntitySnapshot(
        "media_player.birke", "Radio Birke", "media_player", "paused"
    )
    entity = _make_entity(monkeypatch, [RADIO_ATLAS, second_radio])

    question = _run(entity, "Spielt das Radio?", "verb-clarification")
    answer = _run(entity, "Radio Birke", "verb-clarification")

    entity.hass.services.async_call.assert_not_awaited()
    assert "Radio Atlas oder Radio Birke" in question.response.speech
    assert answer.response.speech.startswith("Nein,")
    assert "Radio Birke" in answer.response.speech


def test_location_only_followup_reuses_verb_predicate(monkeypatch):
    kitchen_radio = EntitySnapshot(
        "media_player.atlas", "Radio Atlas", "media_player", "playing",
        area_id="kueche", area_name="Küche",
    )
    living_radio = EntitySnapshot(
        "media_player.birke", "Radio Birke", "media_player", "paused",
        area_id="wohnzimmer", area_name="Wohnzimmer",
    )
    entity = _make_entity(monkeypatch, [kitchen_radio, living_radio])

    _run(entity, "Spielt das Radio Atlas?", "verb-location")
    followup = _run(entity, "Und im Wohnzimmer?", "verb-location")

    entity.hass.services.async_call.assert_not_awaited()
    assert followup.response.speech.startswith("Nein,")
    assert "Radio Birke" in followup.response.speech
    assert followup.response.response_type == intent.IntentResponseType.QUERY_ANSWER


def test_household_presence_query_is_live_and_strictly_read_only(monkeypatch):
    philipp = EntitySnapshot("person.philipp", "Philipp", "person", "home")
    entity = _make_entity(monkeypatch, [philipp])

    result = _run(entity, "Wer ist zuhause?")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert "Zuhause: Philipp" in result.response.speech


def test_capability_readiness_audit_is_live_and_strictly_read_only(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF, EG_TEMPERATUR])

    result = _run(entity, "Ist HomeIntent bereit?")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert "2 freigegebene Entities" in result.response.speech


def test_cover_group_without_floor_assignment_returns_actionable_error(monkeypatch):
    cover = EntitySnapshot(
        "cover.buero", "Rollladen Büro", "cover", "closed",
        area_id="buero", area_name="Büro",
        capabilities=frozenset({"POSITION"}),
    )
    entity = _make_entity(monkeypatch, [cover])

    result = _run(entity, "Fahre alle Rolladen im Erdgeschoss halb runter")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    assert result.response.speech == (
        "Ich kenne den Bereich oder die Etage „Erdgeschoss“ nicht. "
        "Bitte ordne die Geräte in Home Assistant einem Bereich und den Bereich einer Etage zu."
    )


def test_complete_group_command_supersedes_pending_cover_clarification(monkeypatch):
    covers = [
        EntitySnapshot(
            "cover.links", "Rollladen Esszimmer", "cover", "closed",
            area_id="esszimmer", area_name="Esszimmer",
        ),
        EntitySnapshot(
            "cover.rechts", "Rollladen Esszimmer", "cover", "closed",
            area_id="esszimmer", area_name="Esszimmer",
        ),
    ]
    entity = _make_entity(monkeypatch, covers)

    ambiguous = _run(
        entity,
        "Fahre den Rollladen im Esszimmer hoch",
        conversation_id="cover-replacement",
    )
    replacement = _run(
        entity,
        "Fahre alle Rolladen im Esszimmer hoch",
        conversation_id="cover-replacement",
    )

    assert ambiguous.response.speech == (
        "Ich habe mehrere passende Rollläden gefunden: "
        "1. Rollladen Esszimmer im Bereich Esszimmer (cover.links); "
        "2. Rollladen Esszimmer im Bereich Esszimmer (cover.rechts). "
        "Welchen meinst du?"
    )
    assert replacement.response.speech == "2 Rollläden geöffnet."
    entity.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "open_cover",
        {"entity_id": ["cover.links", "cover.rechts"]},
        blocking=True,
    )


def test_live_ambiguity_lists_candidates_and_executes_selected_ordinal(monkeypatch):
    lights = [
        EntitySnapshot(
            "light.a", "Bürolicht", "light", "off",
            area_id="buero_1", area_name="Büro 1",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
        EntitySnapshot(
            "light.b", "Bürolicht", "light", "off",
            area_id="buero_2", area_name="Büro 2",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
    ]
    entity = _make_entity(monkeypatch, lights)

    question = _run(entity, "Mach das Bürolicht an", "listed-selection")
    entity.hass.services.async_call.assert_not_awaited()
    answer = _run(entity, "das zweite", "listed-selection")

    assert "1. Bürolicht im Bereich Büro 1" in question.response.speech
    assert "2. Bürolicht im Bereich Büro 2" in question.response.speech
    assert "Bürolicht eingeschaltet" in answer.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_on", {"entity_id": "light.b"}, blocking=True,
    )


def test_invalid_clarification_reply_keeps_dialog_until_valid_selection(monkeypatch):
    lights = [
        EntitySnapshot(
            "light.a", "Leselicht", "light", "off",
            area_id="links", area_name="Links",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
        EntitySnapshot(
            "light.b", "Leselicht", "light", "off",
            area_id="rechts", area_name="Rechts",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
    ]
    entity = _make_entity(monkeypatch, lights)

    _run(entity, "Mach das Leselicht an", "retry-selection")
    retry = _run(entity, "das unbekannte", "retry-selection")
    entity.hass.services.async_call.assert_not_awaited()
    answer = _run(entity, "Nummer 2", "retry-selection")

    assert "keinem der angebotenen Geräte" in retry.response.speech
    assert "Leselicht eingeschaltet" in answer.response.speech
    entity.hass.services.async_call.assert_awaited_once()


def test_live_multiple_fuzzy_names_ask_before_selected_execution(monkeypatch):
    lights = [
        EntitySnapshot(
            "light.a", "Küchenlicht", "light", "off",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
        EntitySnapshot(
            "light.b", "Kuchenlicht", "light", "off",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
    ]
    entity = _make_entity(monkeypatch, lights)

    question = _run(entity, "Mach Kuechenlict an", "fuzzy-selection")
    entity.hass.services.async_call.assert_not_awaited()
    _run(entity, "Nummer 1", "fuzzy-selection")

    assert "mehrere passende Lichter" in question.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_on", {"entity_id": "light.a"}, blocking=True,
    )


def test_partial_shared_name_lists_distinct_devices_before_execution(monkeypatch):
    lights = [
        EntitySnapshot("light.left", "Deckenlicht links", "light", "off"),
        EntitySnapshot("light.right", "Deckenlicht rechts", "light", "off"),
    ]
    entity = _make_entity(monkeypatch, lights)

    question = _run(entity, "Mach das Deckenlicht an", "partial-selection")
    entity.hass.services.async_call.assert_not_awaited()
    answer = _run(entity, "das rechte", "partial-selection")

    assert "Deckenlicht links" in question.response.speech
    assert "Deckenlicht rechts" in question.response.speech
    assert "Deckenlicht rechts eingeschaltet" in answer.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_on", {"entity_id": "light.right"}, blocking=True,
    )


def test_single_typo_requires_confirmation_before_execution(monkeypatch):
    light = EntitySnapshot("light.left", "Deckenlicht links", "light", "off")
    entity = _make_entity(monkeypatch, [light])

    question = _run(entity, "Mach Deckenlicth links an", "typo-confirmation")
    entity.hass.services.async_call.assert_not_awaited()
    answer = _run(entity, "ja", "typo-confirmation")

    assert "Deckenlicht links" in question.response.speech
    assert "meintest du" in question.response.speech.casefold()
    assert "Deckenlicht links eingeschaltet" in answer.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_on", {"entity_id": "light.left"}, blocking=True,
    )


def test_selected_entity_disappearing_between_turns_is_never_executed(monkeypatch):
    lights = [
        EntitySnapshot(
            "light.a", "Leselicht", "light", "off",
            area_id="a", area_name="Bereich A",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
        EntitySnapshot(
            "light.b", "Leselicht", "light", "off",
            area_id="b", area_name="Bereich B",
            capabilities=frozenset({"TURN_ON", "TURN_OFF"}),
        ),
    ]
    entity = _make_entity(monkeypatch, lights)

    _run(entity, "Mach das Leselicht an", "stale-selection")
    lights.pop()
    result = _run(entity, "Nummer 2", "stale-selection")

    assert "nicht mehr verfügbar" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_clarification_can_be_cancelled_without_execution(monkeypatch):
    lights = [
        EntitySnapshot("light.a", "Leselicht", "light", "off"),
        EntitySnapshot("light.b", "Leselicht", "light", "off"),
    ]
    entity = _make_entity(monkeypatch, lights)

    _run(entity, "Mach das Leselicht an", "cancel-selection")
    result = _run(entity, "keins davon", "cancel-selection")

    assert result.response.speech == "Abgebrochen. Ich führe nichts aus."
    entity.hass.services.async_call.assert_not_awaited()


def test_selected_entity_losing_capability_is_revalidated(monkeypatch):
    valves = [
        EntitySnapshot(
            "valve.a", "Gartenventil", "valve", "closed",
            area_id="a", area_name="Bereich A",
            capabilities=frozenset({"OPEN", "CLOSE"}),
        ),
        EntitySnapshot(
            "valve.b", "Gartenventil", "valve", "closed",
            area_id="b", area_name="Bereich B",
            capabilities=frozenset({"OPEN", "CLOSE"}),
        ),
    ]
    entity = _make_entity(monkeypatch, valves)

    _run(entity, "Öffne das Gartenventil", "capability-selection")
    valves[1] = replace(valves[1], capabilities=frozenset())
    result = _run(entity, "Nummer 2", "capability-selection")

    assert "nicht mehr verfügbar" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_live_cover_percentage_asr_correction_never_becomes_heating_dialog(
    monkeypatch,
):
    entities = [
        EntitySnapshot(
            "cover.left", "Rolllade Poleraum links", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.right", "Rolllade Poleraum rechts", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "climate.kitchen", "Heizung Küche", "climate", "heat",
            capabilities=frozenset({"TEMPERATURE"}),
        ),
        EntitySnapshot(
            "climate.bath", "Heizung Bad", "climate", "heat",
            capabilities=frozenset({"TEMPERATURE"}),
        ),
    ]
    entity = _make_entity(monkeypatch, entities)

    typo = _run(
        entity,
        "Fahre die Rolletten im Polraum auf 30 Prozent.",
        "cover-asr",
    )
    correction = _run(
        entity,
        "Fahre die Rollladen im Polraum auf 30 Prozent.",
        "cover-asr",
    )
    confirmed = _run(entity, "Ja", "cover-asr")

    assert "Heizung" not in typo.response.speech
    assert "Meintest du" in correction.response.speech
    assert "poleraum" in correction.response.speech.casefold()
    assert confirmed.response.speech == "2 Rollläden auf 30 Prozent gefahren."
    entity.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": ["cover.left", "cover.right"], "position": 30},
        blocking=True,
    )


def test_complete_cover_command_supersedes_pending_temperature_dialog(monkeypatch):
    entities = [
        EntitySnapshot(
            "cover.left", "Rolllade Poleraum links", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "cover.right", "Rolllade Poleraum rechts", "cover", "open",
            area_id="pole_raum", area_name="Pole Raum",
            capabilities=frozenset({"POSITION"}),
        ),
        EntitySnapshot(
            "climate.kitchen", "Heizung Küche", "climate", "heat",
            capabilities=frozenset({"TEMPERATURE"}),
        ),
        EntitySnapshot(
            "climate.bath", "Heizung Bad", "climate", "heat",
            capabilities=frozenset({"TEMPERATURE"}),
        ),
    ]
    entity = _make_entity(monkeypatch, entities)

    pending = _run(entity, "Stelle auf 22 Grad", "dialog-switch")
    replacement = _run(
        entity,
        "Fahre die Rollläden im Poleraum auf 30 Prozent",
        "dialog-switch",
    )

    assert pending.response.speech == "Welche Heizung meinst du?"
    assert replacement.response.speech == "2 Rollläden auf 30 Prozent gefahren."
    entity.hass.services.async_call.assert_awaited_once_with(
        "cover",
        "set_cover_position",
        {"entity_id": ["cover.left", "cover.right"], "position": 30},
        blocking=True,
    )


def test_trigger_and_action_can_be_spoken_in_two_turns(monkeypatch):
    entity = _make_entity(monkeypatch, [KUECHE_FENSTER, KUECHE_LICHT])

    question = _run(entity, "Wenn das Küchenfenster geöffnet wird")
    preview = _run(entity, "Schalte das Küchenlicht ein")

    entity.hass.services.async_call.assert_not_awaited()
    assert question.response.speech == "Was soll dann passieren?"
    assert "Fenster im Bereich Küche" in preview.response.speech
    assert "Licht im Bereich Küche" in preview.response.speech
    context = entity._context_store.get("conv-1")
    assert context.pending_automation_confirmation is not None


def test_misspelled_negation_is_blocked_before_every_live_router(monkeypatch):
    entity = _make_entity(monkeypatch, [KUECHE_LICHT])

    result = _run(entity, "Mach das Küchenlicht nciht an")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code is intent.IntentResponseErrorCode.NO_INTENT_MATCH
    assert "führe nichts aus" in result.response.speech


@pytest.mark.parametrize(
    "sentence",
    (
        "Wie warm ist es im Erdgeschoss?",
        "Wie hoch ist die Temperatur im Erdgeschoss?",
        "Welche Temperatur hat das Erdgeschoss?",
    ),
)
def test_floor_temperature_question_reaches_live_conversation_query(
    monkeypatch, sentence
):
    entity = _make_entity(monkeypatch, [EG_TEMPERATUR])

    result = _run(entity, sentence)

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code is None
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    assert result.response.speech == "22,4 Grad."


def test_floor_temperature_question_lists_multiple_sensors(monkeypatch):
    second = EntitySnapshot(
        "sensor.wohnzimmer_temperatur",
        "Wohnzimmer Temperatur",
        "sensor",
        "21.8",
        area_id="wohnzimmer",
        area_name="Wohnzimmer",
        floor_id="eg",
        floor_name="Erdgeschoss",
        floor_level=0,
        unit="°C",
        device_class="temperature",
    )
    entity = _make_entity(monkeypatch, [EG_TEMPERATUR, second])

    question = _run(
        entity,
        "Welche Temperatur hat das Erdgeschoss?",
        conversation_id="floor-temp",
    )
    assert question.response.speech == (
        "Küche Temperatur: 22,4 Grad; Wohnzimmer Temperatur: 21,8 Grad."
    )


def test_temperature_query_then_contextual_setpoint_reaches_live_service(monkeypatch):
    thermostat = EntitySnapshot(
        "climate.erdgeschoss",
        "Erdgeschoss Heizung",
        "climate",
        "heat",
        area_id="kueche",
        area_name="Küche",
        floor_id="eg",
        floor_name="Erdgeschoss",
        floor_level=0,
        attributes={"temperature": 21},
        capabilities=frozenset({"TEMPERATURE"}),
    )
    entity = _make_entity(monkeypatch, [EG_TEMPERATUR, thermostat])

    query = _run(
        entity,
        "Wie hoch ist die Temperatur im Erdgeschoss?",
        conversation_id="logical-temperature",
    )
    command = _run(
        entity,
        "Kannst du die Temperatur auf 22 Grad erhöhen?",
        conversation_id="logical-temperature",
    )

    assert query.response.speech == "22,4 Grad."
    assert command.response.speech == "Erdgeschoss Heizung auf 22 Grad gestellt."
    entity.hass.services.async_call.assert_awaited_once_with(
        "climate",
        "set_temperature",
        {"entity_id": "climate.erdgeschoss", "temperature": 22},
        blocking=True,
    )


def test_unmatched_sentence_returns_not_understood(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    result = _run(entity, "Erzähl mir bitte einen Witz")

    entity.hass.services.async_call.assert_not_awaited()
    assert result.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH


def test_service_call_exception_produces_clean_failed_to_handle_response(monkeypatch, caplog):
    """Milestone 0 regression: a service-call failure must produce a clean
    FAILED_TO_HANDLE response, not propagate as "Unexpected error during
    intent recognition" (the broadened ``except Exception`` in
    conversation.py's two service-call sites)."""
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    entity.hass.services.async_call = ServiceMock(side_effect=RuntimeError("boom"))

    result = _run(entity, "Schalte das Flurlicht ein")

    assert result.response.error_code == intent.IntentResponseErrorCode.FAILED_TO_HANDLE
    assert "boom" in result.response.speech
    assert "Service call homeassistant.turn_on" in caplog.text


def _store_confirmation(entity, conversation_id: str) -> None:
    entity._context_store.set(
        conversation_id,
        ConversationContext(
            last_command=None,
            last_entities=(),
            last_area=None,
            pending_clarification=None,
            pending_service_confirmation=PendingServiceConfirmation(
                ServiceCallPlan(
                    "homeassistant", "turn_on", FLUR_LICHT_OFF.entity_id, {}
                ),
                "Flurlicht eingeschaltet.",
            ),
        ),
    )


def test_service_confirmation_retries_declines_and_executes(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    _store_confirmation(entity, "confirm-unclear")
    unclear = _run(entity, "vielleicht", "confirm-unclear")
    assert unclear.response.speech == "Bitte antworte mit Ja oder Nein."
    entity.hass.services.async_call.assert_not_awaited()

    _store_confirmation(entity, "confirm-no")
    declined = _run(entity, "nein", "confirm-no")
    assert declined.response.speech == "Abgebrochen. Es wurde nichts ausgeführt."
    entity.hass.services.async_call.assert_not_awaited()

    _store_confirmation(entity, "confirm-yes")
    accepted = _run(entity, "ja", "confirm-yes")
    assert accepted.response.speech == "Flurlicht eingeschaltet."
    entity.hass.services.async_call.assert_awaited_once_with(
        "homeassistant", "turn_on", {"entity_id": FLUR_LICHT_OFF.entity_id},
        blocking=True,
    )


def test_confirmed_service_failure_is_reported_without_success(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    entity.hass.services.async_call = ServiceMock(side_effect=RuntimeError("confirm boom"))
    _store_confirmation(entity, "confirm-failure")

    result = _run(entity, "ja", "confirm-failure")

    assert "confirm boom" in result.response.speech
    assert result.response.error_code == intent.IntentResponseErrorCode.FAILED_TO_HANDLE


def test_legacy_confirmation_uses_central_explain_understood_and_cancel(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    conversation_id = "central-meta"
    _store_confirmation(entity, conversation_id)

    why = _run(entity, "Warum fragst du?", conversation_id)
    understood = _run(entity, "Was hast du verstanden?", conversation_id)
    cancelled = _run(entity, "Lass das", conversation_id)
    later_yes = _run(entity, "Ja", conversation_id)

    assert "führe bis zur Klärung nichts aus" in why.response.speech
    assert "SERVICE_CONFIRMATION" in understood.response.speech
    assert "verworfen" in cancelled.response.speech
    assert later_yes.response.error_code == intent.IntentResponseErrorCode.NO_INTENT_MATCH
    entity.hass.services.async_call.assert_not_awaited()


def test_undo_reports_empty_context_then_reverses_last_light_action(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    empty = _run(entity, "Mach das rückgängig", "undo-empty")
    assert "keine kürzlich" in empty.response.speech

    _run(entity, "Schalte das Flurlicht ein", "undo-success")
    undone = _run(entity, "Mach das rückgängig", "undo-success")

    assert undone.response.speech == "Die letzte Aktion wurde rückgängig gemacht."
    assert entity.hass.services.async_call.await_count == 2
    assert entity.hass.services.async_call.await_args_list[-1].args[:2] == (
        "light", "turn_off"
    )


def test_undo_service_failure_is_cleanly_reported(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    _run(entity, "Schalte das Flurlicht ein", "undo-failure")
    entity.hass.services.async_call = ServiceMock(side_effect=RuntimeError("undo boom"))

    result = _run(entity, "Mach das rückgängig", "undo-failure")

    assert "undo boom" in result.response.speech


def test_explanation_reports_empty_and_last_structured_command(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    empty = _run(entity, "Was hast du verstanden?", "explain-empty")
    assert "noch keinen verstandenen Befehl" in empty.response.speech

    _run(entity, "Schalte das Flurlicht ein", "explain-command")
    explained = _run(entity, "Wie hast du das verstanden?", "explain-command")

    assert "Aktion: einschalten" in explained.response.speech
    assert "Ziel: Flurlicht" in explained.response.speech
    assert explained.response.response_type == intent.IntentResponseType.QUERY_ANSWER


def test_execution_audit_is_read_only_and_names_recorded_action(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    _run(entity, "Schalte das Flurlicht ein", "audit-action")
    entity.hass.services.async_call.reset_mock()

    result = _run(
        entity, "Was wurde heute durch HomeIntent ausgeführt?", "audit-query"
    )

    assert "light.flur_licht" in result.response.speech
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    entity.hass.services.async_call.assert_not_awaited()


def test_advanced_unavailable_query_is_read_only(monkeypatch):
    unavailable = EntitySnapshot(
        "light.offline", "Offline Licht", "light", "unavailable"
    )
    entity = _make_entity(monkeypatch, [unavailable])

    result = _run(entity, "Welche Geräte sind nicht erreichbar?", "advanced")

    assert "Offline Licht" in result.response.speech
    assert result.response.response_type == intent.IntentResponseType.QUERY_ANSWER
    entity.hass.services.async_call.assert_not_awaited()


def test_undo_service_failure_is_logged(monkeypatch, caplog):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    _run(entity, "Schalte das Flurlicht ein", conversation_id="undo-failure")
    entity.hass.services.async_call.reset_mock()
    entity.hass.services.async_call.side_effect = RuntimeError("undo unavailable")

    result = _run(entity, "Mach das rückgängig", conversation_id="undo-failure")

    assert "undo unavailable" in result.response.speech
    assert "Undo service call failed" in caplog.text


def test_automation_management_failure_is_logged(monkeypatch, caplog):
    entity = _make_entity(monkeypatch, [])
    entity._automation_executor = AsyncMock()
    entity._automation_executor.async_cleanup_expired_scheduled_automations.side_effect = (
        RuntimeError("yaml unavailable")
    )
    response = intent.IntentResponse(language="de")
    user_input = ConversationInput(text="Ja", conversation_id="management-failure")
    pending = PendingAutomationManagement(
        AutomationManagementRequest(AutomationManagementKind.CLEAN_EXPIRED)
    )

    result = asyncio.run(
        entity._async_handle_automation_management_confirmation(
            user_input, response, pending
        )
    )

    assert "yaml unavailable" in result.response.speech
    assert "Automation management failed" in caplog.text


def test_pronoun_follow_up_reuses_context_from_previous_command(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])

    _run(entity, "Schalte das Flurlicht ein", conversation_id="conv-followup")
    entity.hass.services.async_call.reset_mock()

    result = _run(entity, "Mach es aus", conversation_id="conv-followup")

    entity.hass.services.async_call.assert_awaited_once()
    call_args = entity.hass.services.async_call.await_args
    assert call_args.args[0] == "homeassistant"
    assert call_args.args[1] == "turn_off"
    assert call_args.args[2]["entity_id"] == "light.flur_licht"
    assert result.response.error_code is None


def test_unclaimed_yes_can_resolve_one_open_tts_agent_question(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    voice_reply = AsyncMock(return_value="Erledigt.")
    entity._runtime_data.proactive_agent = type(
        "AgentStub", (), {"async_handle_voice_reply": voice_reply}
    )()

    result = _run(entity, "Ja", conversation_id="agent-voice")

    assert result.response.speech == "Erledigt."
    voice_reply.assert_awaited_once()
    entity.hass.services.async_call.assert_not_awaited()


def test_alias_confirmation_payload_is_owned_by_central_dialog_manager(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    updater = Mock()
    entity.hass.config_entries = type(
        "ConfigEntries", (), {"async_update_entry": updater}
    )()

    question = _run(
        entity, "Nenne Flurlicht künftig Orientierungslicht", "native-alias"
    )
    task = entity._runtime_data.dialog_manager.active("native-alias")

    assert "Soll ich" in question.response.speech
    assert task is not None and task.kind is DialogTaskKind.ALIAS_CONFIRMATION
    assert task.payload is not None
    assert entity._context_store.get("native-alias") is None

    explanation = _run(entity, "Warum fragst du?", "native-alias")
    result = _run(entity, "Ja", "native-alias")

    assert "ausdrücklich bestätigt" in explanation.response.speech
    assert "Gespeichert" in result.response.speech
    assert entity._runtime_data.dialog_manager.active("native-alias") is None
    # 7.4.1: learned aliases live in the one bindings store, not in options.
    updater.assert_not_called()
    aliases = entity._runtime_data.bindings.all(BindingKind.ALIAS)
    assert [(item.data["spoken"], item.target) for item in aliases] == [
        ("Orientierungslicht", FLUR_LICHT_OFF.entity_id)
    ]
    entity.hass.services.async_call.assert_not_awaited()


def test_complete_command_replaces_native_alias_confirmation(monkeypatch):
    entity = _make_entity(monkeypatch, [FLUR_LICHT_OFF])
    updater = Mock()
    entity.hass.config_entries = type(
        "ConfigEntries", (), {"async_update_entry": updater}
    )()
    _run(entity, "Nenne Flurlicht künftig Orientierungslicht", "replace-alias")

    result = _run(entity, "Schalte das Flurlicht ein", "replace-alias")

    assert result.response.error_code is None
    assert entity._runtime_data.dialog_manager.active("replace-alias") is None
    updater.assert_not_called()
    entity.hass.services.async_call.assert_awaited_once()


def test_declining_the_missing_action_discards_the_draft(monkeypatch):
    entity = _make_entity(monkeypatch, [KUECHE_FENSTER, KUECHE_LICHT])

    _run(entity, "Wenn das Küchenfenster geöffnet wird")
    declined = _run(entity, "Nein.")

    assert declined.response.speech == "In Ordnung. Ich lege keine Automation an."
    assert declined.continue_conversation is False
    assert entity._context_store.get("conv-1") is None
    entity.hass.services.async_call.assert_not_awaited()
