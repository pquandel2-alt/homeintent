"""Extended direct-device routing and high-risk confirmation gates."""

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
from homeintent.const import SELECTABLE_DOMAINS  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from _v7_device import understand_device as match_device_control  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeintent.nlu.language_frontend import analyse_language  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

ENTITIES = [
    EntitySnapshot(
        "climate.heizung",
        "Wohnzimmer Heizung",
        "climate",
        "heat",
        attributes={
            "hvac_modes": ["heat", "cool"],
            "min_temp": 8,
            "max_temp": 28,
        },
        capabilities=frozenset({"TEMPERATURE"}),
    ),
    EntitySnapshot(
        "media_player.tv",
        "Wohnzimmer TV",
        "media_player",
        "idle",
        attributes={"source_list": ["HDMI 1", "Netflix"]},
    ),
    EntitySnapshot("vacuum.robi", "Saugroboter", "vacuum", "docked"),
    EntitySnapshot("scene.filmabend", "Filmabend", "scene", "scening"),
    EntitySnapshot("lock.haustuer", "Haustür", "lock", "locked"),
    EntitySnapshot(
        "cover.garage",
        "Garagentor",
        "cover",
        "closed",
        device_class="garage",
    ),
]

EXTENDED_ENTITIES = ENTITIES + [
    EntitySnapshot(
        "humidifier.schlafzimmer", "Luftbefeuchter Schlafzimmer", "humidifier", "on",
        attributes={"min_humidity": 30, "max_humidity": 70},
    ),
    EntitySnapshot(
        "water_heater.boiler", "Warmwasser Boiler", "water_heater", "eco",
        attributes={"min_temp": 40, "max_temp": 75},
    ),
    EntitySnapshot(
        "select.lueftung", "Lüftungsprofil", "select", "Normal",
        attributes={"options": ["Leise", "Normal", "Intensiv"]},
    ),
    EntitySnapshot(
        "number.lautstaerke", "Türklingel Lautstärke", "number", "30",
        attributes={"min": 0, "max": 100},
    ),
    EntitySnapshot("input_boolean.gastmodus", "Gastmodus", "input_boolean", "off"),
    EntitySnapshot("button.neustart", "Router Neustart", "button", "unknown"),
    EntitySnapshot(
        "valve.gas", "Gasventil", "valve", "closed", device_class="gas",
        attributes={"supported_features": 3},
    ),
    EntitySnapshot(
        "lawn_mower.garten", "Mähroboter", "lawn_mower", "docked",
        attributes={"supported_features": 7},
    ),
    EntitySnapshot("camera.einfahrt", "Einfahrt Kamera", "camera", "idle"),
    EntitySnapshot("notify.handys", "Familienhandys", "notify", "unknown"),
]


def test_climate_mode_is_checked_against_reported_modes():
    result = match_device_control(
        "Stelle Wohnzimmer Heizung auf Kühlbetrieb", ENTITIES
    )
    assert result.plan.service == "set_hvac_mode"
    assert result.plan.data == {"hvac_mode": "cool"}

    unsupported = match_device_control(
        "Stelle Wohnzimmer Heizung auf Automatik", ENTITIES
    )
    assert unsupported is None


def test_device_router_reuses_shared_language_document():
    text = "Stelle die Lautstärke von Wohnzimmer TV auf 35 Prozent"
    document = analyse_language(text, ENTITIES)
    result = match_device_control(text, ENTITIES, document)
    assert result is not None and result.plan is not None
    assert result.plan.service == "volume_set"


def test_climate_temperature_is_checked_against_reported_range():
    result = match_device_control(
        "Stelle Wohnzimmer Heizung auf 21,5 Grad", ENTITIES
    )
    assert result.plan.service == "set_temperature"
    assert result.plan.data == {"temperature": 21.5}

    unsupported = match_device_control(
        "Stelle Wohnzimmer Heizung auf 30 Grad", ENTITIES
    )
    assert unsupported is None


def test_media_playback_volume_and_source():
    play = match_device_control("Starte Wiedergabe auf Wohnzimmer TV", ENTITIES)
    volume = match_device_control(
        "Stelle die Lautstärke von Wohnzimmer TV auf 35 Prozent", ENTITIES
    )
    source = match_device_control(
        "Wähle auf Wohnzimmer TV die Quelle HDMI 1", ENTITIES
    )
    assert play.plan.service == "media_play"
    assert volume.plan.data == {"volume_level": 0.35}
    assert source.plan.data == {"source": "HDMI 1"}


