"""Conversation orchestration for calendar and automation-edit dialogs."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import time
from typing import Any, Mapping

from homeassistant.components import conversation
from homeassistant.helpers import intent

from .automation_action_edit import reordered_actions, select_candidate_reply
from .automation_structure_edit import (
    AutomationEditOperation,
    AutomationEditSection,
    AutomationStructureEditRequest,
)
from .automation_executor import AutomationExecutor
from .calendar_management import (
    CalendarManagementKind,
    CalendarManagementRequest,
    flatten_calendar_response,
    render_calendar_events,
)
from .calendar_runtime import (
    async_delete_calendar_event,
    async_get_mutable_calendar_events,
    async_reschedule_calendar_event,
    async_update_calendar_event_details,
)
from .entities import EntitySnapshot, normalize_for_compare
from .nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from .nlu.context import (
    ConversationContext,
    PendingAutomationActionEdit,
    PendingAutomationStructureEdit,
    PendingCalendarMutation,
)
from .nlu.ha_automation_generator import (
    generate_ha_action_configs,
    generate_ha_condition_configs,
    generate_ha_trigger_configs,
)
from .nlu.response_generator import _automation_label

_LOGGER = logging.getLogger(__name__)


def store_automation_action_edit(
    agent: Any, conversation_id: str, pending: PendingAutomationActionEdit
) -> None:
    agent._context_store.set(
        conversation_id,
        ConversationContext(
            last_command=None,
            last_entities=(),
            last_area=None,
            pending_clarification=None,
            pending_automation_action_edit=pending,
        ),
    )


def store_automation_structure_edit(
    agent: Any, conversation_id: str, pending: PendingAutomationStructureEdit
) -> None:
    agent._context_store.set(
        conversation_id,
        ConversationContext(
            last_command=None,
            last_entities=(),
            last_area=None,
            pending_clarification=None,
            pending_automation_structure_edit=pending,
        ),
    )


def _structure_label(section: AutomationEditSection) -> str:
    return "Auslöser" if section is AutomationEditSection.TRIGGERS else "Bedingungen"


async def async_prepare_automation_structure_edit(
    agent: Any,
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    pending: PendingAutomationStructureEdit,
    entities: list[EntitySnapshot],
    edit_text: str | None,
) -> conversation.ConversationResult:
    request: AutomationStructureEditRequest = pending.request
    automation = pending.automation
    assert automation is not None
    section = request.section
    existing = list(
        automation.triggers
        if section is AutomationEditSection.TRIGGERS
        else automation.conditions
    )
    if request.operation is AutomationEditOperation.CLEAR:
        rendered: list[Mapping[str, Any]] = []
        spoken_edit = "alle Bedingungen entfernen"
    elif request.operation is AutomationEditOperation.REMOVE:
        remove_index = request.index
        if remove_index is None and request.payload is not None:
            matching = [
                index for index, value in enumerate(existing)
                if value.get("condition") == request.payload
            ]
            if len(matching) != 1:
                response.async_set_speech(
                    "Ich konnte nicht genau eine passende Bedingung bestimmen. "
                    "Bitte nenne ihre Nummer."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            remove_index = matching[0]
        assert remove_index is not None
        if remove_index >= len(existing):
            response.async_set_speech(
                f"Diese Automation hat nur {len(existing)} Bedingungen."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        rendered = [value for index, value in enumerate(existing) if index != remove_index]
        spoken_edit = f"Bedingung {remove_index + 1} entfernen"
    else:
        if not edit_text:
            store_automation_structure_edit(agent, user_input.conversation_id, pending)
            response.async_set_speech(
                "Wie soll der neue Auslöser lauten?"
                if section is AutomationEditSection.TRIGGERS
                else "Welche Bedingung soll gelten?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if section is AutomationEditSection.TRIGGERS:
            parsed = agent._engine.parse_automation_trigger(
                edit_text, entities, agent._world_model
            )
            new_values, error = (
                generate_ha_trigger_configs((parsed,), entities)
                if parsed is not None else (None, None)
            )
        else:
            parsed = agent._engine.parse_automation_condition(
                edit_text, entities, agent._world_model
            )
            new_values, error = (
                generate_ha_condition_configs((parsed,), entities)
                if parsed is not None else (None, None)
            )
        if parsed is None or error is not None or new_values is None:
            response.async_set_speech(
                f"Den neuen {_structure_label(section)} habe ich nicht eindeutig verstanden. "
                "Bitte formuliere ihn als vollständigen Satz."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        rendered = [
            dict(value) for value in (
            [*existing, *new_values]
            if request.operation is AutomationEditOperation.ADD
            else new_values
            )
        ]
        spoken_edit = edit_text.strip()
    ready = PendingAutomationStructureEdit(
        request=request,
        candidates=pending.candidates,
        automation=automation,
        rendered=tuple(rendered),
        edit_text=spoken_edit,
        ready=True,
    )
    store_automation_structure_edit(agent, user_input.conversation_id, ready)
    response.async_set_speech(
        f"Vorschau für „{_automation_label(automation)}“: "
        f"{_structure_label(section)} vorher {len(existing)}, danach {len(rendered)}. "
        f"Änderung: {spoken_edit}. Soll ich das übernehmen?"
    )
    return conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )


async def async_handle_automation_structure_edit_turn(
    agent: Any,
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    pending: PendingAutomationStructureEdit,
    entities: list[EntitySnapshot],
) -> conversation.ConversationResult | None:
    if pending.ready:
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
        elif reply is ConfirmationReply.NO:
            agent._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Die Automation wurde nicht verändert.")
        else:
            assert pending.automation is not None
            request: AutomationStructureEditRequest = pending.request
            if agent._automation_executor is None:
                agent._automation_executor = AutomationExecutor(agent.hass)
            try:
                await agent._automation_executor.async_replace_automation_section(
                    pending.automation.automation_id,
                    "triggers" if request.section is AutomationEditSection.TRIGGERS else "conditions",
                    [dict(value) for value in pending.rendered],
                    (pending.automation.source_text or pending.automation.alias)
                    + " | Änderung: " + (pending.edit_text or ""),
                )
            except Exception as err:  # noqa: BLE001
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ändern der Automation: {err}",
                )
            else:
                response.async_set_speech(
                    f"Die {_structure_label(request.section)} wurden geändert."
                )
            agent._context_store.clear(user_input.conversation_id)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    automation = pending.automation
    if automation is None:
        automation = select_candidate_reply(user_input.text, pending.candidates)
        if automation is None and len(user_input.text.split()) > 3:
            # A complete new request, not a choice between the candidates:
            # drop the selection question instead of repeating it (F9).
            agent._context_store.clear(user_input.conversation_id)
            return None
        if automation is None:
            response.async_set_speech(
                "Das ist nicht eindeutig. Bitte nenne genau eine Automation oder ihre Nummer: "
                + ", ".join(_automation_label(item) for item in pending.candidates) + "."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        pending = replace(pending, automation=automation)
        payload = pending.request.payload
        if payload or pending.request.operation in {
            AutomationEditOperation.CLEAR, AutomationEditOperation.REMOVE
        }:
            return await async_prepare_automation_structure_edit(
                agent, user_input, response, pending, entities, payload
            )
        store_automation_structure_edit(agent, user_input.conversation_id, pending)
        response.async_set_speech(
            "Wie soll der neue Auslöser lauten?"
            if pending.request.section is AutomationEditSection.TRIGGERS
            else "Welche Bedingung soll gelten?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    return await async_prepare_automation_structure_edit(
        agent, user_input, response, pending, entities, user_input.text
    )


async def async_handle_automation_action_edit_turn(
    agent: Any,
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    pending: PendingAutomationActionEdit,
    entities: list[EntitySnapshot],
) -> conversation.ConversationResult:
    """Resolve automation, replacement action and final consent in stages."""
    reply = classify_confirmation_reply(user_input.text)
    if pending.rendered_actions:
        if reply is ConfirmationReply.NO:
            agent._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Die Automation wurde nicht verändert.")
        elif reply is not ConfirmationReply.YES:
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
        else:
            assert pending.automation is not None and pending.action_text is not None
            if agent._automation_executor is None:
                agent._automation_executor = AutomationExecutor(agent.hass)
            try:
                await agent._automation_executor.async_replace_automation_actions(
                    pending.automation.automation_id,
                    [dict(value) for value in pending.rendered_actions],
                    (
                        (pending.automation.source_text or pending.automation.alias)
                        + " | Neue Aktion: "
                        + pending.action_text
                    ),
                )
            except Exception as err:  # noqa: BLE001
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ändern der Automation: {err}",
                )
            else:
                response.async_set_speech(
                    "Die Aktion wurde geändert. Auslöser und Bedingungen blieben unverändert."
                )
            agent._context_store.clear(user_input.conversation_id)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    automation = pending.automation
    if automation is None:
        automation = select_candidate_reply(user_input.text, pending.candidates)
        if automation is None:
            response.async_set_speech(
                "Das ist nicht eindeutig. Bitte nenne genau eine dieser Automationen: "
                + ", ".join(_automation_label(item) for item in pending.candidates)
                + "."
            )
        else:
            reordered = reordered_actions(automation.actions, pending.operation)
            if pending.operation.startswith("reorder:") and reordered is None:
                response.async_set_speech(
                    "Diese Automation hat nicht genügend Aktionen für diese Reihenfolge."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            store_automation_action_edit(
                agent,
                user_input.conversation_id,
                PendingAutomationActionEdit(
                    candidates=pending.candidates,
                    automation=automation,
                    rendered_actions=reordered or (),
                    action_text=user_input.text if reordered else None,
                    operation=pending.operation,
                ),
            )
            response.async_set_speech(
                (
                    "Soll ich die Reihenfolge der Aktionen wie gewünscht ändern?"
                    if reordered
                    else "Welche Aktion soll zusätzlich danach ausgeführt werden?"
                    if pending.operation == "add"
                    else "Was soll stattdessen passieren? Auslöser und Bedingungen bleiben unverändert."
                )
            )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    actions = agent._engine.parse_automation_actions(
        user_input.text, entities, agent._world_model
    )
    if not actions:
        response.async_set_speech(
            "Die neue Aktion habe ich nicht eindeutig verstanden. Bitte nenne eine vollständige Geräteaktion."
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    rendered, error = generate_ha_action_configs(actions, entities)
    if error is not None or rendered is None:
        response.async_set_speech("Diese Aktion kann ich nicht sicher in Home Assistant abbilden.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    rendered_actions = list(rendered)
    if pending.operation == "add":
        existing_user_actions = [
            dict(action) for action in automation.actions
            if not (
                isinstance(action, dict)
                and (
                    "delay" in action
                    or action.get("action") in {
                        "homeintent.delete_automation",
                        "homeintent.record_automation_run",
                    }
                )
            )
        ]
        rendered_actions = [*existing_user_actions, *rendered_actions]
    store_automation_action_edit(
        agent,
        user_input.conversation_id,
        PendingAutomationActionEdit(
            candidates=pending.candidates,
            automation=automation,
            rendered_actions=tuple(rendered_actions),
            action_text=user_input.text,
            operation=pending.operation,
        ),
    )
    response.async_set_speech(
        (
            f"Vorschau für „{_automation_label(automation)}“: Aktionen vorher "
            f"{len(automation.actions)}, danach {len(rendered_actions)}. "
            f"Soll ich „{user_input.text.strip()}“ zusätzlich ausführen?"
            if pending.operation == "add"
            else f"Soll ich bei „{_automation_label(automation)}“ nur die Aktion durch "
            f"„{user_input.text.strip()}“ ersetzen?"
        )
    )
    return conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )


async def async_handle_calendar_management(
    agent: Any,
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    request: CalendarManagementRequest,
    calendars: tuple[EntitySnapshot, ...],
) -> conversation.ConversationResult:
    """Read events or prepare one capability-checked calendar mutation."""
    if not request.calendar_entity_ids:
        response.async_set_speech("Ich finde keinen für HomeIntent freigegebenen Kalender.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    if request.kind in {
        CalendarManagementKind.LIST,
        CalendarManagementKind.FIND,
        CalendarManagementKind.AVAILABILITY,
    }:
        try:
            result = await agent.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "start_date_time": request.start.isoformat(),
                    "end_date_time": request.end.isoformat(),
                },
                target={"entity_id": list(request.calendar_entity_ids)},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Calendar event query failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Lesen des Kalenders: {err}",
            )
        else:
            # Reading events is a question, not an action (F22).
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
            response.async_set_speech(
                render_calendar_events(
                    flatten_calendar_response(result, request.title_filter), request
                )
            )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    required = (
        "DELETE_EVENT" if request.kind is CalendarManagementKind.DELETE else "UPDATE_EVENT"
    )
    capable_ids = tuple(
        calendar.entity_id
        for calendar in calendars
        if calendar.entity_id in request.calendar_entity_ids
        and required in calendar.capabilities
    )
    if not capable_ids:
        operation = "Löschen" if required == "DELETE_EVENT" else "Verschieben"
        response.async_set_speech(
            f"Der ausgewählte Kalender unterstützt das {operation} von Terminen über Home Assistant nicht."
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if request.kind is CalendarManagementKind.RESCHEDULE and request.new_start_time is None:
        agent._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_mutation=PendingCalendarMutation(
                    kind=request.kind, request=request
                ),
            ),
        )
        response.async_set_speech("Auf welche Uhrzeit soll ich den Termin verschieben?")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    try:
        events = await async_get_mutable_calendar_events(
            agent.hass, capable_ids, request.start, request.end
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Calendar event lookup for mutation failed: %s", err)
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
            f"Fehler beim Lesen des Kalenders: {err}",
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if request.title_filter:
        wanted = normalize_for_compare(request.title_filter)
        events = tuple(
            event for event in events if wanted in normalize_for_compare(event.summary)
        )
    events = tuple(event for event in events if event.uid is not None)
    if not events:
        response.async_set_speech("Ich finde keinen eindeutig änderbaren passenden Termin.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if len(events) > 1:
        names = "; ".join(f"„{event.summary}“ ({event.start})" for event in events[:5])
        agent._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_mutation=PendingCalendarMutation(
                    kind=request.kind,
                    new_start_time=request.new_start_time,
                    candidates=events,
                    new_date=request.new_date,
                    new_title=request.new_title,
                    new_duration_minutes=request.new_duration_minutes,
                ),
            ),
        )
        response.async_set_speech(
            f"Mehrere Termine passen: {names}. Welchen davon meinst du?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    event = next(iter(events))
    agent._context_store.set(
        user_input.conversation_id,
        ConversationContext(
            last_command=None,
            last_entities=(),
            last_area=None,
            pending_clarification=None,
            pending_calendar_mutation=PendingCalendarMutation(
                kind=request.kind,
                event=event,
                new_start_time=request.new_start_time,
                new_date=request.new_date,
                new_title=request.new_title,
                new_duration_minutes=request.new_duration_minutes,
            ),
        ),
    )
    if event.recurrence_id is not None:
        response.async_set_speech(
            "Der Termin gehört zu einer Serie. Meinst du nur diesen Termin, "
            "die ganze Serie oder diesen und alle folgenden Termine?"
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if request.kind is CalendarManagementKind.DELETE:
        preview = f"Soll ich den Termin „{event.summary}“ wirklich löschen?"
    elif request.kind is CalendarManagementKind.UPDATE:
        change = (
            f"in „{request.new_title}“ umbenennen"
            if request.new_title is not None
            else f"auf {request.new_duration_minutes} Minuten Dauer ändern"
        )
        preview = f"Soll ich den Termin „{event.summary}“ wirklich {change}?"
    else:
        assert request.new_start_time is not None
        preview = (
            f"Soll ich den Termin „{event.summary}“ auf "
            f"{request.new_start_time.strftime('%H:%M')} Uhr verschieben?"
        )
    response.async_set_speech(preview)
    return conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )


async def async_handle_calendar_mutation_confirmation(
    agent: Any,
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    pending: PendingCalendarMutation,
) -> conversation.ConversationResult:
    if pending.request is not None:
        match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", user_input.text, re.I)
        if match is None:
            response.async_set_speech("Bitte nenne eine eindeutige Uhrzeit, zum Beispiel 20 Uhr.")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            response.async_set_speech("Diese Uhrzeit ist nicht gültig. Welche Uhrzeit meinst du?")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        agent._context_store.clear(user_input.conversation_id)
        return await async_handle_calendar_management(
            agent,
            user_input,
            response,
            replace(pending.request, new_start_time=time(hour, minute)),
            tuple(entity for entity in agent._world_model.entities if entity.domain == "calendar"),
        )

    if pending.event is None and pending.candidates:
        normalized = normalize_for_compare(user_input.text)
        selected = [
            event for event in pending.candidates
            if normalize_for_compare(event.summary) in normalized
        ]
        ordinal = re.search(r"\b(?:der|den)\s+(erste|zweite|dritte|vierte|fuenfte|fünfte)\b", normalized)
        if not selected and ordinal is not None:
            index = {"erste": 0, "zweite": 1, "dritte": 2, "vierte": 3, "fuenfte": 4, "fünfte": 4}[ordinal.group(1)]
            if index < len(pending.candidates):
                selected = [pending.candidates[index]]
        if len(selected) != 1:
            response.async_set_speech("Das ist noch nicht eindeutig. Bitte nenne den Titel oder zum Beispiel den ersten Termin.")
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        event = selected[0]
        agent._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_mutation=PendingCalendarMutation(
                    kind=pending.kind,
                    event=event,
                    new_start_time=pending.new_start_time,
                    new_date=pending.new_date,
                    new_title=pending.new_title,
                    new_duration_minutes=pending.new_duration_minutes,
                ),
            ),
        )
        new_start_time = pending.new_start_time
        if (
            pending.kind is CalendarManagementKind.RESCHEDULE
            and new_start_time is None
        ):
            response.async_set_speech("Die neue Uhrzeit fehlt.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        verb = (
            "löschen" if pending.kind is CalendarManagementKind.DELETE
            else f"in „{pending.new_title}“ umbenennen" if pending.new_title
            else f"auf {pending.new_duration_minutes} Minuten Dauer ändern" if pending.kind is CalendarManagementKind.UPDATE
            else (
                f"auf {new_start_time.strftime('%H:%M')} Uhr verschieben"
                if new_start_time is not None
                else "verschieben"
            )
        )
        response.async_set_speech(f"Soll ich den Termin „{event.summary}“ wirklich {verb}?")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    if (
        pending.event is not None
        and pending.event.recurrence_id is not None
        and pending.recurrence_scope is None
    ):
        normalized = normalize_for_compare(user_input.text)
        scopes = []
        if re.search(r"\b(?:nur\s+)?(?:diesen|dieser|einzelnen)\b", normalized):
            scopes.append("this")
        if re.search(r"\b(?:ganze|komplette|alle)\w*\s+serie\b", normalized):
            scopes.append("all")
        if re.search(r"\b(?:folgenden|zukuenftigen|zukünftigen)\b", normalized):
            scopes.append("future")
        if len(scopes) != 1:
            response.async_set_speech(
                "Bitte sage: nur diesen Termin, die ganze Serie oder diesen und alle folgenden."
            )
            return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)
        agent._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_mutation=PendingCalendarMutation(
                    kind=pending.kind,
                    event=pending.event,
                    new_start_time=pending.new_start_time,
                    new_date=pending.new_date,
                    new_title=pending.new_title,
                    new_duration_minutes=pending.new_duration_minutes,
                    recurrence_scope=scopes[0],
                ),
            ),
        )
        scope_text = {"this": "nur diesen Termin", "all": "die ganze Serie", "future": "diesen und alle folgenden Termine"}[scopes[0]]
        new_start_time = pending.new_start_time
        if (
            pending.kind is CalendarManagementKind.RESCHEDULE
            and new_start_time is None
        ):
            response.async_set_speech("Die neue Uhrzeit fehlt.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        verb = (
            "löschen" if pending.kind is CalendarManagementKind.DELETE
            else f"in „{pending.new_title}“ umbenennen" if pending.new_title
            else f"auf {pending.new_duration_minutes} Minuten Dauer ändern" if pending.kind is CalendarManagementKind.UPDATE
            else (
                f"auf {new_start_time.strftime('%H:%M')} Uhr verschieben"
                if new_start_time is not None
                else "verschieben"
            )
        )
        response.async_set_speech(f"Soll ich {scope_text} wirklich {verb}?")
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    reply = classify_confirmation_reply(user_input.text)
    if reply is ConfirmationReply.NO:
        agent._context_store.clear(user_input.conversation_id)
        response.async_set_speech("Abgebrochen. Der Termin wurde nicht verändert.")
    elif reply is not ConfirmationReply.YES:
        response.async_set_speech("Bitte antworte mit Ja oder Nein.")
    else:
        agent._context_store.clear(user_input.conversation_id)
        try:
            assert pending.event is not None
            if pending.kind is CalendarManagementKind.DELETE:
                if pending.recurrence_scope is None:
                    await async_delete_calendar_event(agent.hass, pending.event)
                else:
                    await async_delete_calendar_event(
                        agent.hass, pending.event, pending.recurrence_scope
                    )
                response.async_set_speech("Der Termin wurde gelöscht.")
            elif pending.kind is CalendarManagementKind.RESCHEDULE:
                assert pending.new_start_time is not None
                if pending.recurrence_scope is None:
                    if pending.new_date is None:
                        await async_reschedule_calendar_event(
                            agent.hass, pending.event, pending.new_start_time
                        )
                    else:
                        await async_reschedule_calendar_event(
                            agent.hass,
                            pending.event,
                            pending.new_start_time,
                            new_date=pending.new_date,
                        )
                else:
                    await async_reschedule_calendar_event(
                        agent.hass,
                        pending.event,
                        pending.new_start_time,
                        pending.recurrence_scope,
                        pending.new_date,
                    )
                response.async_set_speech("Der Termin wurde verschoben.")
            else:
                await async_update_calendar_event_details(
                    agent.hass,
                    pending.event,
                    new_title=pending.new_title,
                    new_duration_minutes=pending.new_duration_minutes,
                    recurrence_scope=pending.recurrence_scope,
                )
                response.async_set_speech("Der Termin wurde geändert.")
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Calendar mutation failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ändern des Termins: {err}",
            )
    return conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )
