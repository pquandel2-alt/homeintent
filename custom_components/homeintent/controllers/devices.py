"""Device control of one turn (7.7, B4: from ``conversation.py``).

Runs what the arbiter decided for a direct command: previews and
confirmations, clarifications, the pending "Ja" of a critical action,
bound discourse results and undo. The only write path is
``service_executor.async_execute_service_plan`` (policy, EffectGraph,
NEVER_AUTO directly before the call).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import (
    Any,
    Awaitable,
    Callable,
    Protocol,
    Sequence,
)

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..areas import AreaSnapshot
from ..audit_log import AuditTrail
from ..const import NOT_UNDERSTOOD_TEXT
from ..controllers.replies import confirmation_question, execution_failure_text, with_effect_summary
from ..device_result import DeviceControlResult
from ..effect_graph import build_plan_effects
from ..engine import CommandPlan, MatchResult, NluEngine
from ..entities import EntitySnapshot
from ..execution_context import user_facing_error
from ..execution_policy import evaluate_service_plan, PolicyOutcome
from ..nlu.automation_confirmation import classify_confirmation_reply, ConfirmationReply
from ..nlu.context import (
    ConversationContext,
    ConversationContextStore,
    DialogTurnMemory,
    PendingSemanticCommand,
    PendingServiceConfirmation,
)
from ..nlu.dialog_focus import derive_dialog_focus, DialogFocus
from ..nlu.discourse import DiscourseRole, remember_entities, remember_query_group
from ..nlu.query_command import QueryResult
from ..nlu.semantic_utterance import analyse_utterance, SpeechAct
from ..nlu.understanding_context import UnderstandingContext
from ..phonetic_correction import phonetic_suggestions, PhoneticSuggestion
from ..security_control import conversation_user_id, user_is_admin
from ..semantic_dialog import continue_semantic_dialog, start_semantic_dialog
from ..service_call import QUERY_INTENTS, ServiceCallPlan
from ..service_executor import async_execute_service_plan, confirmed_scope
from ..undo import build_undo_plan, UndoPlan
from ..world_model import WorldModel

_LOGGER = logging.getLogger(__name__)


class DeviceRuntime(Protocol):
    """Runtime services of device control."""

    effect_monitor: Any
    shadow: Any


# Single source of truth for "which intents are queries" (V4.2) - read state
# and speak, never call a service - so Assist shows them as a QUERY_ANSWER
# rather than the default ACTION_DONE.
QUERY_INTENT_NAMES = frozenset(QUERY_INTENTS)


_GENERIC_UNKNOWN_TARGET = "Ich habe die Aktion erkannt, aber kein eindeutig passendes"


class DeviceController:
    """Executes, previews or clarifies understood device commands."""

    def __init__(
        self,
        *,
        hass: Callable[[], HomeAssistant],
        entry: ConfigEntry,
        context_store: ConversationContextStore,
        engine: NluEngine,
        world_model: Callable[[], WorldModel | None],
        audit_trail: AuditTrail,
        runtime: DeviceRuntime,
        entities: Callable[[], list[EntitySnapshot]],
        conversation_area: Callable[[Any], AreaSnapshot | None],
        ask_unknown_word: Callable[..., Awaitable[Any]],
        default_choice: Callable[..., Any],
        store_routine_binding: Callable[..., Awaitable[str]],
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._context_store = context_store
        self._engine = engine
        self._world_model_of = world_model
        self._audit_trail = audit_trail
        self._runtime = runtime
        self._entities = entities
        self._conversation_area = conversation_area
        # Learning hooks of the conversation (unknown word, default choice)
        # and the routine binding a confirmed "Ja" may store.
        self._async_ask_unknown_word = ask_unknown_word
        self._default_choice = default_choice
        self._async_store_routine_binding = store_routine_binding

    @property
    def hass(self) -> HomeAssistant:
        return self._hass()

    @property
    def _world_model(self) -> WorldModel | None:
        return self._world_model_of()

    async def async_handle_match_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Authorize, execute and remember one regular engine match."""
        self._runtime.shadow.observe(self.entry.options, user_input.text, entities, result)
        if (
            result.plan is not None
            and analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if result.plan is not None:
            policy = evaluate_service_plan(
                result.plan,
                entities,
                self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=conversation_user_id(user_input),
                effects=build_plan_effects(self.hass, result.plan),
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Diese Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            if policy.outcome is PolicyOutcome.CONFIRM:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=self.pending_confirmation(
                            entities,
                            result.plan,
                            result.response_text,
                            conversation_user_id(user_input),
                            build_undo_plan(
                                result.plan,
                                entities,
                                requested_by_user_id=conversation_user_id(user_input),
                            ),
                            origin=result.origin,
                            binding_confirmed=result.binding_confirmed,
                        ),
                    ),
                )
                question = result.proposal_text or confirmation_question(result.response_text)
                response.async_set_speech(f"{policy.note} {question}" if policy.note else question)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )

        previous_context = self._context_store.get(user_input.conversation_id)
        undo_plan = (
            build_undo_plan(
                result.plan,
                entities,
                requested_by_user_id=conversation_user_id(user_input),
            )
            if result.plan is not None
            else None
        )
        if result.command is not None:
            focus = derive_dialog_focus(result.command)
            discourse = remember_entities(
                previous_context.discourse if previous_context else None,
                result.command.entities,
                role=(
                    DiscourseRole.ACTION_TARGET
                    if result.plan is not None
                    else DiscourseRole.QUERY_RESULT
                ),
                active_property=focus.property,
                active_action=(
                    result.command.intent if result.plan is not None else None
                ),
                semantic_graph=result.command.source_frame.semantic_graph,
            )
            query_result = result.command.parameters.get("query_result")
            if isinstance(query_result, QueryResult) and (
                query_result.member_ids
                or query_result.entities
                or query_result.devices
                or query_result.areas
                or query_result.floors
            ):
                discourse = remember_query_group(
                    discourse,
                    query_result,
                    semantic_graph=result.command.source_frame.semantic_graph,
                )
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=result.command,
                    last_entities=tuple(result.command.entities),
                    last_area=result.command.area,
                    pending_clarification=None,
                    focus=focus,
                    discourse=discourse,
                    pending_undo=undo_plan,
                    last_explanation=result.explanation_text,
                    memory=DialogTurnMemory(
                        source_text=user_input.text,
                        entities=tuple(result.command.entities),
                        explanation=result.explanation_text,
                        command=result.command,
                    ),
                ),
            )
        elif result.context_entities:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=result.context_entities,
                    last_area=None,
                    pending_clarification=None,
                    last_query_predicate=result.context_predicate,
                    last_explanation=result.explanation_text,
                    discourse=remember_entities(
                        previous_context.discourse if previous_context else None,
                        result.context_entities,
                        role=DiscourseRole.QUERY_RESULT,
                    ),
                    memory=DialogTurnMemory(
                        source_text=user_input.text,
                        entities=result.context_entities,
                        predicate=result.context_predicate,
                        explanation=result.explanation_text,
                    ),
                ),
            )
        else:
            self._context_store.clear(user_input.conversation_id)

        if result.plan is not None:
            execution = await async_execute_service_plan(
                self.hass,
                result.plan,
                entities,
                self.entry.options,
                is_admin=await user_is_admin(self.hass, user_input),
                user_id=conversation_user_id(user_input),
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=conversation_user_id(user_input),
                effect_monitor=self._runtime.effect_monitor,
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if not execution.executed:
                self._context_store.clear(user_input.conversation_id)
                _LOGGER.error(
                    "Service call %s.%s on %s failed: %s",
                    result.plan.domain,
                    result.plan.service,
                    result.plan.entity_id,
                    execution.error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    execution_failure_text(execution),
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            result = replace(
                result, response_text=with_effect_summary(result.response_text, execution)
            )

        if (
            result.command is not None and result.command.intent in QUERY_INTENT_NAMES
        ) or (
            analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
            or result.context_predicate is not None
        ):
            response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_command_plan(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: CommandPlan,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Authorize and execute an already validated multi-command plan."""
        self._runtime.shadow.observe(self.entry.options, user_input.text, entities, result)
        if (
            analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
            and any(command.plan is not None for command in result.commands)
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        # Every sub-command was validated together before this point. A HA-side
        # runtime failure remains fail-fast because service calls are not
        # transactional and already executed calls cannot be rolled back safely.
        is_admin = await user_is_admin(self.hass, user_input)
        actor_id = conversation_user_id(user_input)
        for sub_result in result.commands:
            if sub_result.plan is None:
                continue
            policy = evaluate_service_plan(
                sub_result.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                effects=build_plan_effects(self.hass, sub_result.plan),
                origin=result.origin,
                binding_confirmed=result.binding_confirmed,
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Mindestens eine Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
            if (
                policy.outcome is PolicyOutcome.CONFIRM
                and result.confirmation_text is None
                and result.proposal_text is not None
            ):
                # An implicit need proposed as a whole (implicit_action_level).
                result = replace(result, confirmation_text=result.proposal_text)
            if (
                policy.outcome is PolicyOutcome.CONFIRM
                and result.confirmation_text is None
            ):
                # A previewed group plan is confirmed as a whole below; every
                # other multi-command plan needs one confirmation per action.
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    "Sicherheitskritische Aktionen müssen einzeln bestätigt werden.",
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
        if result.confirmation_text is not None:
            plans = [
                sub_result.plan for sub_result in result.commands
                if sub_result.plan is not None
            ]
            if plans:
                success = " ".join(
                    sub_result.response_text for sub_result in result.commands
                )
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=self.pending_confirmation(
                            entities,
                            plans[0],
                            success,
                            actor_id,
                            None,
                            tuple(plans[1:]),
                            origin=result.origin,
                            binding_confirmed=result.binding_confirmed,
                            binding_offer=(
                                (result.routine_key, result.routine_candidates[0])
                                if result.routine_key is not None
                                and not result.binding_confirmed
                                and len(result.routine_candidates) == 1
                                else None
                            ),
                        ),
                    ),
                )
                response.async_set_speech(result.confirmation_text)
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
        self._context_store.clear(user_input.conversation_id)
        response_parts: list[str] = []
        multi_undo_parts: list[UndoPlan] = []
        multi_undo_supported = True
        for sub_result in result.commands:
            if sub_result.plan is None:
                response_parts.append(sub_result.response_text)
                continue
            inverse = build_undo_plan(
                sub_result.plan,
                entities,
                requested_by_user_id=actor_id,
            )
            if inverse is None:
                multi_undo_supported = False
            else:
                multi_undo_parts.append(inverse)
            execution = await async_execute_service_plan(
                self.hass,
                sub_result.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=actor_id,
                effect_monitor=self._runtime.effect_monitor,
                origin=result.origin,
            )
            if not execution.executed:
                error = execution.error or "Die Aktion konnte nicht ausgeführt werden."
                _LOGGER.error(
                    "Service call %s.%s on %s failed: %s",
                    sub_result.plan.domain,
                    sub_result.plan.service,
                    sub_result.plan.entity_id,
                    error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {error}",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            response_parts.append(sub_result.response_text)
        if multi_undo_supported and multi_undo_parts:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_undo=UndoPlan(
                        tuple(
                            plan
                            for undo in reversed(multi_undo_parts)
                            for plan in undo.plans
                        ),
                        actor_id,
                    ),
                ),
            )
        response.async_set_speech(" ".join(response_parts))
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_device_control_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        device_control: DeviceControlResult,
    ) -> conversation.ConversationResult:
        """Apply the common safety, execution and context policy once."""
        if (
            device_control.plan is not None
            and analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        ):
            self._context_store.clear(user_input.conversation_id)
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "Ich habe eine Frage erkannt und führe deshalb keine Aktion aus.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if device_control.plan is None:
            self._context_store.clear(user_input.conversation_id)
            if device_control.is_query:
                response.response_type = intent.IntentResponseType.QUERY_ANSWER
            response.async_set_speech(device_control.response_text)
        else:
            resolved_entities = list(device_control.resolved_entities)
            actor_id = conversation_user_id(user_input)
            is_admin = await user_is_admin(self.hass, user_input)
            policy = evaluate_service_plan(
                device_control.plan,
                self._entities(),
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                effects=build_plan_effects(self.hass, device_control.plan),
            )
            if policy.outcome is PolicyOutcome.DENY:
                self._context_store.clear(user_input.conversation_id)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    policy.reason or "Diese Aktion ist nicht erlaubt.",
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            needs_confirmation = (
                device_control.requires_confirmation
                or policy.outcome is PolicyOutcome.CONFIRM
            )
            if needs_confirmation:
                self._context_store.set(
                    user_input.conversation_id,
                    ConversationContext(
                        last_command=None,
                        last_entities=(),
                        last_area=None,
                        pending_clarification=None,
                        pending_service_confirmation=self.pending_confirmation(
                            resolved_entities,
                            device_control.plan,
                            device_control.response_text,
                            actor_id,
                            build_undo_plan(
                                device_control.plan,
                                resolved_entities,
                                requested_by_user_id=actor_id,
                            ),
                        ),
                    ),
                )
                response.async_set_speech(
                    confirmation_question(device_control.response_text, policy.note)
                )
                return conversation.ConversationResult(
                    response=response, conversation_id=user_input.conversation_id
                )
            execution = await async_execute_service_plan(
                self.hass,
                device_control.plan,
                self._entities(),
                self.entry.options,
                is_admin=is_admin,
                user_id=actor_id,
                confirmed=False,
                audit_trail=self._audit_trail,
                audit_actor_id=actor_id,
                effect_monitor=self._runtime.effect_monitor,
            )
            if not execution.executed:
                self._context_store.clear(user_input.conversation_id)
                error = execution.error or "Die Aktion konnte nicht ausgeführt werden."
                _LOGGER.error(
                    "Device-control service call %s.%s on %s failed: %s",
                    device_control.plan.domain,
                    device_control.plan.service,
                    device_control.plan.entity_id,
                    error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Ausführen: {error}",
                )
            else:
                response.async_set_speech(device_control.response_text)
                if resolved_entities:
                    controlled = resolved_entities[0]
                    self._context_store.set(
                        user_input.conversation_id,
                        ConversationContext(
                            last_command=None,
                            last_entities=tuple(resolved_entities),
                            last_area=None,
                            pending_clarification=None,
                            focus=DialogFocus(
                                property=None,
                                scope_kind=("entity" if len(resolved_entities) == 1 else None),
                                scope_id=(controlled.entity_id if len(resolved_entities) == 1 else None),
                                candidate_entity_ids=tuple(
                                    entity.entity_id for entity in resolved_entities
                                ),
                                source_intent="DeviceControl:" + device_control.plan.service,
                            ),
                            pending_undo=build_undo_plan(
                                device_control.plan,
                                resolved_entities,
                                requested_by_user_id=actor_id,
                            ),
                        ),
                    )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_service_confirmation_reply(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        confirmation: PendingServiceConfirmation,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Confirm a high-risk lock or garage movement before execution."""
        current_user_id = conversation_user_id(user_input)
        if (
            confirmation.requested_by_user_id is not None
            and confirmation.requested_by_user_id != current_user_id
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Diese Bestätigung gehört zu einem anderen Benutzer.",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        reply = classify_confirmation_reply(user_input.text)
        if reply is ConfirmationReply.UNCLEAR:
            response.async_set_speech("Bitte antworte mit Ja oder Nein.")
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        self._context_store.clear(user_input.conversation_id)
        if reply is ConfirmationReply.NO:
            response.async_set_speech("Abgebrochen. Es wurde nichts ausgeführt.")
        else:
            undo = build_undo_plan(
                confirmation.plan,
                entities,
                requested_by_user_id=current_user_id,
            )
            is_admin = await user_is_admin(self.hass, user_input)
            execution = await async_execute_service_plan(
                self.hass,
                confirmation.plan,
                entities,
                self.entry.options,
                is_admin=is_admin,
                user_id=current_user_id,
                confirmed=True,
                audit_trail=self._audit_trail,
                audit_actor_id=current_user_id,
                effect_monitor=self._runtime.effect_monitor,
                origin=confirmation.origin,
                binding_confirmed=confirmation.binding_confirmed,
                scope=confirmation.scope,
            )
            for additional in confirmation.additional_plans:
                if not execution.executed:
                    break
                execution = await async_execute_service_plan(
                    self.hass,
                    additional,
                    entities,
                    self.entry.options,
                    is_admin=is_admin,
                    user_id=current_user_id,
                    confirmed=True,
                    audit_trail=self._audit_trail,
                    audit_actor_id=current_user_id,
                    effect_monitor=self._runtime.effect_monitor,
                    origin=confirmation.origin,
                    scope=confirmation.scope,
                )
            if not execution.executed:
                _LOGGER.error(
                    "Confirmed service call %s.%s on %s failed: %s",
                    confirmation.plan.domain,
                    confirmation.plan.service,
                    confirmation.plan.entity_id,
                    execution.error,
                )
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    execution_failure_text(execution),
                )
            else:
                spoken = with_effect_summary(confirmation.success_text, execution)
                if confirmation.binding_offer is not None:
                    spoken = f"{spoken} " + await self._async_store_routine_binding(
                        *confirmation.binding_offer, current_user_id
                    )
                response.async_set_speech(spoken)
                if undo is not None:
                    self._context_store.set(
                        user_input.conversation_id,
                        ConversationContext(
                            last_command=None,
                            last_entities=(),
                            last_area=None,
                            pending_clarification=None,
                            pending_undo=undo,
                        ),
                    )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_no_match(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Try bounded correction/dialog fallbacks for an unmatched turn."""
        is_query = analyse_utterance(user_input.text).speech_act is SpeechAct.QUERY
        corrected_plans: dict[
            tuple, tuple[ServiceCallPlan, str, PhoneticSuggestion]
        ] = {}
        for suggestion in (
            () if is_query else phonetic_suggestions(user_input.text, entities)
        ):
            corrected = self._engine.understand(
                suggestion.corrected_text,
                entities,
                self._world_model,
                context=UnderstandingContext(
                    source_area=self._conversation_area(user_input)
                ),
            ).payload
            plan = getattr(corrected, "plan", None)
            success = getattr(corrected, "response_text", None)
            if plan is None or not success:
                continue
            entity_ids = (
                tuple(plan.entity_id)
                if isinstance(plan.entity_id, list)
                else (plan.entity_id,)
            )
            key = (plan.domain, plan.service, entity_ids, repr(sorted(plan.data.items())))
            corrected_plans[key] = (plan, success, suggestion)
        if len(corrected_plans) == 1:
            plan, success, correction = next(iter(corrected_plans.values()))
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_service_confirmation=self.pending_confirmation(
                        entities,
                        plan,
                        success,
                        conversation_user_id(user_input),
                        build_undo_plan(
                            plan,
                            entities,
                            requested_by_user_id=conversation_user_id(user_input),
                        ),
                    ),
                ),
            )
            response.async_set_speech(
                f"Meintest du „{correction.corrected_term}“? "
                f"Soll ich {success.rstrip('.')}?"
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )

        dialog = None if is_query else start_semantic_dialog(user_input.text, entities)
        if dialog is not None and dialog.result is not None:
            return await self.async_handle_device_control_result(
                user_input, response, dialog.result
            )
        if dialog is not None and dialog.pending is not None:
            self._context_store.set(
                user_input.conversation_id,
                ConversationContext(
                    last_command=None,
                    last_entities=(),
                    last_area=None,
                    pending_clarification=None,
                    pending_semantic_command=dialog.pending,
                ),
            )
            response.async_set_speech(dialog.question)
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        feedback = self._engine.failure_feedback(user_input.text, entities)
        if not is_query and (feedback is None or feedback.startswith(_GENERIC_UNKNOWN_TARGET)):
            # An unknown device word is asked about, never guessed (7.4.1);
            # specific explanations (unknown floor, capabilities) still win.
            area = self._conversation_area(user_input)
            asked = await self._async_ask_unknown_word(
                user_input, response, entities, area.area_id if area is not None else None
            )
            if asked is not None:
                return asked
        bare_reply = (
            len(user_input.text.split()) <= 3
            and classify_confirmation_reply(user_input.text)
            in {ConfirmationReply.YES, ConfirmationReply.NO}
        )
        response.async_set_error(
            intent.IntentResponseErrorCode.NO_INTENT_MATCH,
            feedback
            or (
                # A bare "Ja"/"Nein" with nothing open (for example after a
                # refusal) is answered honestly (7.3.3).
                "Gerade ist keine Frage offen, auf die sich das beziehen könnte. "
                "Ich habe nichts ausgeführt."
                if bare_reply else NOT_UNDERSTOOD_TEXT
            ),
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_clarify(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Ask - unless the speaker confirmed a default choice for exactly
        this question (7.4.1); the chosen candidate is one the question
        itself offered and runs through the ordinary handlers."""
        clarification = result.clarification
        if clarification is not None:
            area = self._conversation_area(user_input)
            chosen = self._default_choice(
                user_input, clarification.candidates,
                area.area_id if area is not None else None,
            )
            if chosen is not None:
                resolved = self._engine.resolve_clarification(
                    chosen.entity_id, clarification, entities
                )
                if resolved is not None:
                    return await self.async_handle_match_result(
                        user_input, response, resolved, entities
                    )
        return self.handle_clarification_result(user_input, response, result)

    def handle_clarification_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult,
    ) -> conversation.ConversationResult:
        """Store an ambiguous match until the user selects a candidate."""
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=result.clarification,
            ),
        )
        response.async_set_speech(result.response_text)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    async def async_handle_bound_result(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        result: MatchResult | CommandPlan,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Run one meaning-model result through the ordinary handlers."""
        if user_input.text.strip().casefold().startswith("und ") and user_input.text.rstrip().endswith("?"):
            # An elliptical "Und im Bad?" after an action repeats it; the
            # question mark is not a request for information here.
            user_input = replace(user_input, text=user_input.text.rstrip(" ?") + ".")
        if isinstance(result, CommandPlan):
            return await self.async_handle_command_plan(user_input, response, result, entities)
        if result.failure_text is not None:
            response.async_set_error(
                intent.IntentResponseErrorCode.NO_VALID_TARGETS, result.failure_text
            )
            return conversation.ConversationResult(
                response=response, conversation_id=user_input.conversation_id
            )
        if result.clarification is not None:
            return await self.async_clarify(user_input, response, result, entities)
        return await self.async_handle_match_result(user_input, response, result, entities)

    async def async_handle_pending_semantic_command(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: PendingSemanticCommand,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Continue a deterministic slot-filling device dialog."""
        # A complete new turn supersedes the open slot dialog.  Otherwise a
        # command such as "Fahre die Rollläden ..." is interpreted as an
        # attempted thermostat name merely because the preceding turn asked
        # "Welche Heizung?".  Only a fully parsed fresh command/query (or a
        # clearly command-shaped no-match that may enter bounded ASR
        # correction) escapes; short answers such as "Küche" continue below.
        fresh = self._engine.understand(
            user_input.text,
            entities,
            self._world_model,
            context=UnderstandingContext(
                source_area=self._conversation_area(user_input)
            ),
        ).payload
        if isinstance(fresh, CommandPlan):
            self._context_store.clear(user_input.conversation_id)
            return await self.async_handle_command_plan(
                user_input, response, fresh, entities
            )
        if isinstance(fresh, MatchResult) and (
            fresh.command is not None or fresh.clarification is not None
        ):
            self._context_store.clear(user_input.conversation_id)
            if fresh.clarification is not None:
                return await self.async_clarify(
                    user_input, response, fresh, entities
                )
            return await self.async_handle_match_result(
                user_input, response, fresh, entities
            )
        if analyse_utterance(user_input.text).speech_act is SpeechAct.COMMAND:
            self._context_store.clear(user_input.conversation_id)
            return await self.async_handle_no_match(
                user_input, response, entities
            )

        dialog = continue_semantic_dialog(user_input.text, pending)
        if dialog.result is not None:
            self._context_store.clear(user_input.conversation_id)
            return await self.async_handle_device_control_result(
                user_input, response, dialog.result
            )
        self._context_store.set(
            user_input.conversation_id,
            ConversationContext(
                last_command=None,
                last_entities=(),
                last_area=None,
                pending_clarification=None,
                pending_semantic_command=dialog.pending,
            ),
        )
        response.async_set_speech(dialog.question)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    def pending_confirmation(
        self, entities: Sequence[EntitySnapshot], plan: ServiceCallPlan, *args: Any, **kwargs: Any
    ) -> PendingServiceConfirmation:
        """A pending "Ja" bound to the risk and effects it was asked for (7.7)."""
        pending = PendingServiceConfirmation(plan, *args, **kwargs)
        return replace(pending, scope=confirmed_scope(
            self.hass, (plan, *pending.additional_plans), entities, self.entry.options,
            origin=pending.origin, binding_confirmed=pending.binding_confirmed,
        ))

    async def async_handle_undo_request(
        self,
        user_input: conversation.ConversationInput,
        response: intent.IntentResponse,
        pending: ConversationContext | None,
        entities: list[EntitySnapshot],
    ) -> conversation.ConversationResult:
        """Undo the most recent safely reversible action for this user."""
        undo = pending.pending_undo if pending is not None else None
        current_user_id = conversation_user_id(user_input)
        if undo is None:
            response.async_set_speech(
                "Es gibt keine kürzlich ausgeführte, sicher rückgängig machbare Aktion."
            )
        elif (
            undo.requested_by_user_id is not None
            and undo.requested_by_user_id != current_user_id
        ):
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                "Diese Rücknahme gehört zu einem anderen Benutzer.",
            )
        else:
            is_admin = await user_is_admin(self.hass, user_input)
            denial = next(
                (
                    decision.reason
                    for plan in undo.plans
                    if (
                        decision := evaluate_service_plan(
                            plan,
                            entities,
                            self.entry.options,
                            is_admin=is_admin,
                            user_id=current_user_id,
                            effects=build_plan_effects(self.hass, plan),
                        )
                    ).outcome
                    is PolicyOutcome.DENY
                ),
                None,
            )
            if denial is not None:
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    denial,
                )
                return conversation.ConversationResult(
                    response=response,
                    conversation_id=user_input.conversation_id,
                )
            self._context_store.clear(user_input.conversation_id)
            try:
                for plan in undo.plans:
                    execution = await async_execute_service_plan(
                        self.hass,
                        plan,
                        entities,
                        self.entry.options,
                        is_admin=is_admin,
                        user_id=current_user_id,
                        confirmed=True,
                        audit_trail=self._audit_trail,
                        audit_actor_id=current_user_id,
                        effect_monitor=self._runtime.effect_monitor,
                    )
                    if not execution.executed:
                        raise RuntimeError(execution.error or "Rücknahme nicht erlaubt")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Undo service call failed: %s", err, exc_info=True)
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                    f"Fehler beim Rückgängigmachen: {user_facing_error(err)}",
                )
            else:
                response.async_set_speech("Die letzte Aktion wurde rückgängig gemacht.")
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