def test_vacuum_and_scene_services_are_domain_native():
    vacuum = match_device_control("Schicke Saugroboter zur Ladestation", ENTITIES)
    scene = match_device_control("Aktiviere Szene Filmabend", ENTITIES)
    assert (vacuum.plan.domain, vacuum.plan.service) == ("vacuum", "return_to_base")
    assert (scene.plan.domain, scene.plan.service) == ("scene", "turn_on")


@pytest.mark.parametrize(
    ("sentence", "domain", "service", "data"),
    (
        ("Wohnzimmer Heizung bitte auf 22 Grad stellen", "climate", "set_temperature", {"temperature": 22.0}),
        ("Auf 35 Prozent bitte die Lautstärke vom Wohnzimmer TV stellen", "media_player", "volume_set", {"volume_level": 0.35}),
        ("Auf dem Wohnzimmer TV bitte die Wiedergabe starten", "media_player", "media_play", {}),
        ("Netflix als Quelle auf dem Wohnzimmer TV auswählen", "media_player", "select_source", {"source": "Netflix"}),
        ("Den Saugroboter bitte starten", "vacuum", "start", {}),
        ("Zur Ladestation soll der Saugroboter fahren", "vacuum", "return_to_base", {}),
        ("Filmabend bitte aktivieren", "scene", "turn_on", {}),
    ),
)
def test_extended_domain_slots_are_order_independent(sentence, domain, service, data):
    result = match_device_control(sentence, ENTITIES)

    assert result is not None
    assert result.plan is not None
    assert (result.plan.domain, result.plan.service) == (domain, service)
    assert result.plan.data == data


@pytest.mark.parametrize(
    "sentence",
    (
        "Wohnzimmer Heizung bitte nicht auf 22 Grad stellen",
        "Vielleicht den Saugroboter starten",
        "Filmabend normalerweise aktivieren",
    ),
)
def test_extended_domain_composition_rejects_unsafe_modifiers(sentence):
    assert match_device_control(sentence, ENTITIES) is None


@pytest.mark.parametrize(
    ("sentence", "service", "data"),
    (
        (
            "Wäre es möglich, die Wohnzimmer Heizung auf zweiundzwanzig Grad zu stellen?",
            "set_temperature",
            {"temperature": 22.0},
        ),
        (
            "Ich hätte gerne die Lautstärke vom Wohnzimmer TV auf fünfunddreißig Prozent",
            "volume_set",
            {"volume_level": 0.35},
        ),
        ("Sorge bitte dafür, dass der Saugroboter startet", "start", {}),
    ),
)
def test_natural_shells_and_number_words_apply_to_all_device_domains(sentence, service, data):
    result = match_device_control(sentence, ENTITIES)

    assert result is not None
    assert result.plan is not None
    assert result.plan.service == service
    assert result.plan.data == data


@pytest.mark.parametrize(
    "sentence",
    (
        "Wenn es warm ist, stelle die Wohnzimmer Heizung auf 22 Grad",
        "Was passiert, wenn ich den Saugroboter starte?",
        "Kann man die Wohnzimmer Heizung auf 22 Grad stellen?",
    ),
)
def test_extended_device_router_never_executes_conditions_or_information_questions(sentence):
    assert match_device_control(sentence, ENTITIES) is None


def _conversation_entity(monkeypatch, tmp_path):
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    entity.hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    entity.hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: ENTITIES
    )
    return entity


def _run(entity, text, conversation_id="safe", *, authenticated=True):
    return asyncio.run(
        entity._async_handle_message(
            ConversationInput(
                text=text,
                conversation_id=conversation_id,
                context=(SimpleNamespace(user_id="owner") if authenticated else None),
            ),
            chat_log=None,
        )
    )


def test_unlock_requires_explicit_confirmation(monkeypatch, tmp_path):
    entity = _conversation_entity(monkeypatch, tmp_path)

    preview = _run(entity, "Schließe Haustür auf")
    assert "wirklich" in preview.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    confirmed = _run(entity, "Ja")
    assert "aufgeschlossen" in confirmed.response.speech
    entity.hass.services.async_call.assert_awaited_once_with(
        "lock", "unlock", {"entity_id": "lock.haustuer"}, blocking=True
    )


