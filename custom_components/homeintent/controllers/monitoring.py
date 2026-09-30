"""Monitoring requests HomeIntent runs itself, and their dialogs (7.9).

The meaning of a monitoring request comes from the sentence-based event
reader (7.8.3).  Where Home Assistant cannot express it without new helpers
(a change by an amount within a window, W3), HomeIntent's own monitor
runtime runs it - staged here for an explicit "Ja" exactly like an
automation preview, never stored before.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import intent

from ..automation_results import MonitorProposalResult
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..goal_model import (
    DeliveryChannel,
    GoalKind,
    GoalLifecycle,
    GoalModel,
    GoalProvenance,
    GoalTrigger,
    NotificationSeverity,
)
from ..monitor_goal import MonitorRecord
from ..nlu.automation_confirmation import ConfirmationReply, classify_confirmation_reply
from ..security_control import conversation_user_id
from ..user_context import BindingStatus


class MonitoringController:
    """Stages and confirms HomeIntent-run monitors."""

    def __init__(self, *, runtime: Any) -> None:
        self._runtime = runtime

    def stage_value_monitor(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MonitorProposalResult,
    ) -> conversation.ConversationResult:
        """A change by an amount (7.9 W3): HomeIntent's own monitor, staged for
        an explicit "Ja" exactly like an automation preview."""
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        contexts = self._runtime.user_contexts
        binding = contexts.resolve_current_person(actor_id) if contexts is not None else None
        if binding is None or binding.status is not BindingStatus.RESOLVED or binding.person_entity_id is None:
            response.async_set_speech(
                "Ich weiß noch nicht, welche Person du bist. Bitte ordne deinem "
                "HomeIntent-Benutzer eine Person zu, dann kann ich dir solche Meldungen schicken."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        assert contexts is not None
        targets = contexts.resolve_notification_targets(binding.person_entity_id)
        if targets.status is not BindingStatus.RESOLVED:
            response.async_set_speech(
                "Welches bestätigte Gerät soll ich für diese Push-Benachrichtigung verwenden?"
                if targets.status is BindingStatus.AMBIGUOUS
                else "Für dich ist noch kein bestätigtes Push-Ziel konfiguriert."
            )
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if self._runtime.monitor_goals is None:
            response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        rule = result.proposal.rule
        goal = GoalModel(
            GoalKind.MONITOR_AND_NOTIFY,
            goal_id=f"value-change-{uuid.uuid4().hex[:12]}",
            trigger=GoalTrigger(
                "value_change", entity_id=rule.entity_id, delta=rule.delta, unit=rule.unit,
                direction=rule.direction.value, window_seconds=rule.window_seconds,
            ),
            recipient_person_ids=(binding.person_entity_id,),
            delivery_channel=DeliveryChannel.PUSH,
            notification_severity=NotificationSeverity.WARNING,
            provenance=GoalProvenance(
                source_utterance=user_input.text, user_id=actor_id, conversation_id=conversation_id,
            ),
            lifecycle=GoalLifecycle.MONITOR,
        )
        self._runtime.dialog_manager.create(
            conversation_id,
            "monitor-confirmation",
            DialogTaskKind.MONITOR_CONFIRMATION,
            DialogPriority.CONFIRMATION,
            reason="Eine Überwachung wartet auf ausdrückliche Bestätigung.",
            requested_by_user_id=actor_id,
            payload=(goal, result.proposal),
        )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)

    async def async_handle_monitor_confirmation(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: object,
    ) -> conversation.ConversationResult | None:
        """"Ja" stores the monitor, "Nein" discards it; nothing before."""
        manager = self._runtime.dialog_manager
        conversation_id = user_input.conversation_id
        payload = getattr(task, "payload", None)
        task_id = getattr(task, "task_id", "")
        if not (isinstance(payload, tuple) and len(payload) == 2 and isinstance(payload[0], GoalModel)):
            return None
        goal, proposal = payload
        if getattr(task, "requested_by_user_id", None) not in {None, conversation_user_id(user_input)}:
            response.async_set_speech("Diese Bestätigung gehört zu einem anderen Benutzer.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            if len(user_input.text.split()) > 3:
                # Another complete request ends the open question, without effect.
                manager.cancel(conversation_id, task_id)
                return None
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.cancel(conversation_id, task_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("In Ordnung, ich richte nichts ein.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        store = self._runtime.monitor_goals
        if store is None:
            response.async_set_speech("Die lokale Goal-Persistenz ist nicht verfügbar.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        confirmed = replace(goal, provenance=replace(goal.provenance, confirmed=True))
        window = goal.trigger.window_seconds if goal.trigger is not None else None
        await store.async_save(MonitorRecord(confirmed, cooldown_seconds=window or 300))
        response.async_set_speech(
            f"Eingerichtet. Ich überwache {proposal.subject} selbst und melde mich, sobald die "
            "Änderung eintritt."
        )
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)
