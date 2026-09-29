"""Routine bindings by voice (7.3.3, 7.7 B4: from ``conversation.py``).

"Welche Routine meinst du?", "Vergiss die Schlafroutine", "Schlafen ist
ab jetzt das Skript X". A binding is stored only after an explicit
choice or "Ja" and a successful run; binding never authorises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from ..audit_log import AuditTrail
from ..bindings import BindingKind, BindingScope
from ..controllers.replies import confirmation_question, with_effect_summary
from ..dialog_manager import DialogPriority, DialogTaskKind
from ..effect_graph import build_plan_effects
from ..entities import EntitySnapshot
from ..execution_policy import evaluate_service_plan, PolicyOutcome
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.context import ConversationContext, ConversationContextStore
from ..nlu.need_semantics import routine_concept_by_key, ROUTINE_CONCEPTS
from ..routine_binding_intent import (
    choose_candidate,
    RoutineBindingOperation,
    RoutineBindingRequest,
)
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..service_executor import async_execute_service_plan


class RoutineRuntime(Protocol):
    """Runtime services of routine bindings."""

    bindings: Any
    dialog_manager: Any
    effect_monitor: Any
    learning_center_revision: Any


@dataclass(frozen=True)
class RoutineSelection:
    """Open question "Welche Routine meinst du: A, B oder C?"."""

    concept_key: str
    candidate_ids: tuple[str, ...]



@dataclass(frozen=True)
class RoutineBindConfirmation:
    """Open question "Soll ich für „…“ künftig X nehmen?"."""

    concept_key: str
    entity_id: str
    personal: bool = False


class RoutineController:
    """Handles routine choices and spoken binding management."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        context_store: ConversationContextStore,
        audit_trail: AuditTrail,
        runtime: RoutineRuntime,
        pending_confirmation: Callable[..., Any],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._context_store = context_store
        self._audit_trail = audit_trail
        self._runtime = runtime
        self._pending_confirmation = pending_confirmation

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    def bindings_for(self, user_id: str | None) -> dict[str, str]:
        """Concept -> bound script/scene for this speaker (own before household)."""
        store = self._runtime.bindings
        found: dict[str, str] = {}
        for concept in ROUTINE_CONCEPTS:
            binding = store.find(BindingKind.ROUTINE, concept.key, user_id)
            if binding is not None:
                found[concept.key] = binding.target
        return found

    async def async_store_binding(
        self, concept_key: str, entity_id: str, user_id: str | None, *, personal: bool = False
    ) -> str:
        """Store after an explicit "Ja"/choice; returns the spoken note."""
        concept = routine_concept_by_key(concept_key)
        await self._runtime.bindings.async_bind(
            BindingKind.ROUTINE,
            concept_key,
            entity_id,
            confirmed=True,
            scope=BindingScope.USER if personal and user_id else BindingScope.HOUSEHOLD,
            user_id=user_id if personal else None,
            created_by=user_id,
            now=dt_util.now(),
        )
        self._runtime.learning_center_revision.bump()
        label = concept.label if concept is not None else concept_key
        return f"Das merke ich mir für „{label}“."

    async def async_handle_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        request: RoutineBindingRequest,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """"Vergiss die Schlafroutine" / "Schlafen ist ab jetzt das Skript X"."""
        user_id = conversation_user_id(user_input)
        store = self._runtime.bindings
        label = request.concept.label
        if request.operation is RoutineBindingOperation.SHOW:
            binding = store.find(BindingKind.ROUTINE, request.concept.key, user_id)
            name = (
                next(
                    (item.friendly_name for item in entities if item.entity_id == binding.target),
                    binding.target,
                )
                if binding is not None else None
            )
            response.async_set_speech(
                f"Für „{label}“ nehme ich {name}." if name
                else f"Für „{label}“ ist noch keine Routine hinterlegt."
            )
        elif request.operation is RoutineBindingOperation.FORGET:
            binding = store.find(BindingKind.ROUTINE, request.concept.key, user_id)
            is_admin = await user_is_admin(self.hass, user_input)
            if binding is None:
                response.async_set_speech(f"Für „{label}“ war keine Routine hinterlegt.")
            elif (
                binding.scope is BindingScope.HOUSEHOLD
                and not is_admin
                and binding.created_by not in {None, user_id}
            ):
                response.async_set_speech(
                    f"Die Routine für „{label}“ gilt für den ganzen Haushalt; "
                    "ändern darf sie nur, wer sie angelegt hat, oder ein Administrator."
                )
            else:
                await store.async_remove(binding.binding_id)
                self._runtime.learning_center_revision.bump()
                response.async_set_speech(
                    f"Erledigt. Für „{label}“ ist keine Routine mehr hinterlegt; "
                    "beim nächsten Mal frage ich wieder nach."
                )
        elif request.target is None:
            names = ", ".join(entity.friendly_name for entity in request.candidates)
            response.async_set_speech(
                f"Welche Routine meinst du genau: {names}?" if request.candidates
                else "Ein Skript oder eine Szene mit diesem Namen finde ich nicht."
            )
        else:
            kind = "die Szene" if request.target.domain == "scene" else "das Skript"
            self._runtime.dialog_manager.create(
                user_input.conversation_id,
                "routine-binding",
                DialogTaskKind.ROUTINE_BINDING,
                DialogPriority.CONFIRMATION,
                reason="Eine Routine-Bindung muss ausdrücklich bestätigt werden.",
                requested_by_user_id=user_id,
                payload=RoutineBindConfirmation(
                    request.concept.key, request.target.entity_id, request.personal
                ),
            )
            response.async_set_speech(
                f"Soll ich für „{label}“ künftig {kind} {request.target.friendly_name} nehmen?"
            )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_task(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        task: Any,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult | None:
        """Answer to a routine choice or a spoken binding confirmation."""
        manager = self._runtime.dialog_manager
        conversation_id = user_input.conversation_id
        actor_id = conversation_user_id(user_input)
        if task.requested_by_user_id is not None and task.requested_by_user_id != actor_id:
            response.async_set_speech("Diese Rückfrage gehört zu einem anderen Benutzer.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        payload = task.payload
        reply = classify_confirmation_reply(user_input.text)
        if isinstance(payload, RoutineBindConfirmation):
            manager.cancel(conversation_id, task.task_id)
            if reply is ConfirmationReply.YES:
                if not any(entity.entity_id == payload.entity_id for entity in entities):
                    response.async_set_speech(
                        "Diese Routine ist nicht mehr freigegeben. Ich habe nichts gespeichert."
                    )
                else:
                    note = await self.async_store_binding(
                        payload.concept_key, payload.entity_id, actor_id, personal=payload.personal
                    )
                    response.async_set_speech(f"Gespeichert. {note}")
            elif reply is ConfirmationReply.NO:
                response.async_set_speech("In Ordnung, ich habe nichts gespeichert.")
            else:
                return None
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        if not isinstance(payload, RoutineSelection):
            return None
        if reply is ConfirmationReply.NO:
            manager.cancel(conversation_id, task.task_id)
            response.async_set_speech("In Ordnung, ich habe nichts gestartet.")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        candidates = [
            entity for candidate_id in payload.candidate_ids
            for entity in entities if entity.entity_id == candidate_id
        ]
        chosen = choose_candidate(user_input.text, candidates)
        if chosen is None:
            if len(user_input.text.split()) > 4:
                manager.cancel(conversation_id, task.task_id)
                return None  # a new request, not an answer
            names = ", ".join(entity.friendly_name for entity in candidates)
            response.async_set_speech(f"Welche meinst du: {names}?")
            return conversation.ConversationResult(response=response, conversation_id=conversation_id)
        manager.cancel(conversation_id, task.task_id)
        # The user named the routine: evaluated like the explicit command.
        plan = ServiceCallPlan(chosen.domain, "turn_on", chosen.entity_id)
        is_admin = await user_is_admin(self.hass, user_input)
        policy = evaluate_service_plan(
            plan, entities, self.entry.options, is_admin=is_admin, user_id=actor_id,
            effects=build_plan_effects(self.hass, plan),
        )
        success = f"{chosen.friendly_name} ausgeführt."
        if policy.outcome is PolicyOutcome.DENY:
            response.async_set_speech(
                (policy.reason or "Diese Routine darf ich nicht ausführen.")
                + " Ich habe nichts gespeichert."
            )
        elif policy.outcome is PolicyOutcome.CONFIRM:
            self._context_store.set(
                conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_service_confirmation=self._pending_confirmation(
                        entities, plan, success, actor_id,
                        binding_offer=(payload.concept_key, chosen.entity_id),
                    ),
                ),
            )
            question = confirmation_question(success)
            response.async_set_speech(f"{policy.note} {question}" if policy.note else question)
        else:
            execution = await async_execute_service_plan(
                self.hass, plan, entities, self.entry.options,
                is_admin=is_admin, user_id=actor_id, confirmed=False,
                audit_trail=self._audit_trail, audit_actor_id=actor_id,
                effect_monitor=self._runtime.effect_monitor,
            )
            if not execution.executed:
                response.async_set_speech(
                    f"{execution.error or 'Das hat nicht geklappt.'} Ich habe nichts gespeichert."
                )
            else:
                note = await self.async_store_binding(
                    payload.concept_key, chosen.entity_id, actor_id
                )
                response.async_set_speech(f"{with_effect_summary(success, execution)} {note}")
        return conversation.ConversationResult(response=response, conversation_id=conversation_id)