def test_anonymous_critical_voice_command_is_denied_before_confirmation(
    monkeypatch, tmp_path
):
    entity = _conversation_entity(monkeypatch, tmp_path)

    result = _run(entity, "Schließe Haustür auf", authenticated=False)

    assert "authentifizierten Benutzer" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_confirmed_service_failure_is_logged(monkeypatch, tmp_path, caplog):
    entity = _conversation_entity(monkeypatch, tmp_path)

    preview = _run(entity, "Schließe Haustür auf")
    assert "wirklich" in preview.response.speech
    entity.hass.services.async_call.side_effect = RuntimeError("lock unavailable")

    confirmed = _run(entity, "Ja")

    assert "lock unavailable" not in confirmed.response.speech
    assert "Home Assistant hat die Aktion nicht ausgeführt" in confirmed.response.speech
    assert "Confirmed service call lock.unlock" in caplog.text


def test_device_control_service_failure_is_logged(monkeypatch, tmp_path, caplog):
    entity = _conversation_entity(monkeypatch, tmp_path)
    entity.hass.services.async_call.side_effect = RuntimeError("player unavailable")

    result = _run(entity, "Starte Wiedergabe auf Wohnzimmer TV")

    assert "player unavailable" not in result.response.speech
    assert "Home Assistant hat die Aktion nicht ausgeführt" in result.response.speech
    # Media playback is in the V7 authority cohort and now uses the shared
    # validated service-call executor rather than the legacy device router.
    assert "Service call media_player.media_play" in caplog.text


def test_garage_movement_can_be_cancelled(monkeypatch, tmp_path):
    entity = _conversation_entity(monkeypatch, tmp_path)
    preview = _run(entity, "Öffne Garagentor")
    assert "wirklich" in preview.response.speech

    cancelled = _run(entity, "Nein")
    assert "Abgebrochen" in cancelled.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_every_extended_domain_is_selectable_in_live_home_assistant():
    assert {
        "climate", "media_player", "vacuum", "scene", "lock", "calendar",
        "humidifier", "water_heater", "select", "number", "input_number",
        "input_boolean", "button", "valve", "lawn_mower", "camera", "notify",
    } <= set(
        SELECTABLE_DOMAINS
    )


def test_every_household_query_domain_is_selectable_in_live_home_assistant():
    assert {"person", "weather", "sun"} <= set(SELECTABLE_DOMAINS)


@pytest.mark.parametrize(
    ("sentence", "domain", "service", "data"),
    (
        ("Stelle die Luftfeuchtigkeit vom Luftbefeuchter Schlafzimmer auf 55 Prozent", "humidifier", "set_humidity", {"humidity": 55}),
        ("Stelle den Warmwasser Boiler auf 60 Grad", "water_heater", "set_temperature", {"temperature": 60.0}),
        ("Wähle beim Lüftungsprofil Intensiv", "select", "select_option", {"option": "Intensiv"}),
        ("Stelle Türklingel Lautstärke auf 45", "number", "set_value", {"value": 45.0}),
        ("Schalte den Gastmodus ein", "homeassistant", "turn_on", {}),
        ("Starte den Mähroboter", "lawn_mower", "start_mowing", {}),
        ("Schicke den Mähroboter zur Ladestation", "lawn_mower", "dock", {}),
        ("Zeige die Einfahrt Kamera auf dem Wohnzimmer TV", "camera", "play_stream", {"media_player": "media_player.tv"}),
        ("Sende über Familienhandys die Nachricht Essen ist fertig", "notify", "send_message", {"message": "Essen ist fertig"}),
    ),
)
def test_additional_domains_are_composed_from_entity_operation_and_value(
    sentence, domain, service, data
):
    result = match_device_control(sentence, EXTENDED_ENTITIES)
    assert result is not None
    assert result.plan is not None
    assert (result.plan.domain, result.plan.service) == (domain, service)
    assert result.plan.data == data


@pytest.mark.parametrize(
    "sentence",
    ("Drücke Router Neustart", "Öffne Gasventil", "Schließe Gasventil"),
)
def test_additional_high_risk_domains_require_confirmation(sentence):
    result = match_device_control(sentence, EXTENDED_ENTITIES)
    assert result is not None
    assert result.plan is not None
    assert result.requires_confirmation


def test_additional_domain_values_and_features_are_checked():
    assert match_device_control(
        "Stelle die Luftfeuchtigkeit vom Luftbefeuchter Schlafzimmer auf 90 Prozent",
        EXTENDED_ENTITIES,
    ) is None
    unsupported_valve = EntitySnapshot(
        "valve.gas", "Gasventil", "valve", "closed", device_class="gas",
        attributes={"supported_features": 2},
    )
    assert match_device_control("Öffne Gasventil", [unsupported_valve]) is None
