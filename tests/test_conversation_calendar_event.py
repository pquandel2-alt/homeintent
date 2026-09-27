from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
sys.path.insert(0, str(Path(__file__).parent))

import _ha_stub  # noqa: E402

_ha_stub.install()

import homeintent.conversation as ha_conversation  # noqa: E402
import homeintent.management_dialogs as management_dialogs  # noqa: E402
from homeintent.conversation import NluConversationEntity  # noqa: E402
from homeintent.calendar_management import CalendarEventSummary  # noqa: E402
from homeintent.entities import EntitySnapshot  # noqa: E402
from homeassistant.components.conversation import ConversationInput  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
PRIVATE = EntitySnapshot(
    "calendar.privat", "Privat", "calendar", "off",
    capabilities=frozenset({"CREATE_EVENT"}),
)
FAMILY = EntitySnapshot(
    "calendar.familie", "Familie", "calendar", "off",
    capabilities=frozenset({"CREATE_EVENT"}),
)
MUTABLE = EntitySnapshot(
    "calendar.lokal", "Lokal", "calendar", "off",
    capabilities=frozenset({"CREATE_EVENT", "DELETE_EVENT", "UPDATE_EVENT"}),
)


def _make_entity(monkeypatch, calendars=(PRIVATE,)) -> NluConversationEntity:
    entity = NluConversationEntity(ConfigEntry())
    entity.hass = HomeAssistant()
    monkeypatch.setattr(
        ha_conversation, "build_entity_snapshots", lambda hass, entry: list(calendars)
    )
    monkeypatch.setattr(ha_conversation.dt_util, "now", lambda: NOW)
    return entity


def _run(entity, text, conversation_id="calendar-dialog"):
    return asyncio.run(
        entity._async_handle_message(
            ConversationInput(text=text, conversation_id=conversation_id), chat_log=None
        )
    )


def test_missing_fields_are_collected_and_only_confirmation_writes(monkeypatch):
    entity = _make_entity(monkeypatch)

    assert _run(entity, "Trag einen Termin ein").response.speech == "Wie soll der Termin heißen?"
    assert _run(entity, "Zahnarzt").response.speech == "An welchem Datum findet der Termin statt?"
    assert _run(entity, "nächsten Dienstag").response.speech.startswith("Um wie viel Uhr")
    assert _run(entity, "10 Uhr").response.speech == "Wann endet der Termin oder wie lange dauert er?"
    preview = _run(entity, "eine Stunde").response.speech
    assert "Termin „Zahnarzt“" in preview
    assert "Dienstag, 25. August 2026" in preview
    entity.hass.services.async_call.assert_not_awaited()

    result = _run(entity, "Ja")
    assert result.response.speech == "Der Termin wurde eingetragen."
    entity.hass.services.async_call.assert_awaited_once_with(
        "calendar",
        "create_event",
        {
            "summary": "Zahnarzt",
            "start_date_time": "2026-08-25T10:00:00+02:00",
            "end_date_time": "2026-08-25T11:00:00+02:00",
        },
        target={"entity_id": "calendar.privat"},
        blocking=True,
    )


def test_complete_sentence_previews_immediately_and_can_be_cancelled(monkeypatch):
    entity = _make_entity(monkeypatch)
    result = _run(
        entity, "Trag nächsten Dienstag um 10 Uhr für eine Stunde Zahnarzt ein"
    )
    assert "Soll ich den Termin eintragen?" in result.response.speech
    assert _run(entity, "Nein").response.speech == "Abgebrochen. Der Termin wurde nicht eingetragen."
    entity.hass.services.async_call.assert_not_awaited()


def test_multiple_calendars_trigger_a_specific_question(monkeypatch):
    entity = _make_entity(monkeypatch, (PRIVATE, FAMILY))
    result = _run(entity, "Trag morgen um 10 Uhr für eine Stunde Zahnarzt ein")
    assert "mehrere passende Kalender" in result.response.speech
    assert "1. Privat" in result.response.speech
    assert "2. Familie" in result.response.speech
    preview = _run(entity, "Familie").response.speech
    assert "Kalender „Familie“" in preview


def test_preview_can_be_corrected_before_confirmation(monkeypatch):
    entity = _make_entity(monkeypatch)
    _run(entity, "Trag morgen um 10 Uhr für eine Stunde Zahnarzt ein")
    revised = _run(entity, "Doch um 11 Uhr").response.speech
    assert "11:00 bis 12:00 Uhr" in revised
    entity.hass.services.async_call.assert_not_awaited()


def test_invalid_end_is_not_written_and_the_draft_can_be_corrected(monkeypatch):
    entity = _make_entity(monkeypatch)
    _run(entity, "Trag morgen von 20 Uhr bis 19 Uhr den Termin Test ein")

    refused = _run(entity, "Ja")
    assert "Endzeit muss nach der Startzeit liegen" in refused.response.speech
    entity.hass.services.async_call.assert_not_awaited()

    revised = _run(entity, "Doch bis 21 Uhr")
    assert "20:00 bis 21:00 Uhr" in revised.response.speech


def test_no_writable_calendar_is_reported_without_service_call(monkeypatch):
    read_only = EntitySnapshot("calendar.feiertage", "Feiertage", "calendar", "off")
    entity = _make_entity(monkeypatch, (read_only,))
    result = _run(entity, "Trag morgen um 10 Uhr für eine Stunde Zahnarzt ein")
    assert "keinen für HomeIntent freigegebenen, beschreibbaren Kalender" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_calendar_agenda_query_uses_get_events_response(monkeypatch):
    entity = _make_entity(monkeypatch)
    entity.hass.services.async_call.return_value = {
        "calendar.privat": {
            "events": [{
                "summary": "Zahnarzt",
                "start": "2026-08-23T10:00:00+02:00",
                "end": "2026-08-23T11:00:00+02:00",
            }]
        }
    }

    result = _run(entity, "Was steht morgen in meinem Kalender?")

    assert "Zahnarzt" in result.response.speech
    entity.hass.services.async_call.assert_awaited_once()
    # Reading the calendar is a question, not an action (F22).
    assert result.response.response_type == "query_answer"


def test_calendar_delete_is_refused_when_calendar_lacks_capability(monkeypatch):
    entity = _make_entity(monkeypatch)

    result = _run(entity, "Lösche den Termin Zahnarzt")

    assert "unterstützt das Löschen" in result.response.speech
    entity.hass.services.async_call.assert_not_awaited()


def test_supported_calendar_event_delete_requires_confirmation(monkeypatch):
    entity = _make_entity(monkeypatch, (MUTABLE,))
    event = CalendarEventSummary(
        "calendar.lokal",
        "Zahnarzt",
        "2026-08-23T10:00:00+02:00",
        "2026-08-23T11:00:00+02:00",
        uid="event-1",
    )
    lookup = AsyncMock(return_value=(event,))
    delete = AsyncMock()
    monkeypatch.setattr(management_dialogs, "async_get_mutable_calendar_events", lookup)
    monkeypatch.setattr(management_dialogs, "async_delete_calendar_event", delete)

    preview = _run(entity, "Lösche den Termin Zahnarzt morgen")
    result = _run(entity, "Ja")

    assert "wirklich löschen" in preview.response.speech
    assert result.response.speech == "Der Termin wurde gelöscht."
    delete.assert_awaited_once_with(entity.hass, event)
