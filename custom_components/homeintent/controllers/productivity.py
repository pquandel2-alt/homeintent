"""Lists, timers and calendar entries (7.7, B4: from ``conversation.py``).

Own contract: productivity meaning is independent of device control; the
turns write only ``todo``/``timer``/``calendar`` services, never devices.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..calendar_event import (
    build_calendar_event_service_call,
    calendar_event_question,
    CalendarEventDraft,
    render_calendar_event_preview,
    update_calendar_event_draft,
)
from ..entities import EntitySnapshot, normalize_for_compare
from ..execution_context import call_context, user_facing_error
from ..native_timer import (
    describe_timers,
    join_timer_labels,
    match_timer_name,
    NativeTimerInfo,
)
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.context import (
    ConversationContext,
    ConversationContextStore,
    PendingCalendarEvent,
    PendingProductivityCommand,
)
from ..nlu.entity_clarification import (
    CandidateReplyKind,
    render_candidate_question,
    resolve_candidate_reply,
)
from ..nlu.german_morphology import counted_passive
from ..productivity import (
    format_duration,
    parse_productivity_request,
    ProductivityRequest,
    select_productivity_candidate,
    timer_choice_ordinal,
    timer_name_reply,
    TimerOperation,
    TimerRequest,
    TodoOperation,
    TodoRequest,
)
from ..service_call import ServiceCallPlan

_LOGGER = logging.getLogger(__name__)


class ProductivityRuntime(Protocol):
    """The one runtime service this controller uses."""

    native_timer: Any | None


def _helper_timer_seconds_left(entity: EntitySnapshot) -> int | None:
    """Remaining seconds of a ``timer.*`` helper.

    Home Assistant refreshes ``remaining`` only when a timer is paused, so a
    running timer is measured against its ``finishes_at`` timestamp.
    """
    if entity.state == "active":
        finishes_at = entity.attributes.get("finishes_at")
        if isinstance(finishes_at, str):
            try:
                end = datetime.fromisoformat(finishes_at)
            except ValueError:
                end = None
            if end is not None and end.tzinfo is not None:
                return max(0, round((end - datetime.now(end.tzinfo)).total_seconds()))
    for key in ("remaining", "duration"):
        value = entity.attributes.get(key)
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})\s*", value)
            if match:
                hours, minutes, seconds = (int(part) for part in match.groups())
                return hours * 3600 + minutes * 60 + seconds
    return None


class ProductivityController:
    """Handles list, timer and calendar-entry turns."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        context_store: ConversationContextStore,
        runtime: ProductivityRuntime,
        record_execution: Callable[[Any, ServiceCallPlan], None],
    ) -> None:
        self._hass = hass
        self._context_store = context_store
        self._runtime = runtime
        self._record_execution = record_execution
        # Last todo list per conversation, for follow-ups without a list name.
        self._last_todo_lists: dict[str, str] = {}

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    def _store_productivity(
        self,
        conversation_id: str,
        request: ProductivityRequest,
        *,
        awaiting_confirmation: bool = False,
        awaiting_timer_name: bool = False,
        timer_choices: tuple[NativeTimerInfo, ...] = (),
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_productivity_command=PendingProductivityCommand(
                    request=request,
                    awaiting_confirmation=awaiting_confirmation,
                    awaiting_timer_name=awaiting_timer_name,
                    timer_choices=timer_choices,
                ),
            ),
        )

    async def _async_handle_native_timer_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: TimerRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Name, select and confirm native Assist timers before acting."""
        native_timer = self._runtime.native_timer
        assert native_timer is not None
        conversation_id = user_input.conversation_id

        def done(speech: str, *, query: bool = False) -> conversation.ConversationResult:
            response.async_set_speech(speech)
            if query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        try:
            if request.operation is TimerOperation.START:
                # Fail before asking for a name when nothing could be heard.
                await native_timer.async_ensure_audible(user_input)
                if not request.name:
                    self._store_productivity(
                        conversation_id, request, awaiting_timer_name=True
                    )
                    return done("Wie soll der Timer heißen?")
                timers = await native_timer.async_list_timers(user_input)
                if match_timer_name(request.name, timers):
                    self._store_productivity(
                        conversation_id, request, awaiting_timer_name=True
                    )
                    return done(
                        f"Es läuft schon ein Timer {request.name}. "
                        "Wie soll der neue Timer heißen?"
                    )
                return await self._async_execute_productivity(
                    user_input, response, request, entities
                )
            timers = await native_timer.async_list_timers(user_input)
        except Exception as err:  # noqa: BLE001 - HA intent errors are heterogeneous
            _LOGGER.error("Timer command failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ausführen: {user_facing_error(err)}",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        note = native_timer.consume_restart_note() if request.operation in {
            TimerOperation.LIST, TimerOperation.STATUS,
        } else None
        prefix = f"{note} " if note else ""
        if not timers:
            return done(f"{prefix}Es läuft kein Timer.", query=True)
        if request.operation is TimerOperation.LIST:
            return done(prefix + describe_timers(timers), query=True)
        if request.operation is TimerOperation.CANCEL_ALL:
            self._store_productivity(conversation_id, request, awaiting_confirmation=True)
            if len(timers) == 1:
                return done(f"Soll ich wirklich den Timer {timers[0].label} löschen?")
            return done(f"Soll ich wirklich alle {len(timers)} Timer löschen?")

        candidates = timers
        if request.name:
            candidates = match_timer_name(request.name, timers)
            if not candidates:
                return done(
                    f"Ich finde keinen Timer {request.name}. "
                    + describe_timers(timers)
                )
        if len(candidates) == 1:
            return await self._async_execute_native_timer(
                user_input, response, request, candidates[0], entities
            )
        if request.operation is TimerOperation.STATUS and not request.name:
            return done(describe_timers(timers), query=True)
        self._store_productivity(conversation_id, request, timer_choices=candidates)
        return done(f"Welchen Timer meinst du: {join_timer_labels(candidates)}?")

    async def _async_execute_native_timer(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: TimerRequest,
        target: NativeTimerInfo,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        self._context_store.clear(user_input.conversation_id)
        if request.operation is TimerOperation.STATUS:
            response.async_set_speech(describe_timers((target,)).split(". ", 1)[-1])
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self._async_execute_productivity(
            user_input, response, request, entities, timer_target=target
        )

    async def _async_handle_pending_timer_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingProductivityCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        request = pending.request
        assert isinstance(request, TimerRequest)
        conversation_id = user_input.conversation_id

        def ask(speech: str) -> conversation.ConversationResult:
            response.async_set_speech(speech)
            return conversation.ConversationResult(
                response=response, conversation_id=conversation_id
            )

        if classify_confirmation_reply(user_input.text) is ConfirmationReply.NO or re.fullmatch(
            r"\s*(?:abbrechen|abbruch|vergiss\s+es|stopp?)[.!]?\s*",
            user_input.text,
            re.IGNORECASE,
        ):
            self._context_store.clear(conversation_id)
            return ask("Abgebrochen. Ich führe nichts aus.")

        # A new timer or list command is not an answer to the open question
        # ("Pausiere den Küchentimer" must never become a timer name).
        new_request = parse_productivity_request(user_input.text, entities)
        if new_request is not None:
            self._context_store.clear(conversation_id)
            return await self.async_handle_request(
                user_input, response, new_request, entities
            )

        if pending.awaiting_timer_name:
            name = timer_name_reply(user_input.text)
            if name is None:
                return ask("Bitte nenne einen kurzen Namen für den Timer, zum Beispiel Nudeln.")
            if name:
                native_timer = self._runtime.native_timer
                assert native_timer is not None
                timers = await native_timer.async_list_timers(user_input)
                if match_timer_name(name, timers):
                    return ask(
                        f"Es läuft schon ein Timer {name}. Wie soll der neue Timer heißen?"
                    )
            self._context_store.clear(conversation_id)
            return await self._async_execute_productivity(
                user_input, response, replace(request, name=name or None), entities
            )

        choices = tuple(
            item for item in pending.timer_choices if isinstance(item, NativeTimerInfo)
        )
        selected: NativeTimerInfo | None = None
        position = timer_choice_ordinal(user_input.text)
        if position is not None and choices:
            index = len(choices) - 1 if position == -1 else position - 1
            if 0 <= index < len(choices):
                selected = choices[index]
        if selected is None:
            matched = match_timer_name(user_input.text.strip(" .!?"), choices)
            if len(matched) == 1:
                selected = matched[0]
        if selected is None:
            return ask(f"Welchen Timer meinst du: {join_timer_labels(choices)}?")
        return await self._async_execute_native_timer(
            user_input, response, request, selected, entities
        )

    async def async_handle_pending(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingProductivityCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        if pending.awaiting_timer_name or pending.timer_choices:
            return await self._async_handle_pending_timer_reply(
                user_input, response, pending, entities
            )
        if pending.awaiting_confirmation:
            reply = classify_confirmation_reply(user_input.text)
            if reply is ConfirmationReply.UNCLEAR:
                response.async_set_speech("Bitte antworte mit Ja oder Nein.")
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            self._context_store.clear(user_input.conversation_id)
            if reply is ConfirmationReply.NO:
                response.async_set_speech(
                    "Abgebrochen. Die Timer laufen weiter."
                    if isinstance(pending.request, TimerRequest)
                    else "Abgebrochen. Die Liste wurde nicht verändert."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            return await self._async_execute_productivity(
                user_input, response, pending.request, entities
            )

        candidate_reply = resolve_candidate_reply(
            user_input.text, pending.request.candidates, entities
        )
        if candidate_reply.kind is CandidateReplyKind.CANCELLED:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Ich führe nichts aus.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        selected = select_productivity_candidate(pending.request, user_input.text)
        if selected is None:
            response.async_set_speech(
                render_candidate_question(pending.request.candidates)
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if not any(entity.entity_id == selected.entity_id for entity in entities):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(
                "Das ausgewählte Ziel ist nicht mehr verfügbar. "
                "Ich führe nichts aus."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        return await self.async_handle_request(
            user_input, response, selected, entities
        )

    async def async_handle_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: ProductivityRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        last_list = self._last_todo_lists.get(user_input.conversation_id)
        if (
            isinstance(request, TodoRequest)
            and request.entity_id is None
            and last_list is not None
            and any(item.entity_id == last_list for item in request.candidates)
        ):
            # "Markiere Milch und Brot als erledigt" right after using the
            # Einkaufsliste continues on that list (F27).
            request = replace(request, entity_id=last_list, candidates=())
        if isinstance(request, TodoRequest) and request.entity_id is not None:
            self._last_todo_lists[user_input.conversation_id] = request.entity_id
            while len(self._last_todo_lists) > 64:
                self._last_todo_lists.pop(next(iter(self._last_todo_lists)))
        if request.entity_id is None:
            if request.candidates:
                self._store_productivity(user_input.conversation_id, request)
                response.async_set_speech(
                    render_candidate_question(request.candidates)
                )
            elif isinstance(request, TimerRequest) and self._runtime.native_timer is not None:
                self._context_store.clear(user_input.conversation_id)
                return await self._async_handle_native_timer_request(
                    user_input, response, request, entities
                )
            else:
                noun = "keine für HomeIntent freigegebene Liste" if isinstance(request, TodoRequest) else "keinen für HomeIntent freigegebenen Timer"
                response.async_set_speech(f"Ich finde {noun}.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if (
            isinstance(request, TodoRequest)
            and request.operation is TodoOperation.CLEAR_COMPLETED
        ):
            self._store_productivity(
                user_input.conversation_id, request, awaiting_confirmation=True
            )
            response.async_set_speech(
                "Soll ich wirklich alle erledigten Einträge aus der Liste löschen?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        self._context_store.clear(user_input.conversation_id)
        return await self._async_execute_productivity(
            user_input, response, request, entities
        )

    async def _async_execute_productivity(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: ProductivityRequest,
        entities: list[EntitySnapshot],
        *,
        timer_target: NativeTimerInfo | None = None,
    ) -> conversation.ConversationResult:
        try:
            if isinstance(request, TodoRequest):
                speech = await self.async_execute_todo(request)
            else:
                speech = await self._async_execute_timer(
                    request, entities, user_input, timer_target=timer_target
                )
        except Exception as err:  # noqa: BLE001 - HA service errors are heterogeneous
            _LOGGER.error("Productivity command failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                f"Fehler beim Ausführen: {user_facing_error(err)}",
            )
        else:
            response.async_set_speech(speech)
            if isinstance(request, TodoRequest) and request.operation is not TodoOperation.LIST:
                if request.entity_id is None:
                    return conversation.ConversationResult(
                        response=response,
                        conversation_id=user_input.conversation_id,
                    )
                todo_service = {
                    TodoOperation.ADD: "add_item",
                    TodoOperation.COMPLETE: "update_item",
                    TodoOperation.REMOVE: "remove_item",
                    TodoOperation.CLEAR_COMPLETED: "remove_completed_items",
                    TodoOperation.MOVE: "move_items",
                }[request.operation]
                self._record_execution(
                    user_input,
                    ServiceCallPlan("todo", todo_service, request.entity_id, {}),
                )
            elif (
                isinstance(request, TimerRequest)
                and request.entity_id is not None
                and request.operation is not TimerOperation.STATUS
            ):
                timer_service = {
                    TimerOperation.START: "start",
                    TimerOperation.CHANGE: "change",
                    TimerOperation.PAUSE: "pause",
                    TimerOperation.RESUME: "start",
                    TimerOperation.CANCEL: "cancel",
                    TimerOperation.FINISH: "finish",
                }[request.operation]
                self._record_execution(
                    user_input,
                    ServiceCallPlan("timer", timer_service, request.entity_id, {}),
                )
            if (
                isinstance(request, TimerRequest)
                and request.operation is TimerOperation.STATUS
            ) or (
                isinstance(request, TodoRequest)
                and request.operation is TodoOperation.LIST
            ):
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def _async_get_todo_items(self, entity_id: str) -> list[dict]:
        result = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"status": ["needs_action", "completed"]},
            target={"entity_id": entity_id},
            blocking=True,
            return_response=True,
            context=call_context(),
        )
        container = result.get(entity_id, result) if isinstance(result, dict) else {}
        items = container.get("items", []) if isinstance(container, dict) else []
        return [item for item in items if isinstance(item, dict)]

    async def async_execute_todo(self, request: TodoRequest) -> str:
        assert request.entity_id is not None
        if request.operation is TodoOperation.LIST:
            items = await self._async_get_todo_items(request.entity_id)
            open_items = [
                str(item.get("summary") or item.get("item") or "").strip()
                for item in items
                if item.get("status", "needs_action") == "needs_action"
            ]
            open_items = [item for item in open_items if item]
            return (
                "Auf der Liste steht nichts Offenes."
                if not open_items
                else "Auf der Liste stehen: " + ", ".join(open_items) + "."
            )

        if request.operation is TodoOperation.CLEAR_COMPLETED:
            items = await self._async_get_todo_items(request.entity_id)
            completed = [
                item.get("uid") or item.get("summary") or item.get("item")
                for item in items
                if item.get("status") == "completed"
            ]
            completed = [item for item in completed if item]
            if completed:
                await self.hass.services.async_call(
                    "todo", "remove_completed_items", {},
                    target={"entity_id": request.entity_id}, blocking=True,
                    context=call_context(),
                )
            return (
                "Es gab keine erledigten Einträge."
                if not completed
                else counted_passive(
                    len(completed), "erledigter Eintrag", "erledigte Einträge", "gelöscht"
                )
            )

        def matching_items(
            available: list[dict], requested: tuple[str, ...]
        ) -> list[dict]:
            """Resolve spoken summaries to stable todo UIDs without guessing."""
            selected: list[dict] = []
            for spoken in requested:
                key = normalize_for_compare(spoken)
                matches = []
                for candidate in available:
                    summary = str(
                        candidate.get("summary") or candidate.get("item") or ""
                    ).strip()
                    # Home Assistant has no portable priority field. HomeIntent
                    # stores it as a visible prefix, but users need not repeat it.
                    plain_summary = re.sub(
                        r"^\[(?:hoch|mittel|niedrig)\]\s*", "", summary,
                        flags=re.IGNORECASE,
                    )
                    if normalize_for_compare(plain_summary) == key:
                        matches.append(candidate)
                if not matches:
                    raise ValueError(f"Eintrag „{spoken}“ wurde nicht gefunden")
                if len(matches) > 1:
                    raise ValueError(
                        f"Eintrag „{spoken}“ ist mehrfach vorhanden. "
                        "Bitte mache ihn zuerst eindeutig"
                    )
                selected.append(matches[0])
            return selected

        if request.operation in {TodoOperation.COMPLETE, TodoOperation.REMOVE}:
            available = await self._async_get_todo_items(request.entity_id)
            selected = matching_items(available, request.items)
            service = (
                "update_item"
                if request.operation is TodoOperation.COMPLETE
                else "remove_item"
            )
            for item in selected:
                uid = item.get("uid")
                if not uid:
                    raise ValueError("Die Liste liefert keine stabile Eintrags-ID")
                data: dict[str, object] = {"item": uid}
                if request.operation is TodoOperation.COMPLETE:
                    data["status"] = "completed"
                await self.hass.services.async_call(
                    "todo", service, data,
                    target={"entity_id": request.entity_id}, blocking=True,
                    context=call_context(),
                )
            count = len(selected)
            if request.operation is TodoOperation.COMPLETE:
                return counted_passive(count, "Eintrag", "Einträge", "als erledigt markiert")
            return counted_passive(count, "Eintrag", "Einträge", "aus der Liste entfernt")

        if request.operation is TodoOperation.MOVE:
            if request.destination_entity_id is None:
                raise ValueError("Die Zielliste fehlt")
            available = await self._async_get_todo_items(request.entity_id)
            selected = matching_items(available, request.items)
            # Resolve every source item before the first write. A partial move
            # can therefore only result from an external HA service failure.
            added: list[dict] = []
            try:
                for item in selected:
                    summary = str(item.get("summary") or item.get("item") or "")
                    data: dict[str, object] = {"item": summary}
                    due = item.get("due") or item.get("due_date") or item.get("due_datetime")
                    if due:
                        data["due_datetime" if "T" in str(due) else "due_date"] = due
                    if item.get("description"):
                        data["description"] = item["description"]
                    await self.hass.services.async_call(
                        "todo", "add_item", data,
                        target={"entity_id": request.destination_entity_id}, blocking=True,
                        context=call_context(),
                    )
                    added.append(item)
                for item in selected:
                    uid = item.get("uid")
                    if not uid:
                        raise ValueError("Die Liste liefert keine stabile Eintrags-ID")
                    await self.hass.services.async_call(
                        "todo", "remove_item", {"item": uid},
                        target={"entity_id": request.entity_id}, blocking=True,
                        context=call_context(),
                    )
            except Exception:
                _LOGGER.warning(
                    "Todo move failed after %d destination writes; source items "
                    "were retained where possible", len(added)
                )
                raise
            count = len(selected)
            return f"{count} Eintrag" + (" wurde" if count == 1 else "e wurden") + " verschoben."

        assert request.operation is TodoOperation.ADD
        for item in request.items:
            stored_item = (
                f"[{request.priority.capitalize()}] {item}"
                if request.priority else item
            )
            data: dict[str, object] = {"item": stored_item}
            if request.due_date:
                data["due_date"] = request.due_date
            if request.description:
                data["description"] = request.description
            await self.hass.services.async_call(
                "todo", "add_item", data,
                target={"entity_id": request.entity_id}, blocking=True,
                context=call_context(),
            )
        count = len(request.items)
        return (
            f"{request.items[0]} wurde zur Liste hinzugefügt."
            if count == 1
            else f"{count} Einträge wurden zur Liste hinzugefügt: "
            + ", ".join(request.items) + "."
        )

    async def _async_execute_timer(
        self,
        request: TimerRequest,
        entities: list[EntitySnapshot],
        user_input: conversation.ConversationInput,
        *,
        timer_target: NativeTimerInfo | None = None,
    ) -> str:
        if request.entity_id is None:
            native_timer = self._runtime.native_timer
            if native_timer is None:
                raise ValueError("Home Assistants native Timerverwaltung ist nicht verfügbar.")
            if request.operation is TimerOperation.CANCEL_ALL:
                canceled = await native_timer.async_cancel_all(user_input)
                if canceled == 1:
                    return "Der Timer wurde gelöscht."
                return f"Alle {canceled} Timer wurden gelöscht."
            return await native_timer.async_execute(
                request, user_input, target=timer_target
            )
        assert request.entity_id is not None
        entity = next((item for item in entities if item.entity_id == request.entity_id), None)
        if request.operation is TimerOperation.STATUS:
            if entity is None:
                return "Der Timer ist nicht mehr verfügbar."
            if entity.state == "idle":
                return "Der Timer ist nicht aktiv."
            seconds_left = _helper_timer_seconds_left(entity)
            state = "pausiert" if entity.state == "paused" else "aktiv"
            if seconds_left is None:
                return f"Der Timer ist {state}."
            return f"Der Timer ist {state}. Verbleibende Zeit: {format_duration(seconds_left)}."

        service = {
            TimerOperation.START: "start",
            TimerOperation.CHANGE: "change",
            TimerOperation.PAUSE: "pause",
            TimerOperation.RESUME: "start",
            TimerOperation.CANCEL: "cancel",
            TimerOperation.FINISH: "finish",
        }[request.operation]
        data: dict[str, str | int] = {}
        if request.operation is TimerOperation.START:
            assert request.duration_seconds is not None
            data["duration"] = self._timer_duration_value(request.duration_seconds)
        elif request.operation is TimerOperation.CHANGE:
            assert request.change_seconds is not None
            # timer.change explicitly accepts signed seconds; using the
            # integer form avoids ambiguity around a negative HH:MM string.
            data["duration"] = request.change_seconds
        await self.hass.services.async_call(
            "timer", service, data,
            target={"entity_id": request.entity_id}, blocking=True,
            context=call_context(),
        )
        if request.operation is TimerOperation.START:
            return f"Timer für {format_duration(request.duration_seconds or 0)} gestartet."
        if request.operation is TimerOperation.CHANGE:
            verb = "verlängert" if (request.change_seconds or 0) > 0 else "verkürzt"
            return f"Timer um {format_duration(request.change_seconds or 0)} {verb}."
        return {
            TimerOperation.PAUSE: "Timer pausiert.",
            TimerOperation.RESUME: "Timer fortgesetzt.",
            TimerOperation.CANCEL: "Timer abgebrochen.",
            TimerOperation.FINISH: "Timer beendet.",
        }[request.operation]

    @staticmethod
    def _timer_duration_value(seconds: int) -> str:
        sign = "-" if seconds < 0 else ""
        hours, remainder = divmod(abs(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"

    def _store_calendar_event(
        self,
        conversation_id: str,
        draft: CalendarEventDraft,
        awaiting_confirmation: bool,
    ) -> None:
        self._context_store.set(
            conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_calendar_event=PendingCalendarEvent(
                    draft=draft, awaiting_confirmation=awaiting_confirmation
                ),
            ),
        )

    async def async_handle_calendar_event_turn(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingCalendarEvent,
        calendars: tuple[EntitySnapshot, ...],
    ) -> conversation.ConversationResult:
        """Complete, revise, confirm, and finally create one calendar event."""
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.NO:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech("Abgebrochen. Der Termin wurde nicht eingetragen.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if not pending.awaiting_confirmation and reply is ConfirmationReply.YES:
            response.async_set_speech(
                calendar_event_question(pending.draft, calendars)
                or "Bitte ergänze die noch fehlende Terminangabe."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        if pending.awaiting_confirmation and reply is ConfirmationReply.YES:
            try:
                call = build_calendar_event_service_call(
                    pending.draft, calendars, dt_util.now()
                )
            except ValueError as err:
                response.async_set_speech(
                    f"Der Termin kann so nicht eingetragen werden: {err}. Bitte korrigiere die Angabe."
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

            self._context_store.clear(user_input.conversation_id)
            try:
                await self.hass.services.async_call(
                    "calendar",
                    "create_event",
                    call.data,
                    target={"entity_id": call.entity_id},
                    blocking=True,
                    context=call_context(),
                )
            except Exception as err:  # noqa: BLE001 - HA calendar integrations raise heterogeneous errors
                _LOGGER.error("Calendar event creation failed: %s", err)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Eintragen des Termins: {user_facing_error(err)}",
                )
            else:
                self._record_execution(
                    user_input,
                    ServiceCallPlan("calendar", "create_event", call.entity_id, {}),
                )
                response.async_set_speech("Der Termin wurde eingetragen.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        update = update_calendar_event_draft(
            user_input.text, pending.draft, calendars, dt_util.now()
        )
        if update.error_text is not None:
            response.async_set_speech(update.error_text)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if pending.awaiting_confirmation and not update.changed:
            response.async_set_speech(
                "Bitte antworte mit Ja oder Nein. Du kannst Datum, Uhrzeit, Dauer, Titel oder Kalender auch noch korrigieren."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        question = calendar_event_question(update.draft, calendars)
        awaiting_confirmation = question is None
        self._store_calendar_event(
            user_input.conversation_id, update.draft, awaiting_confirmation
        )
        response.async_set_speech(
            render_calendar_event_preview(update.draft, calendars)
            if awaiting_confirmation
            else question
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def handle_calendar_draft(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        draft: CalendarEventDraft,
        calendars: tuple[EntitySnapshot, ...],
    ) -> conversation.ConversationResult:
        """Store a calendar draft and ask for its next missing value."""
        if not calendars:
            self._context_store.clear(user_input.conversation_id)
            response.async_set_speech(
                "Ich finde keinen für HomeIntent freigegebenen, beschreibbaren Kalender."
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        question = calendar_event_question(draft, calendars)
        awaiting_confirmation = question is None
        self._store_calendar_event(
            user_input.conversation_id, draft, awaiting_confirmation
        )
        response.async_set_speech(
            render_calendar_event_preview(draft, calendars)
            if awaiting_confirmation
            else question
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
