"""Read-only answers (7.7, B4: from ``conversation.py``).

Causes ("Warum ist das Flurlicht an?"), explanations of what HomeIntent
did, today's audit, history and advanced queries. Nothing here writes.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..audit_log import AuditTrail, render_today
from ..entities import EntitySnapshot
from ..execution_trace import (
    CauseExplanation,
    ContextIndex,
    ExecutionTraceStore,
    explain_change,
)
from ..history_query import (
    async_execute_history_query,
    ComparativeHistoryQuery,
    HistoryQuery,
    StateHistoryQuery,
)
from ..nlu.automation_preview import render_automation_preview
from ..nlu.context import ConversationContext, ConversationContextStore
from ..nlu.explanation import explain_command


class QueryRuntime(Protocol):
    """The one runtime service this controller uses."""

    trace: Any | None


class QueryController:
    """Answers cause, explanation, audit, history and advanced queries."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        context_store: ConversationContextStore,
        audit_trail: AuditTrail,
        runtime: QueryRuntime,
    ) -> None:
        self._hass = hass
        self._context_store = context_store
        self._audit_trail = audit_trail
        self._runtime = runtime

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    async def async_explain_cause(self, entity: EntitySnapshot) -> CauseExplanation:
        """Cause of a device's current state, strictly from evidence."""
        trace = self._runtime.trace
        store = trace.store if trace is not None else ExecutionTraceStore()
        index = trace.index if trace is not None else ContextIndex()
        state = self.hass.states.get(entity.entity_id)
        users: dict[str, str] = {}
        try:
            users = {
                user.id: user.name
                for user in await self.hass.auth.async_get_users()
                if getattr(user, "name", None)
            }
        except Exception:  # noqa: BLE001 - names are cosmetic only
            users = {}
        cause = explain_change(
            entity.entity_id,
            entity.friendly_name,
            getattr(state, "context", None),
            getattr(state, "last_changed", None) or entity.last_changed,
            store,
            index,
            users,
            name_of=lambda entity_id: (
                str(item.attributes.get("friendly_name"))
                if (item := self.hass.states.get(entity_id)) is not None
                and item.attributes.get("friendly_name")
                else None
            ),
        )
        return cause

    def handle_explanation_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: ConversationContext | None,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Explain the pending automation or the last understood command."""
        if pending is not None and pending.pending_automation_confirmation is not None:
            speech = (
                "Ich habe folgende Automation verstanden:\n"
                + render_automation_preview(
                    pending.pending_automation_confirmation.model, entities
                )
            )
        elif pending is not None and pending.last_command is not None:
            speech = explain_command(pending.last_command)
        elif pending is not None and (
            pending.memory is not None and pending.memory.explanation is not None
            or pending.last_explanation is not None
        ):
            speech = (
                pending.memory.explanation
                if pending.memory is not None and pending.memory.explanation is not None
                else pending.last_explanation
            )
        else:
            speech = "In diesem Gespräch gibt es noch keinen verstandenen Befehl."
        response.async_set_speech(speech)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def handle_audit_query(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
    ) -> conversation.ConversationResult:
        """Render today's bounded HomeIntent execution audit."""
        response.async_set_speech(render_today(self._audit_trail.today(dt_util.now())))
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_history_query_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        history_query: HistoryQuery | StateHistoryQuery | ComparativeHistoryQuery,
    ) -> conversation.ConversationResult:
        """Execute a read-only recorder query and render its answer."""
        self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(
            await async_execute_history_query(self.hass, history_query)
        )
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def handle_advanced_answer(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        answer: str,
    ) -> conversation.ConversationResult:
        """Return an already composed advanced-query answer."""
        self._context_store.clear(user_input.conversation_id)
        response.async_set_speech(answer)
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
