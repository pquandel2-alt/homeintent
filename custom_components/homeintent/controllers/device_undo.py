"""Safe execution of one consumed device-undo request.

The controller adapter deliberately delegates the complete state machine to
this module.  Keeping the evidence, policy and execution order together makes
the race-safety contract reviewable without growing ``controllers/devices``
into another orchestration monolith.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..audit_log import AuditTrail
from ..effect_monitor import EffectMonitor
from ..entities import EntitySnapshot
from ..execution_context import current_turn, new_execution_context
from ..nlu.context import ConversationContext, ConversationContextStore
from ..security_control import conversation_user_id, user_is_admin
from ..service_call import ServiceCallPlan
from ..service_executor import (
    async_execute_service_plan,
    async_preflight_service_plan,
    confirmed_scope,
)
from ..undo import (
    UndoPlan,
    UndoRemainder,
    advance_undo_remainder,
    async_bind_undo_when_observed,
    bind_undo_to_observed_states,
    next_undo_guard,
    stop_undo_remainder,
    undo_matches_observed_states,
)


_LOGGER = logging.getLogger(__name__)

_OPERATION_LABELS = {
    "set_hvac_mode": "Betriebsmodus",
    "set_temperature": "Temperatur",
    "set_humidity": "Luftfeuchtigkeit",
    "set_cover_position": "Position",
    "set_value": "Wert",
    "select_option": "Auswahl",
}


def _target_ids(plan: ServiceCallPlan) -> tuple[str, ...]:
    return (
        (plan.entity_id,)
        if isinstance(plan.entity_id, str)
        else tuple(plan.entity_id)
    )


def describe_undo_plans(
    plans: Sequence[ServiceCallPlan], names: Mapping[str, str]
) -> str:
    """Describe inverse steps without hiding two operations on one target."""

    described: list[str] = []
    seen: set[tuple[str, str | None]] = set()
    for plan in plans:
        operation = _OPERATION_LABELS.get(plan.service)
        for entity_id in _target_ids(plan):
            key = (entity_id, operation)
            if key in seen:
                continue
            seen.add(key)
            label = names.get(entity_id, entity_id)
            described.append(
                f"{label} ({operation})" if operation is not None else label
            )
    return " und ".join(described)


def partial_undo_failure_text(
    remainder: UndoRemainder,
    names: Mapping[str, str],
    reason: str,
    *,
    external: bool = False,
    uncertain_plan: ServiceCallPlan | None = None,
) -> str:
    """Render an honest partial result from the typed terminal remainder."""

    parts = ["Rücknahme abgebrochen."]
    completed = tuple(step.plan for step in remainder.completed_steps)
    if completed:
        parts.append(
            "Bereits rückgängig gemacht: "
            + describe_undo_plans(completed, names)
            + "."
        )
    if external and remainder.aborted_plans:
        parts.append(
            "Extern verändert und nicht rückgängig gemacht: "
            + describe_undo_plans(remainder.aborted_plans, names)
            + "."
        )
    elif uncertain_plan is not None:
        parts.append(
            "Der inverse Aufruf für "
            + describe_undo_plans((uncertain_plan,), names)
            + " wurde angenommen, seine Wirkung ist aber nicht sicher belegt."
        )
    elif remainder.aborted_plans:
        parts.append(
            "Nicht rückgängig gemacht: "
            + describe_undo_plans(remainder.aborted_plans, names)
            + "."
        )
    if remainder.skipped_plans:
        parts.append(
            "Danach nicht mehr ausgeführt: "
            + describe_undo_plans(remainder.skipped_plans, names)
            + "."
        )
    parts.append(reason)
    return " ".join(parts)


async def async_handle_device_undo(
    *,
    hass: HomeAssistant,
    options: Mapping[str, object],
    context_store: ConversationContextStore,
    audit_trail: AuditTrail,
    effect_monitor: EffectMonitor | None,
    entities_provider: Callable[[], list[EntitySnapshot]],
    user_input: conversation.ConversationInput,
    response: intent.IntentResponse,
    pending: ConversationContext | None,
    entities: list[EntitySnapshot],
) -> conversation.ConversationResult:
    """Consume and execute the latest fully evidenced inverse plan."""

    undo = pending.pending_undo if pending is not None else None
    current_user_id = conversation_user_id(user_input)
    if undo is None:
        response.async_set_speech(
            "Es gibt keine kürzlich ausgeführte, sicher rückgängig machbare Aktion."
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if (
        undo.requested_by_user_id is not None
        and undo.requested_by_user_id != current_user_id
    ):
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
            "Diese Rücknahme gehört zu einem anderen Benutzer.",
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
    if not undo_matches_observed_states(undo, hass.states.get):
        context_store.clear(user_input.conversation_id)
        response.async_set_speech(
            "Die Geräte wurden seit der Aktion verändert. "
            "Ich habe deshalb nichts zurückgenommen."
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    is_admin = await user_is_admin(hass, user_input)
    remainder = UndoRemainder.from_undo(undo)
    inverse_scope = confirmed_scope(hass, undo.plans, entities, options)
    names = {entity.entity_id: entity.friendly_name for entity in entities}

    # Consume before the first await that may execute an inverse.  Failed and
    # partial requests are never replayable.
    context_store.clear(user_input.conversation_id)

    # Full read-only preflight before any physical write.
    for plan in remainder.remaining_plans:
        checked = await async_preflight_service_plan(
            hass,
            plan,
            entities_provider(),
            options,
            is_admin=is_admin,
            user_id=current_user_id,
            confirmed=True,
            scope=inverse_scope,
        )
        if checked.error is None:
            continue
        remainder = stop_undo_remainder(remainder, aborted_plan=plan)
        response.async_set_error(
            intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
            partial_undo_failure_text(
                remainder,
                names,
                "Vor Beginn ist die vollständige Vorprüfung fehlgeschlagen: "
                + checked.error,
            ),
        )
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    for plan in undo.plans:
        guard = next_undo_guard(remainder)
        if (
            guard is None
            or guard.plans != (plan,)
            or not undo_matches_observed_states(guard, hass.states.get)
        ):
            remainder = stop_undo_remainder(remainder, aborted_plan=plan)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                partial_undo_failure_text(
                    remainder,
                    names,
                    "Eine fremde Zustandsversion wurde nicht als "
                    "HomeIntent-Evidenz übernommen.",
                    external=True,
                ),
            )
            break
        bound_guard: UndoPlan = guard

        checked = await async_preflight_service_plan(
            hass,
            plan,
            entities_provider(),
            options,
            is_admin=is_admin,
            user_id=current_user_id,
            confirmed=True,
            scope=inverse_scope,
        )
        if checked.error is not None:
            remainder = stop_undo_remainder(remainder, aborted_plan=plan)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                partial_undo_failure_text(remainder, names, checked.error),
            )
            break

        evidence_failure = (
            "Das Ziel wurde unmittelbar vor dem inversen Aufruf verändert."
        )

        def evidence_still_matches() -> str | None:
            return (
                None
                if undo_matches_observed_states(bound_guard, hass.states.get)
                else evidence_failure
            )

        # Home Assistant has no state-version compare-and-swap service API.
        # This synchronous guard is therefore the narrowest possible boundary:
        # no HomeIntent await occurs between it and dispatching the HA call,
        # but HA itself cannot promise physical device atomicity.
        turn = current_turn()
        step_context = new_execution_context(
            current_user_id, turn.context if turn is not None else None
        )
        execution = await async_execute_service_plan(
            hass,
            plan,
            entities_provider(),
            options,
            is_admin=is_admin,
            user_id=current_user_id,
            confirmed=True,
            audit_trail=audit_trail,
            audit_actor_id=current_user_id,
            effect_monitor=effect_monitor,
            context=step_context,
            scope=inverse_scope,
            pre_write_check=evidence_still_matches,
            trace_utterance=user_input.text,
        )
        if not execution.executed or execution.failed:
            if execution.execution_id is not None or execution.executed:
                _LOGGER.error(
                    "Undo service call failed: %s",
                    execution.error
                    or execution.effect_error
                    or "inverse effect was not confirmed",
                )
            remainder = stop_undo_remainder(remainder, aborted_plan=plan)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                partial_undo_failure_text(
                    remainder,
                    names,
                    execution.error
                    or execution.effect_error
                    or "Der inverse Aufruf ist fehlgeschlagen.",
                    external=execution.error == evidence_failure,
                ),
            )
            break

        execution_id = execution.execution_id
        inverse_evidence_plan = UndoPlan(
            (plan,),
            current_user_id,
            source_execution_ids=undo.source_execution_ids,
        )
        observed = (
            bind_undo_to_observed_states(
                inverse_evidence_plan,
                hass.states.get,
                executed=plan,
                execution_id=execution_id,
            )
            if execution_id is not None
            else None
        )
        if observed is None and execution_id is not None:
            monitor_timeout = getattr(effect_monitor, "timeout", None)
            timeout = (
                float(monitor_timeout.total_seconds())
                if monitor_timeout is not None
                else 10.0
            )
            observed = await async_bind_undo_when_observed(
                hass,
                inverse_evidence_plan,
                plan,
                execution_id,
                timeout=min(10.0, max(0.1, timeout)),
            )
        if observed is None or execution_id is None:
            remainder = stop_undo_remainder(remainder, aborted_plan=plan)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                partial_undo_failure_text(
                    remainder,
                    names,
                    "Weitere Schritte wurden ohne aktuelle, kausale "
                    "Wirkungsevidenz gestoppt.",
                    uncertain_plan=plan,
                ),
            )
            break
        remainder = advance_undo_remainder(
            remainder, plan, observed, execution_id
        )
    else:
        response.async_set_speech("Die letzte Aktion wurde rückgängig gemacht.")

    if remainder.remaining_plans:
        # Defensive invariant: every terminal branch consumes the remainder.
        _LOGGER.error("Undo ended with an unconsumed typed remainder")
        remainder = stop_undo_remainder(
            remainder, aborted_plan=remainder.remaining_plans[0]
        )
    return conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )
